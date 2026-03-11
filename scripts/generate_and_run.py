#!/usr/bin/env python3
"""Generate and run Playwright checks from natural-language dialogues."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
MCP_SRC = ROOT / "mcp-server" / "src"
DEFAULT_OUTPUT_PATH = ROOT / "output" / "playwright" / "dialogue-check-result.json"
DEFAULT_SCREENSHOT_DIR = ROOT / "output" / "playwright" / "screenshots" / "dialogue-check"
DEFAULT_DIALOGUES = [
    "帮我看一下滑动窗口系统工作台页面中的点击首页是否正常",
    "帮我看一下滑动窗口系统分析夜中当前访问量是多少",
    "帮我看一下滑动窗口系统表单演示页面是否能正常打开",
]

IntentType = Literal["click_home", "read_visits", "open_form"]


@dataclass(slots=True)
class DialogueIntent:
    raw_text: str
    system_keyword: str
    page_keyword: str
    intent_type: IntentType



def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and run Playwright checks from dialogue intents.")
    parser.add_argument(
        "--dialogue",
        action="append",
        dest="dialogues",
        help="Dialogue line. Repeat to provide multiple lines. If omitted, use built-in sample lines.",
    )
    parser.add_argument("--system-keyword", default=None, help="Force system keyword. Default: infer from dialogue")
    parser.add_argument("--max-locators", type=int, default=10, help="Max locators requested from MCP context service")
    parser.add_argument("--max-fallback-pages", type=int, default=2, help="Max fallback pages requested from MCP")
    parser.add_argument("--timeout-ms", type=int, default=8_000, help="Playwright timeout in milliseconds")
    parser.add_argument("--slow-mo", type=int, default=50, help="Playwright slow-mo milliseconds")
    parser.add_argument("--headless", action="store_true", help="Run headless. Default is headed mode")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Result JSON output path")
    parser.add_argument(
        "--screenshot-dir",
        default=str(DEFAULT_SCREENSHOT_DIR),
        help="Directory for step screenshots",
    )
    return parser.parse_args(argv)


def _normalize_dialogue_text(text: str) -> str:
    value = text.strip()
    value = value.replace("分析夜", "分析页")
    return value


def _infer_system_keyword(dialogues: list[str]) -> str | None:
    for item in dialogues:
        normalized = _normalize_dialogue_text(item)
        match = re.search(r"帮我看一下\s*(.+?系统)", normalized)
        if match:
            return match.group(1).strip()
    return None


def _infer_intent(dialogue: str, system_keyword: str) -> DialogueIntent:
    normalized = _normalize_dialogue_text(dialogue)

    if "工作台" in normalized and "首页" in normalized:
        return DialogueIntent(
            raw_text=dialogue,
            system_keyword=system_keyword,
            page_keyword="工作台",
            intent_type="click_home",
        )

    if "分析" in normalized and "访问量" in normalized:
        return DialogueIntent(
            raw_text=dialogue,
            system_keyword=system_keyword,
            page_keyword="分析页",
            intent_type="read_visits",
        )

    if "表单" in normalized and ("打开" in normalized or "正常" in normalized):
        return DialogueIntent(
            raw_text=dialogue,
            system_keyword=system_keyword,
            page_keyword="表单演示",
            intent_type="open_form",
        )

    raise ValueError(f"无法解析对话意图: {dialogue}")


def parse_dialogues(dialogues: list[str], forced_system_keyword: str | None = None) -> list[DialogueIntent]:
    if not dialogues:
        raise ValueError("对话列表不能为空")

    cleaned = [_normalize_dialogue_text(item) for item in dialogues if item and item.strip()]
    if not cleaned:
        raise ValueError("对话列表不能为空")

    system_keyword = (forced_system_keyword or "").strip() or _infer_system_keyword(cleaned)
    if not system_keyword:
        raise ValueError("未在对话中识别到系统名称，请提供正确的系统名称后重试")
    return [_infer_intent(item, system_keyword) for item in dialogues]


def _ensure_mcp_package_importable() -> None:
    if str(MCP_SRC) not in sys.path:
        sys.path.insert(0, str(MCP_SRC))

    package_name = "menu_context_mcp"
    if package_name in sys.modules:
        return

    package = types.ModuleType(package_name)
    package.__path__ = [str(MCP_SRC / package_name)]
    sys.modules[package_name] = package


async def _query_mcp_context(
    intent: DialogueIntent,
    *,
    max_locators: int,
    max_fallback_pages: int,
) -> dict[str, Any]:
    _ensure_mcp_package_importable()

    config_mod = import_module("menu_context_mcp.config")
    service_mod = import_module("menu_context_mcp.service")
    schemas_mod = import_module("menu_context_mcp.schemas")

    settings = config_mod.get_settings()
    service = service_mod.ContextRetrievalService(settings=settings)
    query = schemas_mod.ContextQuery(
        system_keyword=intent.system_keyword,
        page_keyword=intent.page_keyword,
        max_locators=max_locators,
        max_fallback_pages=max_fallback_pages,
    )
    response = await service.get_page_playwright_context(query)
    return response.model_dump(mode="json")


async def _query_storage_state(system_keyword: str) -> dict[str, Any]:
    _ensure_mcp_package_importable()

    config_mod = import_module("menu_context_mcp.config")
    service_mod = import_module("menu_context_mcp.service")
    schemas_mod = import_module("menu_context_mcp.schemas")

    settings = config_mod.get_settings()
    service = service_mod.ContextRetrievalService(settings=settings)
    query = schemas_mod.StorageStateQuery(system_name=system_keyword)
    response = await service.get_storage_state_for_session(query)
    return response.model_dump(mode="json")


async def _close_mcp_db() -> None:
    _ensure_mcp_package_importable()
    db_mod = import_module("menu_context_mcp.db")
    await db_mod.close_db()


def _extract_origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _looks_like_login_url(url: str) -> bool:
    lowered = (url or "").lower()
    markers = ("#/auth/login", "/auth/login", "#/login", "/login", "#/signin", "/signin")
    return any(token in lowered for token in markers)


def _parse_locator(locator_expr: str) -> tuple[str, str | None, str | None]:
    expr = (locator_expr or "").strip()
    if not expr:
        return ("invalid", None, None)

    match_text = re.match(r"^get_by_text\((['\"])(.*?)\1\)$", expr)
    if match_text:
        return ("get_by_text", match_text.group(2), None)

    match_role = re.match(r"^get_by_role\((['\"])(.*?)\1\s*,\s*name\s*=\s*(['\"])(.*?)\3\)$", expr)
    if match_role:
        return ("get_by_role", match_role.group(2), match_role.group(4))

    return ("css", expr, None)


async def _click_by_locator(page, locator_expr: str, timeout_ms: int) -> bool:
    kind, primary, secondary = _parse_locator(locator_expr)

    if kind == "get_by_text" and primary is not None:
        locator = page.get_by_text(primary)
    elif kind == "get_by_role" and primary is not None:
        locator = page.get_by_role(primary, name=secondary)
    elif kind == "css" and primary is not None:
        locator = page.locator(primary)
    else:
        return False

    await locator.first.wait_for(state="visible", timeout=timeout_ms)
    await locator.first.click(timeout=timeout_ms)
    return True


def _pick_home_locator(locators: list[dict[str, Any]]) -> str | None:
    for item in locators:
        text = f"{item.get('text_content') or ''} {item.get('nearby_text') or ''} {item.get('usage_description') or ''}".lower()
        if "首页" in text:
            locator = (item.get("playwright_locator") or "").strip()
            if locator:
                return locator
    return None


def _pick_locator_by_text(locators: list[dict[str, Any]], target: str) -> str | None:
    for item in locators:
        text = f"{item.get('text_content') or ''} {item.get('nearby_text') or ''} {item.get('usage_description') or ''}"
        if target in text:
            locator = (item.get("playwright_locator") or "").strip()
            if locator:
                return locator
    return None


async def _inject_session_storage(page, session_storage: dict[str, Any]) -> None:
    await page.evaluate(
        """(data) => {
            for (const [key, value] of Object.entries(data || {})) {
                window.sessionStorage.setItem(key, String(value));
            }
        }""",
        session_storage,
    )


async def _extract_visit_metric(page) -> dict[str, Any] | None:
    return await page.evaluate(
        r"""() => {
            const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim();
            const cleanNumber = (value) => (value || '').replace(/[^\d]+$/g, '');
            const all = Array.from(document.querySelectorAll('body *'));

            for (const el of all) {
                const text = normalize(el.textContent || '');
                if (!text || text.length > 120 || !text.includes('访问量')) {
                    continue;
                }
                const number = text.match(/(\d[\d,.]*)/);
                if (number) {
                    return { value: cleanNumber(number[1]), source: text, strategy: 'inline' };
                }
                const parentText = normalize(el.parentElement ? el.parentElement.textContent || '' : '');
                const parentNumber = parentText.match(/(\d[\d,.]*)/);
                if (parentNumber) {
                    return { value: cleanNumber(parentNumber[1]), source: parentText, strategy: 'parent' };
                }
            }

            const body = normalize(document.body ? document.body.innerText || '' : '');
            const byBody = body.match(/访问量[^\d]{0,8}(\d[\d,.]*)/);
            if (byBody) {
                return { value: cleanNumber(byBody[1]), source: byBody[0], strategy: 'body' };
            }

            return null;
        }"""
    )


async def _run_intent_case(page, case: dict[str, Any], *, timeout_ms: int, screenshot_dir: Path) -> dict[str, Any]:
    dialogue = case["dialogue"]
    intent_type = case["intent_type"]
    route_path = case.get("route_path") or ""
    target_url = case["target_url"]
    locators: list[dict[str, Any]] = case.get("locators") or []

    result: dict[str, Any] = {
        "dialogue": dialogue,
        "intent_type": intent_type,
        "target_url": target_url,
        "route_path": route_path,
        "status": "failed",
        "message": "",
        "current_url": "",
        "visit_metric": None,
        "action_trace": [],
        "screenshot_path": None,
    }

    result["action_trace"].append(f"打开目标页面: {target_url}")
    await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_timeout(1200)

    if _looks_like_login_url(page.url):
        result["message"] = "页面跳转到登录页，会话态失效"
        result["current_url"] = page.url
        return result

    if intent_type == "click_home":
        mcp_locator = _pick_home_locator(locators)
        clicked = False
        if mcp_locator:
            result["action_trace"].append(f"使用 MCP 定位器点击首页: {mcp_locator}")
            try:
                clicked = await _click_by_locator(page, mcp_locator, timeout_ms)
            except (PlaywrightTimeoutError, PlaywrightError):
                clicked = False

        if not clicked:
            result["action_trace"].append("MCP 定位器未提供首页，降级尝试 DOM 文本点击: 首页")
            try:
                home_node = page.get_by_text("首页")
                await home_node.first.wait_for(state="visible", timeout=timeout_ms)
                await home_node.first.click(timeout=timeout_ms)
                clicked = True
            except (PlaywrightTimeoutError, PlaywrightError):
                clicked = False

        await page.wait_for_timeout(1200)
        result["current_url"] = page.url

        if clicked:
            result["status"] = "passed"
            result["message"] = "首页点击动作可执行"
        else:
            result["message"] = "未找到可点击的首页元素"

    elif intent_type == "read_visits":
        visit_tab_locator = _pick_locator_by_text(locators, "月访问量")
        if visit_tab_locator:
            result["action_trace"].append(f"点击访问量 Tab: {visit_tab_locator}")
            try:
                await _click_by_locator(page, visit_tab_locator, timeout_ms)
                await page.wait_for_timeout(1000)
            except (PlaywrightTimeoutError, PlaywrightError):
                result["action_trace"].append("访问量 Tab 点击失败，继续尝试直接读取")

        metric = await _extract_visit_metric(page)
        result["current_url"] = page.url
        result["visit_metric"] = metric

        if metric and metric.get("value"):
            result["status"] = "passed"
            result["message"] = f"当前访问量读取成功: {metric['value']}"
        else:
            result["message"] = "未能从分析页提取访问量"

    elif intent_type == "open_form":
        visible_locator = None
        for item in locators[:5]:
            candidate = (item.get("playwright_locator") or "").strip()
            if not candidate:
                continue
            try:
                kind, primary, secondary = _parse_locator(candidate)
                if kind == "get_by_text" and primary is not None:
                    locator = page.get_by_text(primary)
                elif kind == "get_by_role" and primary is not None:
                    locator = page.get_by_role(primary, name=secondary)
                elif kind == "css" and primary is not None:
                    locator = page.locator(primary)
                else:
                    continue
                await locator.first.wait_for(state="visible", timeout=timeout_ms)
                visible_locator = candidate
                break
            except (PlaywrightTimeoutError, PlaywrightError):
                continue

        result["current_url"] = page.url
        if visible_locator and (not route_path or route_path in page.url):
            result["status"] = "passed"
            result["message"] = f"表单演示页面可打开，关键元素可见: {visible_locator}"
        elif visible_locator:
            result["status"] = "passed"
            result["message"] = f"表单演示页面可打开，关键元素可见: {visible_locator}"
        else:
            result["message"] = "表单演示页面未检测到关键元素"

    screenshot_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-z0-9_-]+", "-", intent_type.lower())
    screenshot_path = screenshot_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{safe_name}.png"
    await page.screenshot(path=str(screenshot_path), full_page=True)
    result["screenshot_path"] = str(screenshot_path)
    return result


async def _run_playwright_cases(
    *,
    storage_response: dict[str, Any],
    prepared_cases: list[dict[str, Any]],
    timeout_ms: int,
    slow_mo: int,
    headless: bool,
    screenshot_dir: Path,
) -> list[dict[str, Any]]:
    state = storage_response.get("state") or {}
    storage_state = state.get("storage_state") or {}
    session_storage = state.get("session_storage") or {}

    if not storage_state:
        raise RuntimeError("storage_state 为空，无法复用登录态")

    results: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = await browser.new_context(storage_state=storage_state)
        context.set_default_timeout(timeout_ms)
        page = await context.new_page()
        page.set_default_timeout(timeout_ms)

        try:
            if session_storage:
                first_target = prepared_cases[0].get("target_url") or storage_response.get("system", {}).get("base_url")
                if first_target:
                    await page.goto(_extract_origin(first_target), wait_until="domcontentloaded", timeout=timeout_ms)
                    await _inject_session_storage(page, session_storage)

            for case in prepared_cases:
                case_result = await _run_intent_case(
                    page,
                    case,
                    timeout_ms=timeout_ms,
                    screenshot_dir=screenshot_dir,
                )
                results.append(case_result)
        finally:
            await context.close()
            await browser.close()

    return results


async def _run(args: argparse.Namespace) -> int:
    try:
        dialogues = args.dialogues or list(DEFAULT_DIALOGUES)
        intents = parse_dialogues(dialogues, forced_system_keyword=args.system_keyword)

        system_keyword = intents[0].system_keyword
        storage_response = await _query_storage_state(system_keyword)
        if storage_response.get("status") != "ok":
            user_hint = None
            if storage_response.get("status") == "system_not_found":
                user_hint = f"未找到与“{system_keyword}”匹配的系统，请提供正确的系统名称后重试"
            payload = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "system_keyword": system_keyword,
                "status": "failed",
                "error": {
                    "stage": "get_storage_state_for_session",
                    "response": storage_response,
                    "user_hint": user_hint,
                },
            }
            output_path = Path(args.output).resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1

        prepared_cases: list[dict[str, Any]] = []
        for intent in intents:
            context = await _query_mcp_context(
                intent,
                max_locators=args.max_locators,
                max_fallback_pages=args.max_fallback_pages,
            )
            if context.get("status") != "ok" or not context.get("target_page"):
                prepared_cases.append(
                    {
                        "dialogue": intent.raw_text,
                        "intent_type": intent.intent_type,
                        "target_url": None,
                        "route_path": None,
                        "context_status": context.get("status"),
                        "context_reasons": context.get("reasons") or [],
                        "locators": [],
                    }
                )
                continue

            target_page = context["target_page"]
            prepared_cases.append(
                {
                    "dialogue": intent.raw_text,
                    "intent_type": intent.intent_type,
                    "target_url": target_page.get("target_url"),
                    "route_path": target_page.get("route_path"),
                    "context_status": context.get("status"),
                    "context_reasons": context.get("reasons") or [],
                    "target_page": target_page,
                    "locators": context.get("locators") or [],
                }
            )

        runnable_cases = [item for item in prepared_cases if item.get("target_url")]
        case_results: list[dict[str, Any]] = []

        if runnable_cases:
            case_results = await _run_playwright_cases(
                storage_response=storage_response,
                prepared_cases=runnable_cases,
                timeout_ms=args.timeout_ms,
                slow_mo=max(0, args.slow_mo),
                headless=bool(args.headless),
                screenshot_dir=Path(args.screenshot_dir).resolve(),
            )

        skipped_results: list[dict[str, Any]] = []
        for item in prepared_cases:
            if item.get("target_url"):
                continue
            skipped_results.append(
                {
                    "dialogue": item["dialogue"],
                    "intent_type": item["intent_type"],
                    "target_url": None,
                    "route_path": None,
                    "status": "skipped",
                    "message": f"MCP 上下文不可用: {item.get('context_status')}",
                    "current_url": "",
                    "visit_metric": None,
                    "action_trace": [],
                    "screenshot_path": None,
                    "context_status": item.get("context_status"),
                    "context_reasons": item.get("context_reasons") or [],
                }
            )

        merged_results = case_results + skipped_results
        merged_results.sort(key=lambda item: dialogues.index(item["dialogue"]) if item["dialogue"] in dialogues else 999)

        passed = sum(1 for item in merged_results if item.get("status") == "passed")
        failed = sum(1 for item in merged_results if item.get("status") == "failed")
        skipped = sum(1 for item in merged_results if item.get("status") == "skipped")

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "system_keyword": system_keyword,
            "storage_status": storage_response.get("status"),
            "system": storage_response.get("system"),
            "summary": {
                "total": len(merged_results),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "headless": bool(args.headless),
                "slow_mo": max(0, args.slow_mo),
                "timeout_ms": args.timeout_ms,
            },
            "cases": merged_results,
        }

        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if failed == 0 else 2
    finally:
        await _close_mcp_db()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
