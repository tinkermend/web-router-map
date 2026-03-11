from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "crawl-menu-map.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("crawl_menu_map_script", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


crawl_script = _load_script_module()


@pytest.mark.parametrize(
    "hint,expected",
    [
        ("/system/user", "/system/user"),
        ("#/analytics", "/analytics"),
        ("analytics/report", "/analytics/report"),
        ("2-1", None),
        ("https://demo.example.com/#/dashboard", "/dashboard"),
        ("https://demo.example.com/system/menu", "/system/menu"),
    ],
)
def test_route_path_from_menu_hint(hint: str, expected: str | None):
    assert crawl_script._route_path_from_menu_hint(hint) == expected


def test_build_menu_nodes_can_fill_route_path_and_target_url_from_route_hint():
    route_data = {"routes": []}
    dom_data = {
        "nodes": [
            {
                "node_id": "0",
                "parent_id": None,
                "title": "报表",
                "text_breadcrumb": "报表",
                "menu_order": 0,
                "menu_level": 1,
                "path_indexes": [0],
                "node_type": "page",
                "target_url": None,
                "route_path": None,
                "route_hint": "reports/summary",
                "route_name": None,
                "playwright_locator": "get_by_text('报表')",
                "is_group": False,
                "is_external": False,
                "is_visible": True,
                "dom_css_path": "li:nth-of-type(1)",
                "source": "dom",
            }
        ]
    }

    nodes = crawl_script._build_menu_nodes(
        route_data=route_data,
        dom_data=dom_data,
        origin="https://demo.example.com",
        home_url="https://demo.example.com/#/home",
    )

    assert len(nodes) == 1
    assert nodes[0]["route_path"] == "/reports/summary"
    assert nodes[0]["target_url"] == "https://demo.example.com/#/reports/summary"


def test_build_url_queue_derives_url_from_route_path_before_fallback():
    queue = crawl_script._build_url_queue(
        menu_nodes=[
            {
                "node_type": "page",
                "is_ai_primary_candidate": True,
                "route_path": "/analytics",
                "target_url": None,
            }
        ],
        max_pages=10,
        home_url="https://demo.example.com/#/home",
    )
    assert queue == ["https://demo.example.com/#/analytics"]


def test_build_url_queue_falls_back_to_home_url_when_no_navigable_url():
    queue = crawl_script._build_url_queue(
        menu_nodes=[
            {
                "node_type": "page",
                "is_ai_primary_candidate": True,
                "route_path": None,
                "target_url": None,
            }
        ],
        max_pages=10,
        home_url="https://demo.example.com/#/home",
    )
    assert queue == ["https://demo.example.com/#/home"]
