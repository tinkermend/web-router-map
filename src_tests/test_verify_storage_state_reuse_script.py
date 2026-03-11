from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify-storage-state-reuse.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("verify_storage_state_reuse", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verify_script = _load_script_module()


def _load_generate_and_run_module():
    generate_script_path = Path(__file__).resolve().parents[1] / "scripts" / "generate_and_run.py"
    spec = importlib.util.spec_from_file_location("generate_and_run_script", generate_script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generate_script = _load_generate_and_run_module()


def _make_result(reusable: bool):
    return verify_script.VerifyResult(
        sys_code="demo",
        reusable=reusable,
        message="ok" if reusable else "failed",
        state_id=str(uuid.uuid4()),
        system_url="https://demo.example.com",
        target_url="https://demo.example.com/#/dashboard",
        current_url="https://demo.example.com/#/dashboard",
        login_detected=not reusable,
        cookies_count=1,
        local_storage_count=1,
        session_storage_count=1,
        screenshot_path=None,
    )


def test_parse_args_accepts_sys_code_mode():
    args = verify_script.parse_args(["--sys-code", "ele.vben.pro"])
    assert args.sys_code == "ele.vben.pro"
    assert args.all_valid is False
    assert args.limit == 20


def test_parse_args_accepts_all_valid_mode():
    args = verify_script.parse_args(["--all-valid", "--limit", "8", "--timeout-ms", "120000"])
    assert args.sys_code is None
    assert args.all_valid is True
    assert args.limit == 8
    assert args.timeout_ms == 120000


def test_parse_args_requires_one_mode():
    with pytest.raises(SystemExit):
        verify_script.parse_args([])


def test_extract_origin():
    assert verify_script._extract_origin("https://demo.example.com/#/dashboard") == "https://demo.example.com"


@pytest.mark.parametrize(
    "current_url,login_url,expected",
    [
        ("https://demo.example.com/#/auth/login", None, True),
        ("https://demo.example.com/login", "https://demo.example.com/login", True),
        ("https://demo.example.com/#/dashboard", "https://demo.example.com/#/auth/login", False),
        ("https://demo.example.com/#/auth/login", "https://demo.example.com/#/signin", True),
        (
            "https://panjiachen.github.io/vue-element-admin/#/dashboard",
            "https://panjiachen.github.io/vue-element-admin/#/",
            False,
        ),
    ],
)
def test_is_login_url(current_url: str, login_url: str | None, expected: bool):
    assert verify_script._is_login_url(current_url, login_url) is expected


def test_build_error_result_uses_counts_from_state():
    state = verify_script.StorageState(
        system_id=uuid.uuid4(),
        storage_state={"cookies": [], "origins": []},
        cookies=[{"name": "sid"}],
        local_storage={"token": "a"},
        session_storage={"jwt": "b"},
    )
    system = verify_script.WebSystem(
        id=uuid.uuid4(),
        sys_code="demo",
        name="Demo",
        base_url="https://demo.example.com",
    )
    target = verify_script.VerifyTarget(
        system=system,
        state=state,
        target_url="https://demo.example.com/#/dashboard",
        login_url="https://demo.example.com/#/auth/login",
    )
    result = verify_script._build_error_result(target, "Verification error: timeout")
    assert result.reusable is False
    assert result.cookies_count == 1
    assert result.local_storage_count == 1
    assert result.session_storage_count == 1


def test_exit_code_for_results():
    assert verify_script._exit_code_for_results([]) == 1
    assert verify_script._exit_code_for_results([_make_result(True), _make_result(True)]) == 0
    assert verify_script._exit_code_for_results([_make_result(True), _make_result(False)]) == 2


def test_generate_script_parse_dialogues_infers_three_intents():
    dialogues = [
        "帮我看一下滑动窗口系统工作台页面中的点击首页是否正常",
        "帮我看一下滑动窗口系统分析夜中当前访问量是多少",
        "帮我看一下滑动窗口系统表单演示页面是否能正常打开",
    ]

    intents = generate_script.parse_dialogues(dialogues)

    assert [item.intent_type for item in intents] == ["click_home", "read_visits", "open_form"]
    assert [item.page_keyword for item in intents] == ["工作台", "分析页", "表单演示"]
    assert all(item.system_keyword == "滑动窗口系统" for item in intents)


def test_generate_script_parse_dialogues_respects_forced_system_keyword():
    intents = generate_script.parse_dialogues(
        ["帮我看一下A系统工作台页面中的点击首页是否正常"],
        forced_system_keyword="覆盖系统",
    )
    assert len(intents) == 1
    assert intents[0].system_keyword == "覆盖系统"


def test_generate_script_parse_dialogues_requires_system_keyword():
    with pytest.raises(ValueError, match="系统名称"):
        generate_script.parse_dialogues(["帮我看一下工作台页面是否正常"])


def test_generate_script_parse_locator_supports_text_role_and_css():
    text_kind = generate_script._parse_locator("get_by_text('提交')")
    role_kind = generate_script._parse_locator("get_by_role('button', name='新增')")
    css_kind = generate_script._parse_locator("#main > .btn")

    assert text_kind == ("get_by_text", "提交", None)
    assert role_kind == ("get_by_role", "button", "新增")
    assert css_kind == ("css", "#main > .btn", None)


def test_generate_script_pick_home_locator_prefers_home_related_descriptions():
    locators = [
        {
            "playwright_locator": "#v-1",
            "text_content": "",
            "nearby_text": "",
            "usage_description": "点击后回到首页",
        },
        {
            "playwright_locator": "#v-2",
            "text_content": "提交",
            "nearby_text": "",
            "usage_description": "提交表单",
        },
    ]
    assert generate_script._pick_home_locator(locators) == "#v-1"


def test_generate_script_normalize_dialogue_text_rewrites_common_typo():
    assert generate_script._normalize_dialogue_text("分析夜") == "分析页"


def test_generate_script_parse_args_uses_fast_defaults():
    args = generate_script.parse_args([])
    assert args.timeout_ms == 8000
    assert args.slow_mo == 50
    assert args.headless is False
