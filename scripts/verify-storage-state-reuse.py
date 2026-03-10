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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load storage_state/session_storage from DB and verify whether login state is reusable.",
    )
    parser.add_argument("--sys-code", required=True, help="Target system code from web_systems.sys_code")
    parser.add_argument("--target-url", default=None, help="URL to verify state playback. Defaults to system.base_url")
    parser.add_argument(
        "--login-url",
        default=None,
        help="Explicit login URL for redirect check. Defaults to web_systems.login_url",
    )
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="Playwright timeout in milliseconds")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    parser.add_argument("--skip-screenshot", action="store_true", help="Do not capture screenshot")
    return parser.parse_args()


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


def _is_login_url(current_url: str, login_url: str | None) -> bool:
    lowered = current_url.lower()
    if "#/auth/login" in lowered or "/auth/login" in lowered:
        return True

    if login_url:
        expected = login_url.lower()
        if lowered.startswith(expected):
            return True
        # Fallback: compare non-root path or hash fragment.
        expected_parts = urlsplit(expected)
        expected_fragment = (expected_parts.fragment or "").strip("/")
        if expected_fragment and expected_fragment in lowered:
            return True
        expected_path = (expected_parts.path or "").strip()
        if expected_path and expected_path not in {"/", ""} and expected_path in lowered:
            return True

    return False


async def _verify_reuse(
    system: WebSystem,
    state: StorageState,
    *,
    target_url: str,
    login_url: str | None,
    timeout_ms: int,
    headed: bool,
    skip_screenshot: bool,
) -> VerifyResult:
    screenshot_path: str | None = None
    current_url = ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        context = await browser.new_context(storage_state=state.storage_state)
        context.set_default_timeout(timeout_ms)
        page = await context.new_page()
        page.set_default_timeout(timeout_ms)

        try:
            # sessionStorage 需要同源页面中手动回放
            if state.session_storage:
                origin = _extract_origin(target_url)
                await page.goto(origin, wait_until="domcontentloaded", timeout=timeout_ms)
                await _inject_session_storage(page, state.session_storage)

            await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1500)
            current_url = page.url

            login_detected = _is_login_url(current_url, login_url)
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
                target_url=target_url,
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


async def _run(args: argparse.Namespace) -> int:
    await init_db()

    system, state = await _load_system_and_state(args.sys_code)
    if system is None:
        print(
            json.dumps(
                {
                    "sys_code": args.sys_code,
                    "reusable": False,
                    "message": f"System not found or inactive: {args.sys_code}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        await close_db()
        return 1

    if state is None:
        print(
            json.dumps(
                {
                    "sys_code": args.sys_code,
                    "reusable": False,
                    "message": "No valid storage state found in DB.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        await close_db()
        return 1

    target_url = (args.target_url or system.base_url).strip()
    login_url = (args.login_url or system.login_url or "").strip() or None

    result = await _verify_reuse(
        system,
        state,
        target_url=target_url,
        login_url=login_url,
        timeout_ms=args.timeout_ms,
        headed=args.headed,
        skip_screenshot=args.skip_screenshot,
    )

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    await close_db()
    return 0 if result.reusable else 2


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
