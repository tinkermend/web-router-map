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
