from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


class _FakePage:
    def __init__(self, *, url: str, login_ui_visible: bool):
        self.url = url
        self._login_ui_visible = login_ui_visible

    def goto(self, *_args, **_kwargs):
        return None

    def wait_for_timeout(self, *_args, **_kwargs):
        return None

    def evaluate(self, *_args, **_kwargs):
        return self._login_ui_visible


def test_app_base_of_keeps_sub_path_for_hash_router():
    value = "https://panjiachen.github.io/vue-element-admin/#/login?redirect=%2Fdashboard"
    assert crawl_script._app_base_of(value) == "https://panjiachen.github.io/vue-element-admin"


def test_build_url_from_route_with_sub_path_hash_router():
    app_base = "https://panjiachen.github.io/vue-element-admin"
    home_url = "https://panjiachen.github.io/vue-element-admin/#/dashboard"
    built = crawl_script._build_url_from_route(app_base, home_url, "/list")
    assert built == "https://panjiachen.github.io/vue-element-admin/#/list"


def test_build_url_from_route_with_history_router_uses_origin_root():
    home_url = "https://host.example.com/app/dashboard"
    app_base = crawl_script._app_base_of(home_url)
    built = crawl_script._build_url_from_route(app_base, home_url, "/system/user")
    assert built == "https://host.example.com/system/user"


def test_build_url_queue_uses_sub_path_base():
    menu_nodes = [
        {
            "node_type": "page",
            "is_ai_primary_candidate": True,
            "route_path": "/permission/index",
            "target_url": None,
        }
    ]
    home_url = "https://panjiachen.github.io/vue-element-admin/#/dashboard"
    queue = crawl_script._build_url_queue(menu_nodes, max_pages=3, home_url=home_url)
    assert queue == ["https://panjiachen.github.io/vue-element-admin/#/permission/index"]


def test_is_state_valid_marks_login_form_as_invalid_even_without_login_path():
    page = _FakePage(url="https://host.example.com/portal", login_ui_visible=True)
    assert crawl_script._is_state_valid(page, "https://host.example.com/portal", 30_000) is False


def test_is_state_valid_accepts_authenticated_page_without_login_markers():
    page = _FakePage(url="https://host.example.com/portal", login_ui_visible=False)
    assert crawl_script._is_state_valid(page, "https://host.example.com/portal", 30_000) is True
