#!/usr/bin/env python3
"""
Login https://ele.vben.pro/#/auth/login and persist Playwright storage state.

Requirements:
- python3
- playwright
- ddddocr
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import ddddocr
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://ele.vben.pro/#/auth/login"
DEFAULT_USERNAME = "vben"
DEFAULT_PASSWORD = "123456"
DEFAULT_STORAGE_STATE_PATH = "output/playwright/ele-storage-state.json"
DEFAULT_AUTH_OUTPUT_PATH = "output/playwright/ele-auth.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Login Vben Ele site, solve slider captcha, save storage state."
    )
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Login username.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Login password.")
    parser.add_argument(
        "--storage-state",
        default=DEFAULT_STORAGE_STATE_PATH,
        help="Output storage state file path.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=60_000,
        help="Playwright timeout in milliseconds.",
    )
    parser.add_argument(
        "--auth-output",
        default=DEFAULT_AUTH_OUTPUT_PATH,
        help="Output file path for extracted Authorization/session/local storage info.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run in headed mode for debugging.",
    )
    return parser.parse_args()


def _estimate_drag_distance(
    slider_track_png: bytes,
    slider_handle_png: bytes,
    track_width: float,
    handle_width: float,
) -> float:
    """
    Use ddddocr to estimate slider distance.
    This page currently uses drag-to-end style slider, so OCR may return x=0.
    In that case we fallback to dragging to the far right.
    """
    max_distance = max(0.0, track_width - handle_width - 2.0)
    ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)

    try:
        result = ocr.slide_match(slider_handle_png, slider_track_png, simple_target=True)
        target_x = 0
        if isinstance(result, dict):
            target_x = int(result.get("target_x") or 0)
            if target_x <= 0 and isinstance(result.get("target"), list):
                target_x = int(result["target"][0] or 0)

        if 5 <= target_x <= max_distance:
            return float(target_x)
    except Exception:
        pass

    return max_distance


def _drag_slider(page, drag_distance: float) -> None:
    slider_handle = page.locator('[name="captcha-action"]').first
    handle_box = slider_handle.bounding_box()
    if not handle_box:
        raise RuntimeError("Cannot find slider handle bounding box.")

    start_x = handle_box["x"] + handle_box["width"] / 2
    start_y = handle_box["y"] + handle_box["height"] / 2
    end_x = start_x + drag_distance

    page.mouse.move(start_x, start_y)
    page.mouse.down()

    steps = max(20, int(abs(drag_distance) / 6))
    for i in range(1, steps + 1):
        progress = i / steps
        eased = progress ** 0.88
        x = start_x + drag_distance * eased + random.uniform(-1.0, 1.0)
        y = start_y + random.uniform(-0.4, 0.4)
        page.mouse.move(x, y)
        page.wait_for_timeout(random.randint(6, 16))

    page.mouse.move(end_x, start_y)
    page.wait_for_timeout(random.randint(40, 120))
    page.mouse.up()


def _solve_slider_captcha(page, timeout_ms: int) -> None:
    slider_hint = page.locator("#v-7-form-item div").nth(1)
    slider_track = page.locator("#v-7-form-item")
    slider_handle = page.locator('[name="captcha-action"]').first

    slider_hint.wait_for(state="visible", timeout=timeout_ms)
    slider_handle.wait_for(state="visible", timeout=timeout_ms)

    for attempt in range(1, 4):
        track_box = slider_track.bounding_box()
        handle_box = slider_handle.bounding_box()
        if not track_box or not handle_box:
            raise RuntimeError("Cannot resolve slider bounding boxes.")

        track_png = slider_track.screenshot()
        handle_png = slider_handle.screenshot()

        drag_distance = _estimate_drag_distance(
            slider_track_png=track_png,
            slider_handle_png=handle_png,
            track_width=track_box["width"],
            handle_width=handle_box["width"],
        )
        _drag_slider(page, drag_distance)
        page.wait_for_timeout(800)

        slider_text = slider_hint.inner_text().strip()
        if "验证通过" in slider_text:
            return

    raise RuntimeError("Slider captcha verification failed after 3 attempts.")


def _wait_login_success(page, timeout_ms: int) -> None:
    try:
        page.wait_for_url("**/analytics", timeout=timeout_ms)
        return
    except PlaywrightTimeoutError:
        pass

    try:
        page.wait_for_url(lambda url: "#/auth/login" not in url, timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("Login did not redirect away from login page.") from exc


def _read_web_storage(page, storage_name: str) -> dict[str, str]:
    if storage_name not in {"localStorage", "sessionStorage"}:
        raise ValueError("storage_name must be localStorage or sessionStorage")
    return page.evaluate(
        """(storageName) => {
            const storage = window[storageName];
            const out = {};
            for (let i = 0; i < storage.length; i += 1) {
                const key = storage.key(i);
                if (key) out[key] = storage.getItem(key) || "";
            }
            return out;
        }""",
        storage_name,
    )


def main() -> None:
    args = parse_args()
    storage_state_path = Path(args.storage_state).resolve()
    auth_output_path = Path(args.auth_output).resolve()
    storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    auth_output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context()
        context.set_default_timeout(args.timeout_ms)
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)
        auth_header_capture: dict[str, str | None] = {"authorization": None}

        def _on_request(request) -> None:
            if auth_header_capture["authorization"]:
                return
            auth = request.headers.get("authorization")
            if auth:
                auth_header_capture["authorization"] = auth

        page.on("request", _on_request)

        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=args.timeout_ms)

            # Required locators from user input.
            page.get_by_role("textbox", name="请输入用户名").fill(args.username)
            page.get_by_role("textbox", name="密码").fill(args.password)

            _solve_slider_captcha(page, args.timeout_ms)

            page.get_by_role("button", name="login").click()
            _wait_login_success(page, args.timeout_ms)
            page.wait_for_timeout(1500)

            context.storage_state(path=str(storage_state_path))
            local_storage = _read_web_storage(page, "localStorage")
            session_storage = _read_web_storage(page, "sessionStorage")
            auth_payload = {
                "base_url": LOGIN_URL,
                "current_url": page.url,
                "authorization": auth_header_capture["authorization"],
                "local_storage": local_storage,
                "session_storage": session_storage,
            }
            auth_output_path.write_text(
                json.dumps(auth_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            print(f"StorageState saved: {storage_state_path}")
            print(f"Auth info saved: {auth_output_path}")
            if auth_header_capture["authorization"]:
                print("Authorization header captured: yes")
            else:
                print("Authorization header captured: no")
            print(f"Current URL: {page.url}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
