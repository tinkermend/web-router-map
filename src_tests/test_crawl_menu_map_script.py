from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "crawl-menu-map.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("crawl_menu_map", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


crawl_script = _load_script_module()


class _FakeEvaluatePage:
    def __init__(self, result):
        self.result = result
        self.calls: list[dict[str, object]] = []

    def evaluate(self, script, *args, **kwargs):
        self.calls.append({"script": script, "args": args, "kwargs": kwargs})
        return self.result


def test_select_framework_confidence_levels():
    chosen = crawl_script._select_framework({"vue2": 0.2, "vue3": 0.8, "react": 0.1})
    assert chosen["framework_type"] == "vue3"
    assert chosen["confidence_level"] == "certain"

    chosen_unknown = crawl_script._select_framework({"vue2": 0.3, "vue3": 0.2, "react": 0.1})
    assert chosen_unknown["framework_type"] == "unknown"
    assert chosen_unknown["confidence_level"] == "unknown"


def test_select_framework_prefers_hint_when_close():
    chosen = crawl_script._select_framework(
        {"vue2": 0.69, "vue3": 0.74, "react": 0.2},
        framework_hint="vue2",
    )
    assert chosen["framework_type"] == "vue2"
    assert chosen["confidence_level"] == "suspect"


def test_merge_route_datasets_prioritizes_runtime_over_bundle():
    merged = crawl_script._merge_route_datasets(
        [
            {
                "source": "bundle_scripts",
                "routes": [
                    {
                        "route_path": "/analytics",
                        "route_name": "analytics_bundle",
                        "title": "Bundle Analytics",
                        "source": "bundle_scripts",
                    }
                ],
            },
            {
                "source": "vue2_runtime",
                "routes": [
                    {
                        "route_path": "/analytics",
                        "route_name": "analytics_runtime",
                        "title": "Runtime Analytics",
                        "source": "vue2_runtime",
                    }
                ],
            },
        ]
    )
    assert merged["success"] is True
    route = merged["routes"][0]
    assert route["route_name"] == "analytics_runtime"
    assert route["source"] == "vue2_runtime"


def test_normalize_route_records_with_vue2_and_react_fixtures():
    app_base_url = "https://demo.example.com"
    home_url = "https://demo.example.com/#/analytics"

    vue2_routes = crawl_script._normalize_route_records(
        [
            {"path": "system/user", "name": "sys-user", "title": "用户管理"},
            {"path": "system/user", "name": "sys-user", "title": "用户管理"},
        ],
        source="vue2_runtime",
        app_base_url=app_base_url,
        home_url=home_url,
    )
    assert len(vue2_routes) == 1
    assert vue2_routes[0]["route_path"] == "/system/user"

    react_routes = crawl_script._normalize_route_records(
        [
            {"route_path": "/reports/list", "route_name": "reports_list", "title": "报表列表"},
            {"route_path": "/reports/list", "route_name": "reports_list", "title": "报表列表"},
        ],
        source="react_runtime",
        app_base_url=app_base_url,
        home_url=home_url,
    )
    assert len(react_routes) == 1
    assert react_routes[0]["target_url"] == "https://demo.example.com/#/reports/list"


def test_compute_coverage_score_and_failure_categories():
    route_data = {
        "routes": [
            {"route_path": "/analytics"},
            {"route_path": "/system/user"},
        ]
    }
    dom_data = {"nodes": [{"node_id": "1"}, {"node_id": "2"}], "success": True}
    menu_nodes = [
        {"node_type": "page", "route_path": "/analytics", "is_visible": True},
        {"node_type": "page", "route_path": "/system/user", "is_visible": True},
    ]
    coverage = crawl_script._compute_coverage_score(
        route_data=route_data,
        dom_data=dom_data,
        menu_nodes=menu_nodes,
        queued_page_count=2,
        crawled_page_count=2,
    )
    assert coverage["coverage_score"] >= 0.9

    categories = crawl_script._build_failure_categories(
        framework_info={"framework_type": "unknown"},
        route_data={"routes": []},
        dom_data={"nodes": []},
        queued_page_count=3,
        crawled_page_count=1,
        coverage_score=0.2,
    )
    assert categories == [
        "framework_unresolved",
        "menu_extract_failed",
        "page_crawl_partial",
        "payload_low_confidence",
    ]


def test_detect_framework_does_not_forward_timeout_to_evaluate():
    page = _FakeEvaluatePage(
        {
            "scores": {"vue2": 0.1, "vue3": 0.9, "react": 0.1},
            "evidence": {"vue3": ["window.__VUE_ROUTER__.getRoutes"]},
        }
    )

    result = crawl_script._detect_framework(page, framework_hint="auto", timeout_ms=1234)

    assert result["framework_type"] == "vue3"
    assert len(page.calls) == 1
    assert page.calls[0]["kwargs"] == {}


def test_extract_vue3_routes_runtime_does_not_forward_timeout_to_evaluate():
    page = _FakeEvaluatePage(
        {
            "success": True,
            "source": "vue3_runtime",
            "routes": [
                {"route_path": "/system/user", "route_name": "systemUser", "title": "用户管理"}
            ],
            "error": None,
            "probe_chain": ["window.__VUE_ROUTER__"],
        }
    )

    result = crawl_script._extract_vue3_routes_runtime(
        page,
        app_base_url="https://demo.example.com",
        home_url="https://demo.example.com/#/dashboard",
        timeout_ms=1234,
    )

    assert result["success"] is True
    assert result["routes"][0]["target_url"] == "https://demo.example.com/#/system/user"
    assert len(page.calls) == 1
    assert page.calls[0]["kwargs"] == {}


def test_extract_elements_escapes_label_selector_for_dynamic_id():
    page = _FakeEvaluatePage([])

    result = crawl_script._extract_elements(page, None, 20)

    assert result == []
    assert len(page.calls) == 1
    script = str(page.calls[0]["script"])
    assert "cssEscape" in script
    assert "const escapedId = cssEscape(el.id);" in script
    assert "label[for" in script
