from __future__ import annotations

import io

import pytest

from src.crawler import auth_crawler as ac


class DummyLocator:
    def __init__(self, *, text: str = "", bbox: dict[str, float] | None = None, screenshots: list[bytes] | None = None):
        self.text = text
        self.bbox = bbox
        self.screenshots = list(screenshots or [b"image"])
        self.fills: list[str] = []
        self.click_count = 0
        self.wait_calls: list[tuple[str | None, int | None]] = []
        self.first = self

    async def wait_for(self, state: str | None = None, timeout: int | None = None) -> None:
        self.wait_calls.append((state, timeout))

    async def fill(self, value: str) -> None:
        self.fills.append(value)

    async def click(self) -> None:
        self.click_count += 1

    async def inner_text(self) -> str:
        return self.text

    async def screenshot(self) -> bytes:
        if len(self.screenshots) > 1:
            return self.screenshots.pop(0)
        return self.screenshots[0]

    async def bounding_box(self) -> dict[str, float] | None:
        return self.bbox


class DummyMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[float, float, int | None]] = []

    async def click(self, x: float, y: float, delay: int | None = None) -> None:
        self.clicks.append((x, y, delay))


class DummyPage:
    def __init__(self, locators: dict[str, DummyLocator]) -> None:
        self.locators = locators
        self.mouse = DummyMouse()
        self.waits: list[int] = []

    def locator(self, selector: str) -> DummyLocator:
        if selector not in self.locators:
            raise AssertionError(f"Unexpected selector: {selector}")
        return self.locators[selector]

    async def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


@pytest.mark.asyncio
async def test_login_auth_dispatch_by_type(monkeypatch):
    calls: list[str] = []

    async def fake_slider(*_args, **_kwargs):
        calls.append("slider")

    async def fake_image(*_args, **_kwargs):
        calls.append("image")

    async def fake_click(*_args, **_kwargs):
        calls.append("click")

    monkeypatch.setattr(ac, "_solve_slider_captcha", fake_slider)
    monkeypatch.setattr(ac, "_solve_image_captcha", fake_image)
    monkeypatch.setattr(ac, "_solve_click_captcha", fake_click)

    await ac._solve_login_challenge(None, "captcha_slider", {}, 1000)
    await ac._solve_login_challenge(None, "captcha_image", {}, 1000)
    await ac._solve_login_challenge(None, "captcha_click", {}, 1000)
    await ac._solve_login_challenge(None, "none", {}, 1000)
    await ac._solve_login_challenge(None, "", {}, 1000)

    assert calls == ["slider", "image", "click"]


@pytest.mark.asyncio
async def test_login_auth_fail_fast_for_unimplemented_types():
    with pytest.raises(RuntimeError, match="captcha_sms is not implemented"):
        await ac._solve_login_challenge(None, "captcha_sms", {}, 1000)
    with pytest.raises(RuntimeError, match="sso is not implemented"):
        await ac._solve_login_challenge(None, "sso", {}, 1000)


def test_require_ddddocr_error_includes_actionable_hint(monkeypatch):
    monkeypatch.setattr(ac, "ddddocr", None)
    monkeypatch.setattr(ac, "_DDDDOCR_IMPORT_ERROR", "ImportError('cannot import name DdddOcr')")
    with pytest.raises(RuntimeError, match="pip install 'ddddocr>=1.4,<1.5'"):
        ac._require_ddddocr("captcha_slider")


@pytest.mark.asyncio
async def test_image_captcha_retry_then_fill(monkeypatch):
    page = DummyPage(
        {
            "#img": DummyLocator(screenshots=[b"bad", b"good"]),
            "#input": DummyLocator(),
            "#refresh": DummyLocator(),
        }
    )

    def fake_classify(img_bytes: bytes) -> str:
        return "" if img_bytes == b"bad" else "a1B2"

    monkeypatch.setattr(ac, "_classify_image_captcha_code", fake_classify)

    selectors = {
        "captcha": {
            "image": {
                "image": "#img",
                "input": "#input",
                "refresh": "#refresh",
            }
        }
    }
    await ac._solve_image_captcha(page, 1000, selectors)

    assert page.locators["#input"].fills[-1] == "a1B2"
    assert page.locators["#refresh"].click_count == 1


@pytest.mark.asyncio
async def test_click_captcha_retry_and_confirm(monkeypatch):
    page = DummyPage(
        {
            "#click-img": DummyLocator(
                text="",
                bbox={"x": 100.0, "y": 80.0, "width": 200.0, "height": 100.0},
                screenshots=[b"one", b"two"],
            ),
            "#click-prompt": DummyLocator(text='请依次点击 "甲,乙"'),
            "#click-refresh": DummyLocator(),
            "#click-confirm": DummyLocator(),
        }
    )

    calls = {"n": 0}

    def fake_detect(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return [(120.0, 100.0), (220.0, 120.0)]

    monkeypatch.setattr(ac, "_new_detection_ocr", lambda: object())
    monkeypatch.setattr(ac, "_new_text_ocr", lambda _auth="captcha_click": object())
    monkeypatch.setattr(ac, "_detect_click_target_points", fake_detect)

    selectors = {
        "captcha": {
            "click": {
                "image": "#click-img",
                "prompt": "#click-prompt",
                "refresh": "#click-refresh",
                "confirm": "#click-confirm",
            }
        }
    }
    await ac._solve_click_captcha(page, 1000, selectors)

    assert page.locators["#click-refresh"].click_count == 1
    assert page.locators["#click-confirm"].click_count == 1
    assert len(page.mouse.clicks) == 2


def test_extract_click_targets_keep_order():
    assert ac._extract_click_targets('请依次点击 "甲,乙,丙"') == ["甲", "乙", "丙"]
    assert ac._extract_click_targets("请依次点击：甲乙") == ["甲", "乙"]
    assert ac._extract_click_targets("请依次点击甲乙") == ["甲", "乙"]
    assert ac._extract_click_targets("依次点击甲乙") == ["甲", "乙"]


def test_strip_click_prompt_prefix_longest_first():
    assert ac._strip_click_prompt_prefix("请依次点击甲乙") == "甲乙"
    assert ac._strip_click_prompt_prefix("依次点击甲乙") == "甲乙"


def test_click_target_point_scale_and_order():
    if ac.Image is None:
        pytest.skip("Pillow is unavailable in current environment.")

    image = ac.Image.new("RGB", (100, 50), color=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_png = buffer.getvalue()

    class FakeDetOCR:
        def detection(self, img_bytes: bytes):  # noqa: ARG002
            return [[10, 10, 20, 20], [60, 10, 70, 20]]

    class FakeTextOCR:
        def __init__(self) -> None:
            self.idx = 0

        def classification(self, _crop_png: bytes) -> str:
            self.idx += 1
            return "甲" if self.idx == 1 else "乙"

    points = ac._detect_click_target_points(
        image_png=image_png,
        targets=["甲", "乙"],
        detect_ocr=FakeDetOCR(),
        text_ocr=FakeTextOCR(),
        image_box={"x": 100.0, "y": 50.0, "width": 200.0, "height": 100.0},
    )
    assert points is not None
    assert points[0][0] == pytest.approx(130.0)
    assert points[0][1] == pytest.approx(80.0)
    assert points[1][0] == pytest.approx(230.0)
    assert points[1][1] == pytest.approx(80.0)
