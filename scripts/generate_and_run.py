#!/usr/bin/env python3
"""
Playwright 脚本生成与执行工具
根据用户输入生成检测脚本并执行

Usage:
    python generate_and_run.py --system "ERP" --page "工作台"
    python generate_and_run.py --system "A系统" --page "用户管理" --output ./result.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


# MCP Server API 配置
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8765")


_NL_QUERY_HINT_RE = re.compile(
    r"(?:看一下|看下|帮我|请|检查|核查|确认|测试|验证|是否|能否|正常|展现|展示|显示|打开|访问|下面的|下的|里的|中的|数据表格|表格|页面|模块)"
)
_PREFIX_CLEAN_RE = re.compile(
    r"^(?:请|麻烦|帮我|帮忙|看一下|看下|看一眼|检查一下|检查|核查一下|核查|确认一下|确认|测试一下|测试|验证一下|验证)+"
)
_SUFFIX_CLEAN_RE = re.compile(
    r"(?:数据表格|表格|数据)?(?:是否|能否|可否).*$|"
    r"(?:正常(?:展现|展示|显示|访问|打开)|展示正常|可访问|可打开).*$"
)
_SPLIT_RE = re.compile(
    r"(?:下面的|下的|里的|中的|/|->|→|>|：|:|，|,|。|？|\?|！|!|并且|并|然后)"
)
_QUOTED_RE = re.compile(r"[\"“'‘]([^\"”'’]{2,24})[\"”'’]")
_TOKEN_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9_-]{2,20}")
_SYSTEM_KEYWORD_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9_-]{1,32}?系统)")
_LOCATOR_TEXT_RE = re.compile(r"""^get_by_text\((["'])(.+?)\1\)$""")
_LOCATOR_ROLE_WITH_NAME_RE = re.compile(
    r"""^get_by_role\((["'])(.+?)\1\s*,\s*name\s*=\s*(["'])(.+?)\3\)$"""
)
_LOCATOR_ROLE_RE = re.compile(r"""^get_by_role\((["'])(.+?)\1\)$""")


@dataclass(slots=True)
class DialogueIntent:
    raw_text: str
    normalized_text: str
    system_keyword: str
    page_keyword: str
    intent_type: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成并执行 Playwright 检测脚本")
    parser.add_argument("--system", "-s", help="系统名称")
    parser.add_argument("--page", "-p", help="页面名称（可选）")
    parser.add_argument("--dialogue", "-d", action="append", default=[], help="自然语言检测描述（可重复传入）")
    parser.add_argument("--output", "-o", help="输出结果文件路径")
    parser.add_argument("--timeout-ms", type=int, default=8000, help="页面加载超时时间（毫秒）")
    parser.add_argument("--slow-mo", type=int, default=50, help="操作慢放毫秒")
    parser.add_argument("--headless", action="store_true", help="启用无头模式")
    parser.add_argument("--dry-run", action="store_true", help="仅生成脚本，不执行")
    return parser.parse_args(argv)


def _normalize_dialogue_text(text: str) -> str:
    normalized = str(text or "").strip()
    normalized = normalized.replace("分析夜", "分析页")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _extract_system_keyword(text: str) -> str | None:
    normalized = _PREFIX_CLEAN_RE.sub("", text).strip()
    match = _SYSTEM_KEYWORD_RE.search(normalized)
    if not match:
        return None
    return match.group(1).strip()


def _infer_intent_type(text: str) -> str:
    lowered = str(text or "").lower()
    if "点击首页" in lowered or "回到首页" in lowered:
        return "click_home"
    if "访问量" in lowered or "pv" in lowered or "uv" in lowered:
        return "read_visits"
    if "表单" in lowered or "打开" in lowered:
        return "open_form"
    return "check_page"


def _extract_page_from_sentence(text: str) -> str | None:
    sentence = _PREFIX_CLEAN_RE.sub("", str(text or "").strip())
    sentence = _SUFFIX_CLEAN_RE.sub("", sentence)
    sentence = sentence.strip()
    patterns = (
        re.compile(r"([\u4e00-\u9fa5A-Za-z0-9_-]{2,24})(?:页面|模块)"),
        re.compile(r"([\u4e00-\u9fa5A-Za-z0-9_-]{1,24}页)中"),
        re.compile(r"([\u4e00-\u9fa5A-Za-z0-9_-]{2,24})中"),
    )
    for pattern in patterns:
        match = pattern.search(sentence)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate
    return None


def parse_dialogues(dialogues: list[str], forced_system_keyword: str | None = None) -> list[DialogueIntent]:
    intents: list[DialogueIntent] = []
    forced_system = str(forced_system_keyword or "").strip() or None
    for raw in dialogues:
        raw_text = str(raw or "").strip()
        if not raw_text:
            continue
        normalized = _normalize_dialogue_text(raw_text)
        system_keyword = forced_system or _extract_system_keyword(normalized)
        if not system_keyword:
            raise ValueError("无法识别系统名称，请在对话中提供系统名称。")
        residual = normalized.replace(system_keyword, "", 1).strip()
        page_keyword = (
            _extract_page_from_sentence(residual)
            or extract_page_keyword(system_keyword, residual)
            or extract_page_keyword(system_keyword, normalized)
            or "未知页面"
        )
        intents.append(
            DialogueIntent(
                raw_text=raw_text,
                normalized_text=normalized,
                system_keyword=system_keyword,
                page_keyword=page_keyword,
                intent_type=_infer_intent_type(normalized),
            )
        )
    return intents


def _parse_locator(locator: str) -> tuple[str, str, str | None]:
    text = str(locator or "").strip()
    if not text:
        return "css", "", None
    text_match = _LOCATOR_TEXT_RE.match(text)
    if text_match:
        return "get_by_text", text_match.group(2), None
    role_with_name_match = _LOCATOR_ROLE_WITH_NAME_RE.match(text)
    if role_with_name_match:
        return "get_by_role", role_with_name_match.group(2), role_with_name_match.group(4)
    role_match = _LOCATOR_ROLE_RE.match(text)
    if role_match:
        return "get_by_role", role_match.group(2), None
    return "css", text, None


def _pick_home_locator(locators: list[dict[str, Any]]) -> str:
    home_keywords = ("首页", "home", "dashboard", "工作台")
    fallback = ""
    for item in locators or []:
        locator = str(item.get("playwright_locator") or "").strip()
        if not locator:
            continue
        if not fallback:
            fallback = locator
        search_text = " ".join(
            [
                str(item.get("usage_description") or ""),
                str(item.get("nearby_text") or ""),
                str(item.get("text_content") or ""),
                locator,
            ]
        ).lower()
        if any(keyword in search_text for keyword in home_keywords):
            return locator
    return fallback


def extract_page_keyword(system: str, page: str | None) -> str | None:
    """从自然语言页面描述中提取更稳定的页面关键词。"""
    if not page:
        return None

    raw = str(page).strip()
    if not raw:
        return None

    text = raw
    if system:
        text = text.replace(system, "")
    text = text.strip()
    if not text:
        return raw

    words = [item for item in re.split(r"\s+", text) if item]
    if len(words) >= 2 and all(1 <= len(item) <= 20 for item in words):
        # "超级管理员 菜单管理" 场景下优先叶子页面词。
        text = words[-1]

    compact = re.sub(r"\s+", "", text)
    if not compact:
        return raw

    looks_like_query = bool(_NL_QUERY_HINT_RE.search(compact)) or (compact != raw)
    if not looks_like_query and 1 < len(compact) <= 24:
        return compact

    quoted = _QUOTED_RE.findall(compact)
    if quoted:
        candidate = quoted[-1]
    else:
        candidate = _PREFIX_CLEAN_RE.sub("", compact)
        candidate = _SUFFIX_CLEAN_RE.sub("", candidate)
        if "页面" in candidate:
            candidate = candidate.split("页面", 1)[0]
        candidate = re.sub(r"(?:页面|模块)$", "", candidate)

        pieces = [item for item in _SPLIT_RE.split(candidate) if item]
        if pieces:
            candidate = pieces[-1]

    candidate = candidate.strip("-_/ >")
    candidate = re.sub(r"(?:页面|模块|菜单页|菜单项)$", "", candidate)
    if 1 < len(candidate) <= 24 and candidate not in {"页面", "模块", "菜单"}:
        return candidate

    tokens = _TOKEN_RE.findall(compact)
    if tokens:
        return tokens[-1]

    return raw


def _build_rest_tool_url(server_url: str, tool_name: str) -> str:
    base = server_url.rstrip("/")
    if base.endswith("/mcp"):
        return f"{base}/tools/{tool_name}"
    return f"{base}/mcp/tools/{tool_name}"


def _build_streamable_http_url(server_url: str) -> str:
    base = server_url.rstrip("/")
    if base.endswith("/mcp"):
        return base
    return f"{base}/mcp"


def _extract_first_json_from_sse(payload: str) -> dict[str, Any]:
    """从 SSE 文本中提取首个 JSON data 事件。"""
    events: list[str] = []
    current_data_lines: list[str] = []
    for raw_line in payload.splitlines():
        line = raw_line.rstrip("\r")
        if line.startswith("data:"):
            current_data_lines.append(line[len("data:") :].lstrip())
            continue
        if line == "" and current_data_lines:
            events.append("\n".join(current_data_lines))
            current_data_lines = []
    if current_data_lines:
        events.append("\n".join(current_data_lines))

    for item in events:
        text = item.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("No JSON payload found in SSE response")


async def _parse_jsonrpc_message(response: Any) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" in content_type:
        return _extract_first_json_from_sse(response.text)
    if "application/json" in content_type:
        parsed = response.json()
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("JSON response is not an object")
    return _extract_first_json_from_sse(response.text)


def _extract_tool_result_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                return text
    return ""


async def _call_mcp_tool_streamable_http(
    client: Any, server_url: str, tool_name: str, params: dict[str, Any]
) -> dict[str, Any]:
    endpoint = _build_streamable_http_url(server_url)

    init_request = {
        "jsonrpc": "2.0",
        "id": "init-1",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "playwright-script-generator", "version": "1.0.0"},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init_resp = await client.post(endpoint, json=init_request, headers=headers, timeout=30.0)
    init_resp.raise_for_status()

    session_id = init_resp.headers.get("mcp-session-id")
    init_msg = await _parse_jsonrpc_message(init_resp)
    if "error" in init_msg:
        raise RuntimeError(f"MCP initialize failed: {init_msg['error']}")

    protocol_version = (
        init_msg.get("result", {}).get("protocolVersion")
        if isinstance(init_msg.get("result"), dict)
        else None
    ) or "2025-06-18"

    session_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "mcp-protocol-version": str(protocol_version),
    }
    if session_id:
        session_headers["mcp-session-id"] = session_id

    try:
        # 通知服务端初始化完成（允许 202 Accepted）
        init_notify = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        notify_resp = await client.post(endpoint, json=init_notify, headers=session_headers, timeout=30.0)
        if notify_resp.status_code >= 400:
            notify_resp.raise_for_status()

        tool_request = {
            "jsonrpc": "2.0",
            "id": "tool-1",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": params},
        }
        tool_resp = await client.post(endpoint, json=tool_request, headers=session_headers, timeout=30.0)
        tool_resp.raise_for_status()
        tool_msg = await _parse_jsonrpc_message(tool_resp)

        if "error" in tool_msg:
            raise RuntimeError(f"MCP tool call failed: {tool_msg['error']}")

        result = tool_msg.get("result", {})
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected MCP result payload: {result}")

        if result.get("isError") is True:
            raise RuntimeError(_extract_tool_result_text(result.get("content")) or "MCP tool returned isError=true")

        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured

        text_payload = _extract_tool_result_text(result.get("content"))
        if not text_payload:
            raise RuntimeError("MCP tool response has no structuredContent or text content")

        try:
            parsed = json.loads(text_payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"MCP tool text payload is not JSON: {text_payload}") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError(f"MCP tool payload JSON is not an object: {parsed}")
        return parsed
    finally:
        if session_id:
            with suppress(Exception):
                await client.delete(endpoint, headers=session_headers, timeout=10.0)


async def call_mcp_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """调用 MCP Server 的工具（优先 REST，失败自动回退 streamable-http）。"""
    import httpx

    async with httpx.AsyncClient(follow_redirects=True) as client:
        rest_url = _build_rest_tool_url(MCP_SERVER_URL, tool_name)
        rest_error: Exception | None = None
        try:
            response = await client.post(rest_url, json=params, timeout=30.0)
            if response.status_code < 400:
                payload = response.json()
                if isinstance(payload, dict):
                    return payload
                raise RuntimeError(f"Unexpected REST MCP payload: {payload}")
            if response.status_code not in (404, 405, 406):
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            rest_error = exc

        try:
            return await _call_mcp_tool_streamable_http(client, MCP_SERVER_URL, tool_name, params)
        except Exception as stream_exc:  # noqa: BLE001
            if rest_error is not None:
                raise RuntimeError(f"REST MCP failed: {rest_error}; streamable-http MCP failed: {stream_exc}") from stream_exc
            raise


async def get_page_context(system: str, page: str | None = None) -> dict[str, Any]:
    """获取页面上下文"""
    params = {
        "system_keyword": system,
        "max_locators": 10,
        "max_fallback_pages": 2,
    }
    if page:
        params["page_keyword"] = page
    
    return await call_mcp_tool("get_page_playwright_context", params)


async def get_storage_state(system: str) -> dict[str, Any]:
    """获取存储状态"""
    return await call_mcp_tool("get_storage_state_for_session", {
        "system_name": system
    })


def _extract_navigation_plan(context: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None, bool]:
    plan = context.get("navigation_plan")
    if not isinstance(plan, dict):
        plan = {}

    raw_steps = plan.get("steps")
    steps: list[dict[str, Any]] = []
    if isinstance(raw_steps, list):
        for item in raw_steps:
            if not isinstance(item, dict):
                continue
            locator = str(item.get("playwright_locator") or "").strip()
            title = str(item.get("title") or "").strip()
            if not locator and not title:
                continue
            steps.append(
                {
                    "menu_id": str(item.get("menu_id") or ""),
                    "title": title or locator,
                    "playwright_locator": locator,
                    "is_target": bool(item.get("is_target")),
                }
            )

    route_path_raw = plan.get("route_path")
    if not route_path_raw:
        target_page = context.get("target_page") if isinstance(context.get("target_page"), dict) else {}
        route_path_raw = target_page.get("route_path")
    route_path = str(route_path_raw or "").strip() or None

    route_fallback_enabled = bool(plan.get("route_fallback_enabled", True))
    return steps[:20], route_path, route_fallback_enabled


def generate_script(context: dict, storage_state: dict | None) -> str:
    """生成 Playwright 脚本"""
    system = context.get("system", {})
    target_page = context.get("target_page", {})
    locators = context.get("locators", [])

    storage_state_payload = storage_state.get("state", {}).get("storage_state", {}) if storage_state else {}
    storage_state_json = json.dumps(storage_state_payload, ensure_ascii=False)
    storage_state_json_literal = json.dumps(storage_state_json, ensure_ascii=False)
    locator_specs = []
    for loc in locators[:5]:
        pw_loc = (loc.get("playwright_locator") or "").strip()
        desc = (
            (loc.get("usage_description") or "").strip()
            or (loc.get("nearby_text") or "").strip()
            or pw_loc
        )
        if pw_loc:
            locator_specs.append(
                {
                    "playwright_locator": pw_loc,
                    "description": desc,
                }
            )

    system_name_json = json.dumps(system.get("name", "unknown"), ensure_ascii=False)
    page_title_json = json.dumps(target_page.get("title", "unknown"), ensure_ascii=False)
    target_url_json = json.dumps(target_page.get("target_url", ""), ensure_ascii=False)
    base_url_json = json.dumps(system.get("base_url", ""), ensure_ascii=False)
    locator_specs_json = json.dumps(locator_specs, ensure_ascii=False)
    navigation_steps, route_path, route_fallback_enabled = _extract_navigation_plan(context)
    navigation_steps_json = json.dumps(navigation_steps, ensure_ascii=False)
    navigation_steps_json_literal = json.dumps(navigation_steps_json, ensure_ascii=False)
    route_path_json = json.dumps(route_path, ensure_ascii=False)
    route_fallback_enabled_json = repr(route_fallback_enabled)

    script = f'''#!/usr/bin/env python3
"""Auto-generated Playwright script for page check"""

import asyncio
import json
from playwright.async_api import async_playwright

async def _resolve_locator(page, playwright_locator):
    locator_text = (playwright_locator or "").strip()
    if not locator_text:
        raise ValueError("empty locator")
    if locator_text.startswith("page."):
        return eval(locator_text, {{"__builtins__": {{}}}}, {{"page": page}})
    if locator_text.startswith(("get_by_", "locator(", "frame_locator(")):
        return eval(f"page.{{locator_text}}", {{"__builtins__": {{}}}}, {{"page": page}})
    return page.locator(locator_text)

def _normalize_route_path(route_path):
    text = (route_path or "").strip()
    if not text:
        return ""
    if not text.startswith("/"):
        text = f"/{{text.lstrip('/')}}"
    return text

def _url_hits_target(current_url, target_url, route_path=""):
    current = str(current_url or "")
    expected_target = str(target_url or "").strip()
    if expected_target:
        expected_fragment = expected_target.split("#", 1)[-1] if "#" in expected_target else expected_target
        if expected_fragment and expected_fragment in current:
            return True

    normalized_route = _normalize_route_path(route_path)
    if not normalized_route:
        return False
    return (normalized_route in current) or (f"#{{normalized_route}}" in current)

def _route_path_from_target_url(target_url):
    text = (target_url or "").strip()
    if not text:
        return ""
    fragment = text.split("#", 1)[-1] if "#" in text else text
    if not fragment.startswith("/"):
        fragment = f"/{{fragment.lstrip('/')}}"
    return fragment

async def _click_navigation_step(page, step):
    title = str(step.get("title") or "unknown_step")
    locator_text = str(step.get("playwright_locator") or "").strip()
    if not locator_text:
        return False, title, "missing_playwright_locator"
    try:
        locator_obj = await _resolve_locator(page, locator_text)
        target = locator_obj.first
        await target.wait_for(timeout=3000, state="visible")
        await target.click(timeout=3000)
        await page.wait_for_timeout(600)
        return True, title, ""
    except Exception as exc:
        return False, title, str(exc)

async def _push_target_route_via_runtime(page, route_path, target_url):
    resolved_route = (route_path or "").strip() or _route_path_from_target_url(target_url)
    if not resolved_route:
        return ""
    try:
        method = await page.evaluate(
            """(routePath) => {{
                const app = document.querySelector('#app');
                const vue3Router = app && app.__vue_app__ && app.__vue_app__.config &&
                    app.__vue_app__.config.globalProperties && app.__vue_app__.config.globalProperties.$router;
                if (vue3Router && typeof vue3Router.push === 'function') {{
                    vue3Router.push(routePath);
                    return 'vue3_router';
                }}
                if (window.$router && typeof window.$router.push === 'function') {{
                    window.$router.push(routePath);
                    return 'window_$router';
                }}
                if (window.__VUE_ROUTER__ && typeof window.__VUE_ROUTER__.push === 'function') {{
                    window.__VUE_ROUTER__.push(routePath);
                    return 'window___VUE_ROUTER__';
                }}
                if (window.location && typeof window.location.hash === 'string') {{
                    window.location.hash = '#' + routePath;
                    return 'hash_fallback';
                }}
                return '';
            }}""",
            resolved_route,
        )
    except Exception:
        return ""
    return str(method or "")

async def _navigate_by_navigation_plan(page, navigation_steps, target_url, route_path, route_fallback_enabled):
    result = {{
        "attempted": bool(navigation_steps),
        "strategy": "mcp_navigation_plan",
        "clicked": [],
        "failed": [],
        "route_push_method": "",
        "current_url": page.url,
        "url_matched": _url_hits_target(page.url, target_url, route_path),
    }}
    for step in navigation_steps or []:
        clicked, title, reason = await _click_navigation_step(page, step)
        if clicked:
            result["clicked"].append(title)
        else:
            result["failed"].append({{"title": title, "reason": reason}})
        if _url_hits_target(page.url, target_url, route_path):
            break

    if (not _url_hits_target(page.url, target_url, route_path)) and route_fallback_enabled:
        result["route_push_method"] = await _push_target_route_via_runtime(page, route_path, target_url)
        if result["route_push_method"]:
            await page.wait_for_timeout(1200)

    await page.wait_for_timeout(1200)
    result["current_url"] = page.url
    result["url_matched"] = _url_hits_target(page.url, target_url, route_path)
    return result

async def _run_once(
    context,
    base_url,
    target_url,
    locator_specs,
    navigation_steps,
    route_path,
    route_fallback_enabled,
    attempt_no,
):
    page = await context.new_page()
    start_time = asyncio.get_event_loop().time()
    await page.goto(base_url, wait_until="domcontentloaded")
    response = None
    if target_url:
        response = await page.goto(target_url, wait_until="networkidle")
    else:
        await page.wait_for_timeout(500)
    load_time = (asyncio.get_event_loop().time() - start_time) * 1000

    current_url = page.url
    redirect_to_login = "/login" in current_url or "redirect=" in current_url
    url_matched = _url_hits_target(current_url, target_url, route_path)

    attempt = {{
        "attempt_no": attempt_no,
        "current_url": current_url,
        "http_status": response.status if response else None,
        "load_time_ms": round(load_time, 2),
        "redirect_to_login": redirect_to_login,
        "url_matched": url_matched,
        "elements": {{}},
        "element_found_count": 0,
        "errors": [],
        "screenshot": None,
        "menu_fallback": None,
    }}

    if redirect_to_login:
        attempt["errors"].append("redirected_to_login")
        return attempt

    if not url_matched and (navigation_steps or route_fallback_enabled):
        menu_fallback = await _navigate_by_navigation_plan(
            page,
            navigation_steps,
            target_url,
            route_path,
            route_fallback_enabled,
        )
        attempt["menu_fallback"] = menu_fallback
        current_url = page.url
        redirect_to_login = "/login" in current_url or "redirect=" in current_url
        url_matched = _url_hits_target(current_url, target_url, route_path)
        attempt["current_url"] = current_url
        attempt["redirect_to_login"] = redirect_to_login
        attempt["url_matched"] = url_matched
        if redirect_to_login:
            attempt["errors"].append("redirected_to_login_after_menu_fallback")
            return attempt

    for spec in locator_specs:
        desc = spec.get("description") or spec.get("playwright_locator") or "unknown locator"
        pw_loc = spec.get("playwright_locator", "")
        try:
            locator_obj = await _resolve_locator(page, pw_loc)
            await locator_obj.first.wait_for(timeout=5000, state="visible")
            attempt["elements"][desc] = "found"
            attempt["element_found_count"] += 1
        except Exception as exc:
            attempt["elements"][desc] = f"not found: {{str(exc)}}"

    screenshot_path = f"output/screenshot_attempt{{attempt_no}}_{{int(asyncio.get_event_loop().time() * 1000)}}.png"
    await page.screenshot(path=screenshot_path, full_page=True)
    attempt["screenshot"] = screenshot_path
    return attempt

async def check_page():
    results = {{
        "system": {system_name_json},
        "page": {page_title_json},
        "url": {target_url_json},
        "base_url": {base_url_json},
        "timestamp": "{datetime.now().isoformat()}",
        "status": "unknown",
        "http_status": None,
        "load_time_ms": None,
        "current_url": None,
        "url_matched": False,
        "redirect_to_login": False,
        "element_found_count": 0,
        "attempts": [],
        "elements": {{}},
        "errors": []
    }}

    target_url = results["url"]
    base_url = results["base_url"]
    locator_specs = {locator_specs_json}
    navigation_steps = json.loads({navigation_steps_json_literal})
    route_path = {route_path_json}
    route_fallback_enabled = {route_fallback_enabled_json}

    if not base_url:
        results["status"] = "error"
        results["errors"].append("missing_base_url")
        return results
    if not target_url and not route_path and not navigation_steps:
        results["status"] = "error"
        results["errors"].append("missing_target_or_route")
        return results

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # 使用存储状态创建上下文
        storage_state = json.loads({storage_state_json_literal})

        try:
            context = await browser.new_context(storage_state=storage_state if storage_state else None)
            first_attempt = await _run_once(
                context,
                base_url,
                target_url,
                locator_specs,
                navigation_steps,
                route_path,
                route_fallback_enabled,
                1,
            )
            await context.close()

            attempts = [first_attempt]
            final_attempt = first_attempt

            # 首次重定向到登录页时，必须同 storage_state 重试一次
            if first_attempt["redirect_to_login"]:
                retry_context = await browser.new_context(storage_state=storage_state if storage_state else None)
                second_attempt = await _run_once(
                    retry_context,
                    base_url,
                    target_url,
                    locator_specs,
                    navigation_steps,
                    route_path,
                    route_fallback_enabled,
                    2,
                )
                await retry_context.close()
                attempts.append(second_attempt)
                final_attempt = second_attempt

            results["attempts"] = attempts
            results["http_status"] = final_attempt["http_status"]
            results["load_time_ms"] = final_attempt["load_time_ms"]
            results["current_url"] = final_attempt["current_url"]
            results["url_matched"] = final_attempt["url_matched"]
            results["redirect_to_login"] = final_attempt["redirect_to_login"]
            results["elements"] = final_attempt["elements"]
            results["element_found_count"] = final_attempt["element_found_count"]
            results["menu_fallback"] = final_attempt.get("menu_fallback")
            if final_attempt.get("screenshot"):
                results["screenshot"] = final_attempt["screenshot"]

            if final_attempt["redirect_to_login"]:
                results["status"] = "auth_invalid"
                results["errors"].append("session_invalid_or_expired")
            elif final_attempt["url_matched"]:
                if final_attempt["element_found_count"] > 0:
                    results["status"] = "ok"
                else:
                    results["status"] = "low_confidence"
                    results["errors"].append("no_key_elements_found")
            else:
                results["status"] = "error"
                if not final_attempt["url_matched"]:
                    results["errors"].append("url_not_matched")

            results["errors"].extend(final_attempt.get("errors", []))
        except Exception as e:
            results["status"] = "error"
            results["errors"].append(str(e))
        finally:
            await browser.close()

    return results

if __name__ == "__main__":
    result = asyncio.run(check_page())
    print(json.dumps(result, ensure_ascii=False, indent=2))
'''
    return script


async def run_generated_script(script_content: str) -> dict[str, Any]:
    """执行生成的脚本"""
    # 创建临时脚本文件
    temp_script = Path("output/temp_check_script.py")
    temp_script.parent.mkdir(parents=True, exist_ok=True)
    temp_script.write_text(script_content, encoding="utf-8")
    
    try:
        # 执行脚本
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(temp_script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            return {
                "status": "execution_error",
                "error": stderr.decode("utf-8"),
                "stdout": stdout.decode("utf-8")
            }
        
        return json.loads(stdout.decode("utf-8"))
    finally:
        # 清理临时文件
        if temp_script.exists():
            temp_script.unlink()


def format_result(result: dict[str, Any]) -> str:
    """格式化结果为 Markdown"""
    status = result.get("status")
    status_emoji = "✅" if status == "ok" else ("⚠️" if status == "low_confidence" else "❌")
    
    lines = [
        f"## 页面检测结果 {status_emoji}",
        "",
        f"**系统**: {result.get('system', 'unknown')}",
        f"**页面**: {result.get('page', 'unknown')}",
        f"**URL**: {result.get('url', 'N/A')}",
        f"**当前URL**: {result.get('current_url', 'N/A')}",
        "",
        "### 检测结果",
        f"- 页面状态: {'正常' if status in {'ok', 'low_confidence'} else '异常'}",
        f"- 会话状态: {'有效' if status != 'auth_invalid' else '失效/过期'}",
        f"- HTTP 状态: {result.get('http_status', 'N/A')}",
        f"- 加载时间: {result.get('load_time_ms', 'N/A')} ms",
        f"- URL 命中目标: {'是' if result.get('url_matched') else '否'}",
        f"- 关键元素命中数: {result.get('element_found_count', 0)}",
        "",
        "### 元素检测",
    ]
    
    elements = result.get("elements", {})
    if elements:
        for name, status in elements.items():
            emoji = "✅" if status == "found" else "❌"
            lines.append(f"- {emoji} {name}: {status}")
    else:
        lines.append("- 无元素检测数据")
    
    errors = result.get("errors", [])
    if errors:
        lines.extend(["", "### 错误信息"])
        for error in errors:
            lines.append(f"- ⚠️ {error}")

    menu_fallback = result.get("menu_fallback")
    if isinstance(menu_fallback, dict) and menu_fallback.get("attempted"):
        lines.extend(["", "### 菜单回退"])
        lines.append(f"- 点击成功: {len(menu_fallback.get('clicked') or [])}")
        lines.append(f"- 点击失败: {len(menu_fallback.get('failed') or [])}")
        if menu_fallback.get("clicked"):
            lines.append(f"- 成功节点: {', '.join(menu_fallback.get('clicked') or [])}")
        if menu_fallback.get("failed"):
            failed_labels: list[str] = []
            for item in menu_fallback.get("failed") or []:
                if isinstance(item, dict):
                    title = str(item.get("title") or "unknown")
                    reason = str(item.get("reason") or "")
                    failed_labels.append(f"{title} ({reason})" if reason else title)
                else:
                    failed_labels.append(str(item))
            lines.append(f"- 失败节点: {', '.join(failed_labels)}")
        if menu_fallback.get("route_push_method"):
            lines.append(f"- 路由回退: {menu_fallback.get('route_push_method')}")
    
    if result.get("screenshot"):
        lines.extend(["", f"### 截图", f"![页面截图]({result['screenshot']})"])
    
    return "\n".join(lines)


async def main():
    args = parse_args()
    if not args.system:
        print("❌ 错误: 请通过 --system 指定系统名称")
        return 1
    
    resolved_page = extract_page_keyword(args.system, args.page)
    if args.page and resolved_page and resolved_page != args.page:
        print(f"🧠 页面关键词抽取: {args.page} -> {resolved_page}")

    print(f"🔍 正在查询系统: {args.system}, 页面: {resolved_page or '默认'}")

    # 1. 获取页面上下文
    context = await get_page_context(args.system, resolved_page)
    
    if context.get("status") == "system_not_found":
        print("❌ 错误: 暂未找到对应系统数据")
        print("请先配置系统信息或检查系统名称是否正确")
        return 1
    
    if context.get("status") == "page_not_found":
        print("❌ 错误: 未找到该页面")
        fallback = context.get("fallback_pages", [])
        if fallback:
            print("\n可用页面:")
            for page in fallback:
                print(f"  - {page.get('title')} ({page.get('route_path')})")
        return 1
    
    if context.get("status") == "need_recrawl":
        print("⚠️ 警告: 数据已过期，建议触发重新采集")
        return 1
    
    print(f"✅ 找到页面: {context.get('target_page', {}).get('title')}")
    print(f"   URL: {context.get('target_page', {}).get('target_url')}")
    
    # 2. 获取存储状态
    print("\n🔑 正在获取会话状态...")
    storage_state = await get_storage_state(args.system)
    
    if storage_state.get("status") != "ok":
        print(f"❌ 错误: 会话状态不可用 ({storage_state.get('status')})")
        print("请先确认系统名称并重新认证会话状态")
        return 1
    if not storage_state.get("is_valid", True):
        print("❌ 错误: 会话状态已过期，请重新认证")
        return 1
    else:
        print("✅ 会话状态获取成功")
    
    # 3. 生成脚本
    print("\n📝 正在生成 Playwright 脚本...")
    script = generate_script(context, storage_state)
    
    # 保存脚本
    script_path = Path("output/generated_check_script.py")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    print(f"✅ 脚本已保存: {script_path}")
    
    if args.dry_run:
        print("\n🛑 干运行模式，跳过执行")
        print(f"脚本内容预览:\n{'='*50}")
        print(script[:500] + "..." if len(script) > 500 else script)
        return 0
    
    # 4. 执行脚本
    print("\n🚀 正在执行检测脚本...")
    result = await run_generated_script(script)
    
    # 5. 输出结果
    print("\n" + "="*50)
    print(format_result(result))
    
    # 保存结果
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n💾 结果已保存: {output_path}")
    
    return 0 if result.get("status") in {"ok", "low_confidence"} else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
