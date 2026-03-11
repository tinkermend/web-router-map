"""Playwright-based login crawler for storage state capture."""

from __future__ import annotations

import io
import random
import re
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import urlsplit

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

try:
    import ddddocr
except Exception as exc:  # pragma: no cover - optional dependency
    ddddocr = None
    _DDDDOCR_IMPORT_ERROR = repr(exc)
else:
    _DDDDOCR_IMPORT_ERROR = None

try:  # pragma: no cover - optional dependency
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None

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
COOKIE_KEY_HINTS = (
    "session",
    "sessionid",
    "jsessionid",
    "token",
    "auth",
    "access_token",
)
LOGIN_LOCATION_TOKENS = (
    "login",
    "signin",
    "sign-in",
    "auth/login",
    "auth/signin",
)
LOGIN_LOCATION_RE = re.compile(r"(^|[^a-z0-9])(login|signin|sign-in)([^a-z0-9]|$)")

SUPPORTED_LOGIN_AUTH_TYPES = {
    "captcha_slider",
    "captcha_image",
    "captcha_click",
    "captcha_sms",
    "sso",
    "none",
}

UNIMPLEMENTED_LOGIN_AUTH_TYPES = {"captcha_sms", "sso"}
CLICK_TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
CLICK_SPLIT_RE = re.compile(r"[\s,，、;；|]+")
SLIDER_SUCCESS_TEXT_RE = re.compile(r"(验证通过|verification\s*passed|captcha\s*passed|success)", re.IGNORECASE)
CLICK_PROMPT_PREFIXES = tuple(
    sorted(
        {
            "请按顺序点击",
            "按顺序点击",
            "请依次点击",
            "依次点击",
            "请点击",
            "请按",
            "请在",
            "请依次",
            "点击",
            "依次",
            "请",
        },
        key=len,
        reverse=True,
    )
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
                await _solve_login_challenge(page, login_auth, login_selectors, timeout_ms)
                await _click_submit(page, login_selectors)
                await _wait_login_success(page, timeout_ms, login_url=login_url)
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


def _build_login_wait_payload(login_url: str) -> dict[str, Any]:
    normalized_login_url = str(login_url or "").strip().lower()
    parsed = urlsplit(normalized_login_url)
    fragment = (parsed.fragment or "").split("?", maxsplit=1)[0].strip().strip("/")
    path = (parsed.path or "").strip().rstrip("/")
    path_is_login = _looks_like_login_location(path)
    fragment_is_login = _looks_like_login_location(fragment)
    login_url_prefix_enabled = path_is_login or fragment_is_login
    return {
        "expected_origin": _get_origin(login_url).lower(),
        "login_url": normalized_login_url,
        "login_path": path,
        "login_fragment": fragment,
        "login_path_is_login": path_is_login,
        "login_fragment_is_login": fragment_is_login,
        "login_url_prefix_enabled": login_url_prefix_enabled,
        "token_keys": [key.lower() for key in TOKEN_KEYS],
        "cookie_hints": [key.lower() for key in COOKIE_KEY_HINTS],
    }


async def _wait_login_success(page: Page, timeout_ms: int, *, login_url: str) -> None:
    wait_script = """(args) => {
        const href = String(window.location.href || "").toLowerCase();
        const origin = String(window.location.origin || "").toLowerCase();
        const expectedOrigin = String(args.expected_origin || "").toLowerCase();
        const loginUrl = String(args.login_url || "").toLowerCase();
        const loginPath = String(args.login_path || "").toLowerCase();
        const loginFragment = String(args.login_fragment || "").toLowerCase();
        const loginPathIsLogin = Boolean(args.login_path_is_login);
        const loginFragmentIsLogin = Boolean(args.login_fragment_is_login);
        const loginUrlPrefixEnabled = Boolean(args.login_url_prefix_enabled);

        const sameOrigin = !expectedOrigin || origin === expectedOrigin;
        const genericLoginPattern = /(^|[#/?&])login([/?&#]|$)/i;
        const genericSigninPattern = /(^|[#/?&])sign[-_]?in([/?&#]|$)/i;
        const stillLoginPage =
            (loginUrlPrefixEnabled && loginUrl && href.startsWith(loginUrl)) ||
            (loginFragmentIsLogin && loginFragment && href.includes(loginFragment)) ||
            (loginPathIsLogin && loginPath && loginPath !== "/" && href.includes(loginPath)) ||
            href.includes("#/auth/login") ||
            href.includes("/auth/login") ||
            genericLoginPattern.test(href) ||
            genericSigninPattern.test(href);

        const awayFromLogin = sameOrigin && !stillLoginPage;
        return awayFromLogin;
    }"""
    payload = _build_login_wait_payload(login_url)
    try:
        await page.wait_for_function(wait_script, arg=payload, timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            f"Login did not reach authenticated state within timeout. current_url={page.url}, login_url={login_url}"
        ) from exc


def _looks_like_login_location(value: str) -> bool:
    lowered = str(value or "").strip().lower().strip("/")
    if not lowered:
        return False
    if any(token in lowered for token in ("auth/login", "auth/signin")):
        return True
    if LOGIN_LOCATION_RE.search(lowered):
        return True
    return any(lowered == token for token in LOGIN_LOCATION_TOKENS)


async def _solve_login_challenge(
    page: Page,
    login_auth: str,
    selectors: dict,
    timeout_ms: int,
) -> None:
    auth_type = str(login_auth or "none").strip().lower()
    if not auth_type or auth_type == "none":
        return

    if auth_type not in SUPPORTED_LOGIN_AUTH_TYPES:
        raise RuntimeError(f"Unsupported login_auth type: {auth_type}")

    if auth_type in UNIMPLEMENTED_LOGIN_AUTH_TYPES:
        raise RuntimeError(f"login_auth {auth_type} is not implemented yet")

    if auth_type == "captcha_slider":
        await _solve_slider_captcha(page, timeout_ms, selectors)
        return
    if auth_type == "captcha_image":
        await _solve_image_captcha(page, timeout_ms, selectors)
        return
    if auth_type == "captcha_click":
        await _solve_click_captcha(page, timeout_ms, selectors)
        return

    raise RuntimeError(f"Unsupported login_auth type: {auth_type}")


def _require_ddddocr(login_auth: str):
    if ddddocr is None:
        detail = f" (import_error={_DDDDOCR_IMPORT_ERROR})" if _DDDDOCR_IMPORT_ERROR else ""
        raise RuntimeError(
            f"ddddocr is required for login_auth {login_auth}{detail}. "
            "Install a stable version, e.g. `pip install 'ddddocr>=1.4,<1.5'`."
        )
    return ddddocr


def _new_slider_ocr():
    return _require_ddddocr("captcha_slider").DdddOcr(det=False, ocr=False, show_ad=False)


def _new_text_ocr(login_auth: str = "captcha_image"):
    return _require_ddddocr(login_auth).DdddOcr(det=False, ocr=True, show_ad=False)


def _new_detection_ocr():
    return _require_ddddocr("captcha_click").DdddOcr(det=True, ocr=False, show_ad=False)


def _captcha_scope(selectors: dict, captcha_type: str) -> dict[str, Any]:
    if not isinstance(selectors, dict):
        return {}
    captcha_obj = selectors.get("captcha")
    if not isinstance(captcha_obj, dict):
        return {}
    scoped = captcha_obj.get(captcha_type)
    return scoped if isinstance(scoped, dict) else {}


def _first_non_empty(values: Sequence[Any], default: str = "") -> str:
    for value in values:
        candidate = str(value or "").strip()
        if candidate:
            return candidate
    return default


def _resolve_captcha_selector(
    selectors: dict,
    captcha_type: str,
    key: str,
    *,
    legacy_keys: Sequence[str] = (),
    default: str = "",
) -> str:
    scope = _captcha_scope(selectors, captcha_type)
    choices = [scope.get(key)]
    choices.extend(selectors.get(legacy_key) for legacy_key in legacy_keys)
    return _first_non_empty(choices, default=default)


def _estimate_drag_distance(
    slider_track_png: bytes,
    slider_handle_png: bytes,
    track_width: float,
    handle_width: float,
    *,
    slide_ocr: Any,
) -> float:
    max_distance = max(0.0, track_width - handle_width - 2.0)

    try:
        result = slide_ocr.slide_match(slider_handle_png, slider_track_png, simple_target=True)
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


async def _drag_slider(page: Page, drag_distance: float, *, handle_selector: str) -> None:
    slider_handle = page.locator(handle_selector).first
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


async def _solve_slider_captcha(page: Page, timeout_ms: int, selectors: dict) -> None:
    track_selector = _resolve_captcha_selector(
        selectors,
        "slider",
        "track",
        legacy_keys=("captcha_slider_track",),
        default="#v-7-form-item",
    )
    handle_selector = _resolve_captcha_selector(
        selectors,
        "slider",
        "handle",
        legacy_keys=("captcha_slider_handle",),
        default='[name="captcha-action"]',
    )
    hint_selector = _resolve_captcha_selector(
        selectors,
        "slider",
        "hint",
        legacy_keys=("captcha_slider_hint",),
        default="",
    )
    slider_track = page.locator(track_selector).first
    slider_handle = page.locator(handle_selector).first
    slide_ocr = _new_slider_ocr()

    await slider_track.wait_for(state="visible", timeout=timeout_ms)
    await slider_handle.wait_for(state="visible", timeout=timeout_ms)
    slider_hint = await _resolve_slider_hint_locator(
        page,
        slider_track,
        timeout_ms=timeout_ms,
        hint_selector=hint_selector,
    )

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
            slide_ocr=slide_ocr,
        )
        await _drag_slider(page, drag_distance, handle_selector=handle_selector)
        await page.wait_for_timeout(800)

        if await _is_slider_verified(slider_hint, slider_track):
            return

    raise RuntimeError("Slider captcha verification failed after 3 attempts.")


async def _resolve_slider_hint_locator(
    page: Page,
    slider_track: Any,
    *,
    timeout_ms: int,
    hint_selector: str,
):
    if hint_selector:
        locator = page.locator(hint_selector).first
        await locator.wait_for(state="visible", timeout=timeout_ms)
        return locator

    probe_timeout = max(300, min(timeout_ms, 1200))
    candidates = (
        slider_track.locator("div").nth(1),
        slider_track.locator("div").first,
        page.locator("text=/验证通过|拖动滑块|滑块验证|按住滑块|请完成滑块验证/i").first,
    )
    for candidate in candidates:
        try:
            await candidate.wait_for(state="visible", timeout=probe_timeout)
            return candidate
        except Exception:
            continue

    raise RuntimeError(
        "Cannot auto-detect slider hint element. Configure login_selectors.captcha.slider.hint in web_systems.login_selectors."
    )


def _is_slider_success_text(text: str) -> bool:
    return bool(SLIDER_SUCCESS_TEXT_RE.search(str(text or "").strip()))


async def _is_slider_verified(slider_hint: Any, slider_track: Any) -> bool:
    try:
        if _is_slider_success_text(await slider_hint.inner_text()):
            return True
    except Exception:
        pass

    try:
        if _is_slider_success_text(await slider_track.inner_text()):
            return True
    except Exception:
        pass

    return False


def _classify_image_captcha_code(img_bytes: bytes) -> str:
    text_ocr = _new_text_ocr("captcha_image")
    try:
        raw = str(text_ocr.classification(img_bytes) or "")
    except Exception:
        return ""
    clean = "".join(ch for ch in raw if ch.isalnum())
    if 3 <= len(clean) <= 8:
        return clean
    return ""


async def _solve_image_captcha(page: Page, timeout_ms: int, selectors: dict) -> None:
    image_selector = _resolve_captcha_selector(
        selectors,
        "image",
        "image",
        legacy_keys=("captcha_image", "captcha_image_selector"),
    )
    input_selector = _resolve_captcha_selector(
        selectors,
        "image",
        "input",
        legacy_keys=("captcha_input", "captcha_input_selector"),
    )
    refresh_selector = _resolve_captcha_selector(
        selectors,
        "image",
        "refresh",
        legacy_keys=("captcha_refresh", "captcha_refresh_selector"),
    )
    if not image_selector or not input_selector:
        raise RuntimeError("captcha_image requires selectors: captcha.image.image and captcha.image.input")

    image_locator = page.locator(image_selector).first
    input_locator = page.locator(input_selector).first
    refresh_locator = page.locator(refresh_selector).first if refresh_selector else None

    await image_locator.wait_for(state="visible", timeout=timeout_ms)
    await input_locator.wait_for(state="visible", timeout=timeout_ms)

    for _attempt in range(1, 4):
        image_png = await image_locator.screenshot()
        captcha_value = _classify_image_captcha_code(image_png)
        if captcha_value:
            await input_locator.fill(captcha_value)
            return

        if refresh_locator is not None:
            await refresh_locator.click()
            await page.wait_for_timeout(500)

    raise RuntimeError("Image captcha recognition failed after 3 attempts.")


def _split_click_tokens(text: str) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    pieces = [piece.strip() for piece in CLICK_SPLIT_RE.split(normalized) if piece.strip()]
    if len(pieces) > 1:
        return [piece for piece in pieces if CLICK_TOKEN_RE.search(piece)]
    one = pieces[0] if pieces else normalized
    if re.fullmatch(r"[\u4e00-\u9fff]+", one) and len(one) > 1:
        return list(one)
    if re.fullmatch(r"[A-Za-z0-9]+", one) and len(one) > 1:
        return list(one)
    return [one] if CLICK_TOKEN_RE.search(one) else []


def _extract_click_targets(prompt_text: str) -> list[str]:
    text = str(prompt_text or "").strip()
    if not text:
        return []

    quoted_segments = re.findall(r"[\"“](.*?)[\"”]", text)
    if quoted_segments:
        targets: list[str] = []
        for segment in quoted_segments:
            targets.extend(_split_click_tokens(segment))
        if targets:
            return targets

    if "：" in text:
        text = text.split("：", maxsplit=1)[1]
    elif ":" in text:
        text = text.split(":", maxsplit=1)[1]

    text = _strip_click_prompt_prefix(text)
    parsed = _split_click_tokens(text)
    if parsed:
        return parsed

    return CLICK_TOKEN_RE.findall(text)


def _strip_click_prompt_prefix(text: str) -> str:
    cleaned = str(text or "").strip()
    while cleaned:
        for prefix in CLICK_PROMPT_PREFIXES:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip()
                break
        else:
            break
    return cleaned


def _normalize_click_token(token: str) -> str:
    match = CLICK_TOKEN_RE.findall(str(token or ""))
    return match[0] if match else ""


def _crop_png(image: Any, bbox: tuple[int, int, int, int]) -> bytes:
    x1, y1, x2, y2 = bbox
    cropped = image.crop((x1, y1, x2, y2))
    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    return buffer.getvalue()


def _detect_click_target_points(
    image_png: bytes,
    targets: list[str],
    detect_ocr: Any,
    text_ocr: Any,
    image_box: dict[str, float],
) -> list[tuple[float, float]] | None:
    if Image is None:
        raise RuntimeError("Pillow is required for captcha_click image crop.")

    try:
        boxes = detect_ocr.detection(img_bytes=image_png)
    except Exception:
        return None
    if not isinstance(boxes, list) or not boxes:
        return None

    image = Image.open(io.BytesIO(image_png))
    image_width, image_height = image.size
    if image_width <= 0 or image_height <= 0:
        return None
    scale_x = image_box["width"] / image_width
    scale_y = image_box["height"] / image_height

    detected_points: dict[str, list[tuple[float, float]]] = {}
    for raw_box in boxes:
        if not isinstance(raw_box, (list, tuple)) or len(raw_box) < 4:
            continue
        x1 = max(0, min(int(raw_box[0]), image_width))
        y1 = max(0, min(int(raw_box[1]), image_height))
        x2 = max(0, min(int(raw_box[2]), image_width))
        y2 = max(0, min(int(raw_box[3]), image_height))
        if x2 - x1 <= 1 or y2 - y1 <= 1:
            continue

        token = ""
        try:
            token = _normalize_click_token(str(text_ocr.classification(_crop_png(image, (x1, y1, x2, y2))) or ""))
        except Exception:
            token = ""
        if not token:
            continue

        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        page_x = image_box["x"] + center_x * scale_x
        page_y = image_box["y"] + center_y * scale_y
        detected_points.setdefault(token, []).append((page_x, page_y))

    if not detected_points:
        return None

    selected_points: list[tuple[float, float]] = []
    used_counts: dict[str, int] = {}
    for raw_target in targets:
        target = _normalize_click_token(raw_target)
        if not target:
            return None
        candidates = detected_points.get(target)
        if not candidates:
            return None
        used_idx = used_counts.get(target, 0)
        if used_idx >= len(candidates):
            return None
        selected_points.append(candidates[used_idx])
        used_counts[target] = used_idx + 1
    return selected_points


async def _solve_click_captcha(page: Page, timeout_ms: int, selectors: dict) -> None:
    image_selector = _resolve_captcha_selector(
        selectors,
        "click",
        "image",
        legacy_keys=("captcha_click_image", "captcha_click_image_selector"),
    )
    prompt_selector = _resolve_captcha_selector(
        selectors,
        "click",
        "prompt",
        legacy_keys=("captcha_click_prompt", "captcha_click_prompt_selector"),
    )
    refresh_selector = _resolve_captcha_selector(
        selectors,
        "click",
        "refresh",
        legacy_keys=("captcha_click_refresh", "captcha_click_refresh_selector"),
    )
    confirm_selector = _resolve_captcha_selector(
        selectors,
        "click",
        "confirm",
        legacy_keys=("captcha_click_confirm", "captcha_click_confirm_selector"),
    )
    error_selector = _resolve_captcha_selector(
        selectors,
        "click",
        "error",
        legacy_keys=("captcha_click_error", "captcha_click_error_selector"),
    )
    if not image_selector or not prompt_selector:
        raise RuntimeError("captcha_click requires selectors: captcha.click.image and captcha.click.prompt")

    image_locator = page.locator(image_selector).first
    prompt_locator = page.locator(prompt_selector).first
    refresh_locator = page.locator(refresh_selector).first if refresh_selector else None
    confirm_locator = page.locator(confirm_selector).first if confirm_selector else None
    error_locator = page.locator(error_selector).first if error_selector else None
    detect_ocr = _new_detection_ocr()
    text_ocr = _new_text_ocr("captcha_click")

    await image_locator.wait_for(state="visible", timeout=timeout_ms)
    await prompt_locator.wait_for(state="visible", timeout=timeout_ms)

    for _attempt in range(1, 4):
        prompt_text = (await prompt_locator.inner_text()).strip()
        targets = _extract_click_targets(prompt_text)
        if not targets:
            raise RuntimeError("Cannot parse click captcha prompt text.")

        image_png = await image_locator.screenshot()
        image_box = await image_locator.bounding_box()
        if not image_box:
            raise RuntimeError("Cannot resolve click captcha image bounding box.")

        points = _detect_click_target_points(
            image_png=image_png,
            targets=targets,
            detect_ocr=detect_ocr,
            text_ocr=text_ocr,
            image_box=image_box,
        )
        if points is None:
            if refresh_locator is not None:
                await refresh_locator.click()
                await page.wait_for_timeout(400)
            continue

        for page_x, page_y in points:
            await page.mouse.click(page_x, page_y, delay=random.randint(30, 90))
            await page.wait_for_timeout(random.randint(120, 220))

        if confirm_locator is not None:
            await confirm_locator.click()
            await page.wait_for_timeout(300)

        if error_locator is not None:
            try:
                error_text = (await error_locator.inner_text()).strip()
                if error_text:
                    if refresh_locator is not None:
                        await refresh_locator.click()
                        await page.wait_for_timeout(400)
                    continue
            except Exception:
                pass
        return

    raise RuntimeError("Click captcha recognition failed after 3 attempts.")


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
