#!/usr/bin/env python3
"""Validate whether DB-persisted storage state/session data is reusable."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright
from sqlmodel import desc, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.database import close_db, init_db, session_scope
from src.models.storage_state import StorageState
from src.models.web_system import WebSystem

DEFAULT_SCREENSHOT_DIR = "output/playwright/screenshots/state-reuse"
LOGIN_MARKERS = (
    "#/auth/login",
    "/auth/login",
    "#/login",
    "/login",
    "#/signin",
    "/signin",
)


@dataclass(slots=True)
class VerifyTarget:
    system: WebSystem
    state: StorageState
    target_url: str
    login_url: str | None


@dataclass(slots=True)
class VerifyResult:
    sys_code: str
    reusable: bool
    message: str
    state_id: str | None
    system_url: str
    target_url: str
    current_url: str
    login_detected: bool
    cookies_count: int
    local_storage_count: int
    session_storage_count: int
    screenshot_path: str | None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load storage_state/session_storage from DB and verify whether login state is reusable.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sys-code", help="Target system code from web_systems.sys_code")
    mode.add_argument(
        "--all-valid",
        action="store_true",
        help="Verify latest valid state (storage_states.is_valid=true) for each active system",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max systems to verify when --all-valid is set")
    parser.add_argument("--target-url", default=None, help="URL to verify state playback. Defaults to system.base_url")
    parser.add_argument(
        "--login-url",
        default=None,
        help="Explicit login URL for redirect check. Defaults to web_systems.login_url",
    )
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="Playwright timeout in milliseconds")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    parser.add_argument("--skip-screenshot", action="store_true", help="Do not capture screenshot")
    return parser.parse_args(argv)


async def _load_system_and_state(sys_code: str) -> tuple[WebSystem | None, StorageState | None]:
    async with session_scope() as session:
        system_stmt = select(WebSystem).where(WebSystem.sys_code == sys_code, WebSystem.is_active.is_(True))
        system = (await session.exec(system_stmt)).first()
        if system is None:
            return None, None

        state: StorageState | None = None
        if system.latest_valid_state_id:
            state = await session.get(StorageState, system.latest_valid_state_id)
            if state and not state.is_valid:
                state = None

        if state is None:
            state_stmt = (
                select(StorageState)
                .where(StorageState.system_id == system.id, StorageState.is_valid.is_(True))
                .order_by(desc(StorageState.validated_at), desc(StorageState.created_at))
            )
            state = (await session.exec(state_stmt)).first()

        return system, state


async def _load_all_valid_targets(
    *,
    limit: int,
    target_url: str | None,
    login_url: str | None,
) -> list[VerifyTarget]:
    query_limit = max(limit, 1)
    async with session_scope() as session:
        stmt = (
            select(StorageState, WebSystem)
            .join(WebSystem, StorageState.system_id == WebSystem.id)
            .where(StorageState.is_valid.is_(True), WebSystem.is_active.is_(True))
            .order_by(WebSystem.sys_code, desc(StorageState.validated_at), desc(StorageState.created_at))
        )
        rows = (await session.exec(stmt)).all()

    picked: dict[str, VerifyTarget] = {}
    for state, system in rows:
        if system.sys_code in picked:
            continue
        picked[system.sys_code] = VerifyTarget(
            system=system,
            state=state,
            target_url=(target_url or system.base_url).strip(),
            login_url=(login_url or system.login_url or "").strip() or None,
        )
        if len(picked) >= query_limit:
            break
    return list(picked.values())


async def _inject_session_storage(page, session_storage: dict[str, str]) -> None:
    await page.evaluate(
        """(data) => {
            for (const [key, value] of Object.entries(data || {})) {
                window.sessionStorage.setItem(key, String(value));
            }
        }""",
        session_storage,
    )


def _extract_origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _has_login_marker(value: str) -> bool:
    lowered = (value or "").lower()
    return any(marker in lowered for marker in LOGIN_MARKERS)


def _is_login_url(current_url: str, login_url: str | None) -> bool:
    lowered = current_url.lower()
    if _has_login_marker(lowered):
        return True

    if login_url:
        expected = login_url.lower().strip()
        if not expected:
            return False
        expected_parts = urlsplit(expected)
        expected_path = (expected_parts.path or "").strip("/")
        expected_fragment = (expected_parts.fragment or "").split("?", maxsplit=1)[0].strip("/")
        path_has_login = _has_login_marker(expected_path) or expected_path.endswith(("login", "signin"))
        fragment_has_login = _has_login_marker(expected_fragment) or expected_fragment.endswith(("login", "signin"))
        url_has_login = _has_login_marker(expected) or path_has_login or fragment_has_login

        normalized_current = lowered.rstrip("/")
        normalized_expected = expected.rstrip("/")
        if normalized_expected and url_has_login and normalized_current.startswith(normalized_expected):
            return True
        if expected_fragment and fragment_has_login and expected_fragment in lowered:
            return True
        if expected_path and path_has_login and f"/{expected_path}" in lowered:
            return True

    return False


def _build_error_result(target: VerifyTarget, message: str) -> VerifyResult:
    return VerifyResult(
        sys_code=target.system.sys_code,
        reusable=False,
        message=message,
        state_id=str(target.state.id),
        system_url=target.system.base_url,
        target_url=target.target_url,
        current_url="",
        login_detected=False,
        cookies_count=len(target.state.cookies or []),
        local_storage_count=len(target.state.local_storage or {}),
        session_storage_count=len(target.state.session_storage or {}),
        screenshot_path=None,
    )


def _exit_code_for_results(results: list[VerifyResult]) -> int:
    if not results:
        return 1
    return 0 if all(item.reusable for item in results) else 2


async def _verify_reuse(
    target: VerifyTarget,
    *,
    timeout_ms: int,
    headed: bool,
    skip_screenshot: bool,
) -> VerifyResult:
    system = target.system
    state = target.state
    screenshot_path: str | None = None
    current_url = ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        context = await browser.new_context(storage_state=state.storage_state)
        context.set_default_timeout(timeout_ms)
        page = await context.new_page()
        page.set_default_timeout(timeout_ms)

        try:
            # sessionStorage requires same-origin replay before the target page opens.
            if state.session_storage:
                origin = _extract_origin(target.target_url)
                await page.goto(origin, wait_until="domcontentloaded", timeout=timeout_ms)
                await _inject_session_storage(page, state.session_storage)

            await page.goto(target.target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1500)
            current_url = page.url

            login_detected = _is_login_url(current_url, target.login_url)
            reusable = not login_detected
            message = "Storage state can be reused." if reusable else "Redirected to login page; state is not reusable."

            if not skip_screenshot:
                screenshot_dir = Path(DEFAULT_SCREENSHOT_DIR).resolve()
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_file = screenshot_dir / f"{system.sys_code}-state-reuse.png"
                await page.screenshot(path=str(screenshot_file), full_page=True)
                screenshot_path = str(screenshot_file)

            return VerifyResult(
                sys_code=system.sys_code,
                reusable=reusable,
                message=message,
                state_id=str(state.id),
                system_url=system.base_url,
                target_url=target.target_url,
                current_url=current_url,
                login_detected=login_detected,
                cookies_count=len(state.cookies or []),
                local_storage_count=len(state.local_storage or {}),
                session_storage_count=len(state.session_storage or {}),
                screenshot_path=screenshot_path,
            )
        finally:
            await context.close()
            await browser.close()


async def _resolve_targets(args: argparse.Namespace) -> tuple[list[VerifyTarget], str | None]:
    if args.all_valid:
        targets = await _load_all_valid_targets(limit=args.limit, target_url=args.target_url, login_url=args.login_url)
        if not targets:
            return [], "No active system with valid storage state found in DB."
        return targets, None

    assert args.sys_code
    system, state = await _load_system_and_state(args.sys_code)
    if system is None:
        return [], f"System not found or inactive: {args.sys_code}"
    if state is None:
        return [], "No valid storage state found in DB."

    return [
        VerifyTarget(
            system=system,
            state=state,
            target_url=(args.target_url or system.base_url).strip(),
            login_url=(args.login_url or system.login_url or "").strip() or None,
        )
    ], None


async def _run(args: argparse.Namespace) -> int:
    await init_db()
    try:
        targets, err = await _resolve_targets(args)
        if err:
            payload = {
                "sys_code": args.sys_code or "*",
                "reusable": False,
                "message": err,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1

        results: list[VerifyResult] = []
        for target in targets:
            try:
                result = await _verify_reuse(
                    target,
                    timeout_ms=args.timeout_ms,
                    headed=args.headed,
                    skip_screenshot=args.skip_screenshot,
                )
            except Exception as exc:  # pragma: no cover - runtime flow
                result = _build_error_result(target, f"Verification error: {exc}")
            results.append(result)

        if args.sys_code and not args.all_valid and results:
            print(json.dumps(asdict(results[0]), ensure_ascii=False, indent=2))
            return _exit_code_for_results(results)

        payload = {
            "mode": "all_valid",
            "total": len(results),
            "reusable_count": sum(1 for item in results if item.reusable),
            "failed_count": sum(1 for item in results if not item.reusable),
            "results": [asdict(item) for item in results],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return _exit_code_for_results(results)
    finally:
        await close_db()


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
