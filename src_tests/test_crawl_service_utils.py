from datetime import datetime
from types import SimpleNamespace

from src.services.crawl_service import (
    CrawlService,
    _build_payload_fingerprint,
    _build_page_summary,
    _extract_page_keywords,
    _extract_framework_detected,
    _infer_usage_description,
    _normalize_ltree_path,
    _normalize_authorization_value,
    _normalize_extractor_chain,
    _normalize_failure_categories,
    _parse_dt,
    _safe_str,
    _to_bool,
    _to_float,
    _to_int,
    _validate_payload_before_overwrite,
)
from src.services.crypto_service import CryptoService


def test_to_int_and_to_bool():
    assert _to_int("12") == 12
    assert _to_int(None) is None
    assert _to_int("abc") is None
    assert _to_float("12.5") == 12.5
    assert _to_float("bad") is None

    assert _to_bool(True) is True
    assert _to_bool("yes") is True
    assert _to_bool("0") is False
    assert _to_bool(None) is None


def test_safe_str_and_parse_dt():
    assert _safe_str("  abc  ", 10) == "abc"
    assert _safe_str("", 10) is None
    assert _safe_str(None, 10) is None

    dt = _parse_dt("2026-03-11T00:00:00Z")
    assert isinstance(dt, datetime)
    assert _parse_dt("bad") is None


def test_route_path_extractor():
    assert CrawlService._route_path_from_url_pattern("https://a.com/#/analytics") == "/analytics"
    assert CrawlService._route_path_from_url_pattern("/system/menu") == "/system/menu"
    assert CrawlService._route_path_from_url_pattern("https://a.com/system/menu") is None


def test_default_home_url_for_hash_router_keeps_hash_root():
    system = SimpleNamespace(base_url="https://demo.gin-vue-admin.com/#/")
    assert CrawlService._default_home_url(system) == "https://demo.gin-vue-admin.com/#/"


def test_default_home_url_for_plain_origin_appends_default_path():
    system = SimpleNamespace(base_url="https://ele.vben.pro")
    assert CrawlService._default_home_url(system) == "https://ele.vben.pro/#/analytics"


def test_ltree_path_normalization():
    node_with_indexes = {"path_indexes": [0, 2, 1]}
    assert _normalize_ltree_path(node_with_indexes, default_index=99) == "root.n_0.n_2.n_1"

    node_with_raw_path = {"node_path": "Root.Menu-Item.9Box"}
    assert _normalize_ltree_path(node_with_raw_path, default_index=1) == "root.menu_item.n_9box"


def test_page_summary_keywords_and_usage_description():
    payload = {
        "page_title": "用户管理",
        "url_pattern": "/system/user",
        "elements": [
            {"element_type": "action_btn", "text_content": "新增用户", "nearby_text": ""},
            {"element_type": "action_btn", "text_content": "批量导入", "nearby_text": ""},
            {"element_type": "form_input", "text_content": "", "nearby_text": "用户名"},
            {"element_type": "nav_link", "text_content": "角色权限", "nearby_text": ""},
        ],
    }
    summary = _build_page_summary(payload)
    assert summary is not None
    assert "页面标题：用户管理" in summary
    assert "关键操作：新增用户, 批量导入" in summary
    assert "主要字段：用户名" in summary

    keywords = _extract_page_keywords(payload)
    assert keywords is not None
    assert "用户管理" in keywords
    assert "新增用户" in keywords
    assert "用户名" in keywords

    btn_desc = _infer_usage_description({"element_type": "action_btn", "text_content": "保存"})
    assert btn_desc == "点击按钮“保存”触发对应业务操作。"

    input_desc = _infer_usage_description(
        {
            "element_type": "form_input",
            "nearby_text": "手机号",
            "locators": {"attributes": {"placeholder": "请输入手机号"}},
        }
    )
    assert input_desc == "用于录入“手机号”相关信息。"


def test_validate_payload_before_overwrite():
    menus, pages, meta = _validate_payload_before_overwrite(
        {
            "menus": [{"node_id": "1"}],
            "pages": [],
            "meta": {"state_valid": True},
        }
    )
    assert len(menus) == 1
    assert pages == []
    assert meta == {"state_valid": True}

    try:
        _validate_payload_before_overwrite({"menus": [], "pages": []})
        assert False, "expected RuntimeError for empty payload"
    except RuntimeError:
        pass


def test_meta_normalization_helpers():
    meta = {
        "framework_detection": {"framework_type": "vue3"},
        "route_extraction": {"extractor_chain": ["vue3_runtime", "bundle_scripts", None, ""]},
        "failure_categories": ["payload_low_confidence", "PAGE_CRAWL_PARTIAL", "payload_low_confidence"],
    }
    assert _extract_framework_detected(meta) == "vue3"
    assert _normalize_extractor_chain(meta["route_extraction"]) == ["vue3_runtime", "bundle_scripts"]
    assert _normalize_failure_categories(meta["failure_categories"]) == [
        "payload_low_confidence",
        "page_crawl_partial",
    ]


def test_normalize_authorization_value_and_resolve_state_authorization():
    assert _normalize_authorization_value("token-1", "Bearer") == "Bearer token-1"
    assert _normalize_authorization_value("Bearer token-1", "Bearer") == "Bearer token-1"
    assert _normalize_authorization_value("token-1", None) == "token-1"

    service = object.__new__(CrawlService)
    service.crypto = CryptoService("test-crawl-crypto-key")
    encrypted = service.crypto.encrypt("token-2")
    state = SimpleNamespace(authorization_value=encrypted, authorization_schema="Bearer")
    assert service._resolve_state_authorization(state) == "Bearer token-2"


def test_build_payload_fingerprint_ignores_order_and_volatile_fields():
    payload_a = {
        "meta": {"state_valid": True, "crawled_at": "2026-03-11T13:25:00Z"},
        "menus": [
            {
                "title": "分析页",
                "menu_order": 0,
                "menu_level": 1,
                "route_path": "/analytics",
                "target_url": "https://a.com/#/analytics",
                "path_indexes": [0],
            },
            {
                "title": "系统管理",
                "menu_order": 1,
                "menu_level": 1,
                "route_path": "/system",
                "target_url": "https://a.com/#/system",
                "path_indexes": [1],
            },
        ],
        "pages": [
            {
                "url_pattern": "/analytics",
                "target_url": "https://a.com/#/analytics",
                "page_title": "分析",
                "screenshot_path": "a/1.png",
                "crawled_at": "2026-03-11T13:25:00Z",
                "elements": [
                    {
                        "tag_name": "a",
                        "element_type": "nav_link",
                        "dom_css_path": "ul > li:nth-of-type(1) > a",
                        "locator_tier": "text",
                    },
                ],
            }
        ],
    }
    payload_b = {
        "meta": {"state_valid": True, "crawled_at": "2026-03-11T13:26:00Z"},
        "menus": list(reversed(payload_a["menus"])),
        "pages": [
            {
                "url_pattern": "/analytics",
                "target_url": "https://a.com/#/analytics",
                "page_title": "分析",
                "screenshot_path": "a/2.png",
                "crawled_at": "2026-03-11T13:26:00Z",
                "elements": [
                    {
                        "tag_name": "a",
                        "element_type": "nav_link",
                        "dom_css_path": "ul > li:nth-of-type(9) > a",
                        "locator_tier": "text",
                    },
                ],
            }
        ],
    }

    assert _build_payload_fingerprint(payload_a) == _build_payload_fingerprint(payload_b)


def test_build_payload_fingerprint_detects_structural_change():
    payload_a = {
        "menus": [{"title": "分析页", "route_path": "/analytics", "target_url": "https://a.com/#/analytics"}],
        "pages": [
            {
                "url_pattern": "/analytics",
                "target_url": "https://a.com/#/analytics",
                "elements": [{"tag_name": "button", "element_type": "action_btn", "dom_css_path": "div > button"}],
            }
        ],
    }
    payload_b = {
        "menus": [{"title": "分析页", "route_path": "/analytics-v2", "target_url": "https://a.com/#/analytics-v2"}],
        "pages": [
            {
                "url_pattern": "/analytics-v2",
                "target_url": "https://a.com/#/analytics-v2",
                "elements": [{"tag_name": "button", "element_type": "action_btn", "dom_css_path": "div > button"}],
            }
        ],
    }

    assert _build_payload_fingerprint(payload_a) != _build_payload_fingerprint(payload_b)


def test_build_payload_fingerprint_detects_container_change():
    payload_a = {
        "menus": [{"title": "分析页", "route_path": "/analytics", "target_url": "https://a.com/#/analytics"}],
        "pages": [
            {
                "url_pattern": "/analytics",
                "target_url": "https://a.com/#/analytics",
                "containers": [{"container_type": "modal", "css_selector": ".modal-a"}],
                "elements": [{"tag_name": "button", "element_type": "action_btn", "dom_css_path": ".modal-a button"}],
            }
        ],
    }
    payload_b = {
        "menus": [{"title": "分析页", "route_path": "/analytics", "target_url": "https://a.com/#/analytics"}],
        "pages": [
            {
                "url_pattern": "/analytics",
                "target_url": "https://a.com/#/analytics",
                "containers": [{"container_type": "modal", "css_selector": ".modal-b"}],
                "elements": [{"tag_name": "button", "element_type": "action_btn", "dom_css_path": ".modal-a button"}],
            }
        ],
    }

    assert _build_payload_fingerprint(payload_a) != _build_payload_fingerprint(payload_b)
