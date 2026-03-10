from datetime import datetime

from src.services.crawl_service import CrawlService, _parse_dt, _safe_str, _to_bool, _to_int


def test_to_int_and_to_bool():
    assert _to_int("12") == 12
    assert _to_int(None) is None
    assert _to_int("abc") is None

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
