"""Playwright-based login crawler for storage state capture."""

from __future__ import annotations

import random
from dataclasses import dataclass
from urllib.parse import urlsplit

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

try:
    import ddddocr
except Exception:  # pragma: no cover - optional dependency
    ddddocr = None

AUTH_HEADER_KEYS = {
    "authorization",
    "cookie",
    "x-csrf-token",
    "x-xsrf-token",
    "x-auth-token",
    "token",
    "access-token",
}

TOKEN_KEYS = (
    "token",
    "access_token",
    "accessToken",
    "auth_token",
    "authorization",
    "skillsflow:access_token",
)


@dataclass(slots=True)
class AuthCapture:
    """Captured browser auth payload."""

    base_url: str
    current_url: str
    storage_state: dict
    cookies: list[dict]
    local_storage: dict[str, str]
    session_storage: dict[str, str]
    request_headers: dict[str, str]
    authorization: str | None


class AuthCrawler:
    """Reusable crawler to login and capture auth artifacts."""

    async def login_and_capture(
        self,
        *,
        login_url: str,
        username: str,
        password: str,
        login_auth: str,
        login_selectors: dict,
        timeout_ms: int,
        headed: bool,
        slow_mo: int,
    ) -> AuthCapture:
        expected_origin = _get_origin(login_url)
        request_capture: dict[str, dict[str, str] | str | None] = {
            "authorization": None,
            "request_headers": None,
        }

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not headed, slow_mo=slow_mo)
            context = await browser.new_context()
            context.set_default_timeout(timeout_ms)
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            def on_request(request) -> None:
                if _get_origin(request.url) != expected_origin:
                    return

                headers = _normalize_request_headers(dict(request.headers))
                if not headers:
                    return

                is_auth_related = _is_auth_related_request(headers)
                is_api_like = request.resource_type in {"xhr", "fetch"}
                if is_auth_related:
                    request_capture["request_headers"] = headers
                    request_capture["authorization"] = headers.get("authorization")
                    return
                if request_capture["request_headers"] is None and is_api_like:
                    request_capture["request_headers"] = headers
                    request_capture["authorization"] = headers.get("authorization")

            page.on("request", on_request)

            try:
                await page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await _fill_username_password(page, login_selectors, username, password)
                if login_auth in {"captcha_slider", "captcha_click"}:
                    await _solve_slider_captcha(page, timeout_ms)
                await _click_submit(page, login_selectors)
                await _wait_login_success(page, timeout_ms)
                await page.wait_for_timeout(1500)

                storage_state = await context.storage_state()
                cookies = storage_state.get("cookies", []) if isinstance(storage_state, dict) else []
                local_storage = await _read_web_storage(page, "localStorage")
                session_storage = await _read_web_storage(page, "sessionStorage")

                return AuthCapture(
                    base_url=login_url,
                    current_url=page.url,
                    storage_state=storage_state,
                    cookies=cookies,
                    local_storage=local_storage,
                    session_storage=session_storage,
                    request_headers=request_capture["request_headers"] or {},
                    authorization=request_capture["authorization"],
                )
            finally:
                await browser.close()


async def _fill_username_password(
    page: Page,
    selectors: dict,
    username: str,
    password: str,
) -> None:
    user_selector = str(selectors.get("username") or "").strip()
    pass_selector = str(selectors.get("password") or "").strip()

    if user_selector:
        await page.locator(user_selector).first.fill(username)
    else:
        await page.get_by_role("textbox", name="请输入用户名").fill(username)

    if pass_selector:
        await page.locator(pass_selector).first.fill(password)
    else:
        await page.get_by_role("textbox", name="密码").fill(password)


async def _click_submit(page: Page, selectors: dict) -> None:
    submit_selector = str(selectors.get("submit") or "").strip()
    if submit_selector:
        await page.locator(submit_selector).first.click()
    else:
        await page.get_by_role("button", name="login").click()


async def _read_web_storage(page: Page, storage_name: str) -> dict[str, str]:
    if storage_name not in {"localStorage", "sessionStorage"}:
        raise ValueError("storage_name must be localStorage or sessionStorage")
    return await page.evaluate(
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


async def _wait_login_success(page: Page, timeout_ms: int) -> None:
    try:
        await page.wait_for_url("**/analytics", timeout=timeout_ms)
        return
    except PlaywrightTimeoutError:
        pass

    try:
        await page.wait_for_url(lambda url: "#/auth/login" not in url, timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("Login did not redirect away from login page.") from exc


def _estimate_drag_distance(
    slider_track_png: bytes,
    slider_handle_png: bytes,
    track_width: float,
    handle_width: float,
) -> float:
    max_distance = max(0.0, track_width - handle_width - 2.0)
    if ddddocr is None:
        return max_distance

    try:
        ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
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


async def _drag_slider(page: Page, drag_distance: float) -> None:
    slider_handle = page.locator('[name="captcha-action"]').first
    handle_box = await slider_handle.bounding_box()
    if not handle_box:
        raise RuntimeError("Cannot find slider handle bounding box.")

    start_x = handle_box["x"] + handle_box["width"] / 2
    start_y = handle_box["y"] + handle_box["height"] / 2
    end_x = start_x + drag_distance

    await page.mouse.move(start_x, start_y)
    await page.mouse.down()

    steps = max(20, int(abs(drag_distance) / 6))
    for i in range(1, steps + 1):
        progress = i / steps
        eased = progress**0.88
        x = start_x + drag_distance * eased + random.uniform(-1.0, 1.0)
        y = start_y + random.uniform(-0.4, 0.4)
        await page.mouse.move(x, y)
        await page.wait_for_timeout(random.randint(6, 16))

    await page.mouse.move(end_x, start_y)
    await page.wait_for_timeout(random.randint(40, 120))
    await page.mouse.up()


async def _solve_slider_captcha(page: Page, timeout_ms: int) -> None:
    slider_hint = page.locator("#v-7-form-item div").nth(1)
    slider_track = page.locator("#v-7-form-item")
    slider_handle = page.locator('[name="captcha-action"]').first

    await slider_hint.wait_for(state="visible", timeout=timeout_ms)
    await slider_handle.wait_for(state="visible", timeout=timeout_ms)

    for _attempt in range(1, 4):
        track_box = await slider_track.bounding_box()
        handle_box = await slider_handle.bounding_box()
        if not track_box or not handle_box:
            raise RuntimeError("Cannot resolve slider bounding boxes.")

        track_png = await slider_track.screenshot()
        handle_png = await slider_handle.screenshot()
        drag_distance = _estimate_drag_distance(
            slider_track_png=track_png,
            slider_handle_png=handle_png,
            track_width=track_box["width"],
            handle_width=handle_box["width"],
        )
        await _drag_slider(page, drag_distance)
        await page.wait_for_timeout(800)

        slider_text = (await slider_hint.inner_text()).strip()
        if "验证通过" in slider_text:
            return

    raise RuntimeError("Slider captcha verification failed after 3 attempts.")


def _get_origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _normalize_request_headers(headers: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if value:
            normalized[key.lower()] = value
    return normalized


def _is_auth_related_request(headers: dict[str, str]) -> bool:
    return any(key in headers for key in AUTH_HEADER_KEYS)
