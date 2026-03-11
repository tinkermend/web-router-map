#!/usr/bin/env python3
"""
Collect menu map and page elements by reusing existing browser session state.

Flow:
1) Load saved state payload (simulating DB-loaded storage state)
2) Validate state; if invalid, exit for auth refresh workflow
3) Launch browser with state and navigate home
4) Detect framework and try console route extraction
5) Fallback to DOM menu traversal/extraction
6) Build URL queue, crawl pages and modal elements
7) Save JSON output for feasibility verification
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request as urllib_request
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DEFAULT_STORAGE_STATE_PATH = "output/playwright/ele-storage-state.json"
DEFAULT_AUTH_INPUT_PATH = "output/playwright/ele-auth.json"
DEFAULT_OUTPUT_PATH = "output/playwright/ele-menu-map.json"
DEFAULT_SCREENSHOT_DIR = "output/playwright/screenshots/menu-map"
DEFAULT_LOGIN_URL = "https://ele.vben.pro/#/auth/login"
DEFAULT_HOME_URL = "https://ele.vben.pro/#/analytics"

DEFAULT_MODAL_SELECTORS = [
    ".ant-modal",
    ".n-modal-container",
    ".el-dialog",
    "[role='dialog']",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect menu map and page elements by reusing saved session state.",
    )
    parser.add_argument(
        "--storage-state",
        default=DEFAULT_STORAGE_STATE_PATH,
        help="Path to Playwright storage state JSON.",
    )
    parser.add_argument(
        "--auth-input",
        default=DEFAULT_AUTH_INPUT_PATH,
        help="Path to auth payload JSON (cookies/local_storage/session_storage/request_headers).",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_PATH,
        help="Output JSON file path for menu map payload.",
    )
    parser.add_argument(
        "--screenshot-dir",
        default=DEFAULT_SCREENSHOT_DIR,
        help="Directory for page screenshots.",
    )
    parser.add_argument(
        "--home-url",
        default="",
        help="System home URL. Empty means auto from auth payload current_url.",
    )
    parser.add_argument(
        "--menu-selector",
        default="",
        help="Optional explicit menu root selector for DOM extraction.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Max number of page URLs to crawl for element collection.",
    )
    parser.add_argument(
        "--max-elements-per-page",
        type=int,
        default=180,
        help="Max number of visible interactive elements to record per page/container.",
    )
    parser.add_argument(
        "--max-modal-triggers",
        type=int,
        default=8,
        help="Max trigger buttons to try for modal collection per page.",
    )
    parser.add_argument(
        "--expand-rounds",
        type=int,
        default=6,
        help="Menu expand attempts for DOM crawling.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=45_000,
        help="Playwright timeout in milliseconds.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode.",
    )
    return parser.parse_args()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to load JSON from {path}") from exc


def _origin_of(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _safe_slug(value: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", value).strip("_")
    if not raw:
        raw = "node"
    return raw[:50].lower()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _to_route_path(url_or_path: str) -> str | None:
    if not url_or_path:
        return None
    if url_or_path.startswith("/"):
        return url_or_path
    if "#/" in url_or_path:
        return "/" + url_or_path.split("#/", 1)[1].lstrip("/")
    try:
        parsed = urlsplit(url_or_path)
        if parsed.fragment.startswith("/"):
            return parsed.fragment
    except Exception:
        return None
    return None


def _route_path_from_menu_hint(value: Any) -> str | None:
    text = _normalize_text(str(value or ""))
    if not text:
        return None
    lower = text.lower()
    if lower.startswith("javascript:") or lower.startswith("mailto:") or lower.startswith("tel:"):
        return None

    if text.startswith("http://") or text.startswith("https://"):
        route_path = _to_route_path(text)
        if route_path:
            return route_path
        parsed = urlsplit(text)
        if parsed.path and parsed.path != "/":
            return parsed.path
        return None

    if text.startswith("#/"):
        return "/" + text[2:].lstrip("/")
    if text.startswith("/"):
        return text

    if re.fullmatch(r"\d+(?:[-_.:]\d+)*", text):
        return None
    if text.startswith("?") or text.startswith("#"):
        return None

    cleaned = text[2:] if text.startswith("./") else text
    cleaned = cleaned.lstrip("/")
    if not cleaned or cleaned.startswith("../"):
        return None
    if re.search(r"\s", cleaned):
        return None
    return f"/{cleaned}"


def _build_url_from_route(origin: str, home_url: str, route_path: str | None) -> str | None:
    if not route_path:
        return None
    if route_path.startswith("http://") or route_path.startswith("https://"):
        return route_path
    normalized = route_path if route_path.startswith("/") else f"/{route_path}"
    if "#/" in home_url:
        return f"{origin}/#{normalized}"
    return urljoin(origin, normalized)


def _is_state_valid(page, validate_url: str, timeout_ms: int) -> bool:
    try:
        page.goto(validate_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1200)
    except PlaywrightTimeoutError:
        return False

    url = (page.url or "").lower()
    if "#/auth/login" in url or "/auth/login" in url or "#/login" in url or "/login" in url:
        return False
    return True


def _apply_saved_web_storage(page, origin: str, local_storage: dict[str, str], session_storage: dict[str, str], timeout_ms: int) -> None:
    try:
        page.goto(origin, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return

    if local_storage:
        page.evaluate(
            """(items) => {
                for (const [k, v] of Object.entries(items || {})) {
                    localStorage.setItem(k, String(v));
                }
            }""",
            local_storage,
        )

    if session_storage:
        page.evaluate(
            """(items) => {
                for (const [k, v] of Object.entries(items || {})) {
                    sessionStorage.setItem(k, String(v));
                }
            }""",
            session_storage,
        )


def _detect_framework(page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
            const out = { framework_type: 'unknown', evidence: [] };
            try {
                if (window.__VUE__ || window.__VUE_DEVTOOLS_GLOBAL_HOOK__ || document.querySelector('[data-v-app]')) {
                    out.framework_type = 'vue3';
                    out.evidence.push('vue markers');
                }
                if (document.querySelector('[data-reactroot]') || window.__REACT_DEVTOOLS_GLOBAL_HOOK__) {
                    if (out.framework_type === 'unknown') out.framework_type = 'react';
                    out.evidence.push('react markers');
                }
                const hasNg = !!document.querySelector('[ng-version]');
                if (hasNg && out.framework_type === 'unknown') {
                    out.framework_type = 'unknown';
                    out.evidence.push('angular markers');
                }
            } catch (e) {
                out.error = String(e);
            }
            return out;
        }"""
    )


def _extract_routes_from_console(page, origin: str, home_url: str) -> dict[str, Any]:
    data = page.evaluate(
        """() => {
            const out = { success: false, source: 'none', routes: [], error: null };
            const seen = new Set();

            function normalizeRoute(r) {
                if (!r || typeof r !== 'object') return null;
                const path = typeof r.path === 'string' ? r.path : '';
                const name = typeof r.name === 'string' ? r.name : null;
                let title = null;
                if (r.meta && typeof r.meta === 'object') {
                    if (typeof r.meta.title === 'string') title = r.meta.title;
                }
                if (!path && !name && !title) return null;
                const key = `${path}|${name || ''}|${title || ''}`;
                if (seen.has(key)) return null;
                seen.add(key);
                return {
                    route_path: path || null,
                    route_name: name,
                    title: title || name || path || 'route',
                };
            }

            function pushRoutes(routes, source) {
                if (!Array.isArray(routes)) return;
                for (const r of routes) {
                    const n = normalizeRoute(r);
                    if (n) out.routes.push(n);
                }
                if (out.routes.length > 0) {
                    out.success = true;
                    out.source = source;
                }
            }

            try {
                if (window.__VUE_ROUTER__ && typeof window.__VUE_ROUTER__.getRoutes === 'function') {
                    pushRoutes(window.__VUE_ROUTER__.getRoutes(), 'window.__VUE_ROUTER__');
                }

                if (!out.success) {
                    const keys = Object.keys(window).slice(0, 300);
                    for (const k of keys) {
                        const obj = window[k];
                        if (!obj || typeof obj !== 'object') continue;
                        if (typeof obj.getRoutes === 'function' && (obj.currentRoute || obj.options || obj.matcher)) {
                            try {
                                pushRoutes(obj.getRoutes(), `window.${k}`);
                                if (out.success) break;
                            } catch (_) {}
                        }
                    }
                }
            } catch (e) {
                out.error = String(e);
            }

            return out;
        }"""
    )

    for route in data.get("routes", []):
        route_path = route.get("route_path")
        target_url = _build_url_from_route(origin, home_url, route_path)
        route["target_url"] = target_url
        route["node_type"] = "page"
    return data


def _extract_routes_from_bundle(page, origin: str, home_url: str) -> dict[str, Any]:
    script_urls = page.evaluate(
        """() => {
            const scripts = Array.from(document.querySelectorAll('script[src]'))
                .map((el) => el.src)
                .filter(Boolean);
            const preloads = Array.from(document.querySelectorAll('link[rel="modulepreload"][href]'))
                .map((el) => new URL(el.getAttribute('href'), location.origin).toString())
                .filter(Boolean);
            return Array.from(new Set([...scripts, ...preloads]));
        }"""
    )

    allow_heads = {
        "analytics",
        "dashboard",
        "workspace",
        "demos",
        "demo",
        "system",
        "permission",
        "form",
        "table",
        "list",
        "features",
        "feature",
        "auth",
    }
    deny_exact = {"/auth", "/auth/login", "/auth/logout", "/auth/refresh", "/auth/codes"}

    routes: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for script_url in script_urls:
        if "/js/" not in script_url and "/jse/" not in script_url:
            continue
        try:
            content = urllib_request.urlopen(script_url, timeout=20).read().decode("utf-8", "ignore")
        except Exception as exc:  # pragma: no cover - network dependent
            errors.append(f"{script_url}: {exc}")
            continue

        candidates = set(
            re.findall(
                r"/(?:[a-zA-Z][a-zA-Z0-9_-]{1,20})(?:/[a-zA-Z0-9_-]{1,30}){0,4}",
                content,
            )
        )
        for path in candidates:
            head = path.split("/")[1] if path.startswith("/") else ""
            if head not in allow_heads:
                continue
            if path in deny_exact:
                continue
            if path.startswith("/auth/"):
                continue
            title = path.strip("/").replace("/", " > ") or "route"
            routes[path] = {
                "route_path": path,
                "route_name": path.strip("/").replace("/", "_") or "root",
                "title": title,
                "target_url": _build_url_from_route(origin, home_url, path),
                "node_type": "page",
            }

    return {
        "success": len(routes) > 0,
        "source": "bundle_scripts",
        "routes": sorted(routes.values(), key=lambda x: x["route_path"]),
        "errors": errors,
    }


def _expand_menu_dom(page, rounds: int) -> int:
    total_clicks = 0
    for _ in range(rounds):
        clicks = page.evaluate(
            """() => {
                let clicked = 0;
                const dispatched = new WeakSet();
                const clickNode = (el) => {
                    if (!el || dispatched.has(el)) return;
                    dispatched.add(el);
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    clicked += 1;
                };

                // Ant Design style menus
                document.querySelectorAll('.ant-menu-submenu:not(.ant-menu-submenu-open) > .ant-menu-submenu-title')
                    .forEach((el) => clickNode(el));

                // Generic aria-expanded menuitem
                document.querySelectorAll('[role=\"menuitem\"][aria-expanded=\"false\"]')
                    .forEach((el) => {
                        const hasPopup = el.getAttribute('aria-haspopup') === 'true';
                        if (hasPopup) clickNode(el);
                    });

                return clicked;
            }"""
        )
        if not isinstance(clicks, int) or clicks <= 0:
            break
        total_clicks += clicks
        page.wait_for_timeout(250)
    return total_clicks


def _extract_menu_nodes_from_dom(page, origin: str, menu_selector: str) -> dict[str, Any]:
    return page.evaluate(
        """([origin, explicitSelector]) => {
            const result = {
                success: false,
                root_selector: null,
                nodes: [],
                error: null,
            };

            const candidates = [];
            if (explicitSelector) candidates.push(explicitSelector);
            candidates.push(
                'aside nav',
                'aside .ant-menu',
                'aside .el-menu',
                'aside',
                '.ant-layout-sider .ant-menu',
                '.vben-admin-layout .ant-menu',
                '.el-aside .el-menu',
                '.el-menu',
                '[role=\"navigation\"]',
                'nav'
            );

            const normalizeText = (text) => (text || '').replace(/\\s+/g, ' ').trim();

            const cssPath = (el) => {
                if (!(el instanceof Element)) return '';
                const parts = [];
                let cur = el;
                while (cur && cur.nodeType === 1 && parts.length < 6) {
                    let part = cur.tagName.toLowerCase();
                    if (cur.id) {
                        part += `#${cur.id}`;
                        parts.unshift(part);
                        break;
                    }
                    if (cur.classList && cur.classList.length > 0) {
                        part += '.' + Array.from(cur.classList).slice(0, 2).join('.');
                    }
                    const parent = cur.parentElement;
                    if (parent) {
                        const same = Array.from(parent.children).filter((n) => n.tagName === cur.tagName);
                        if (same.length > 1) {
                            part += `:nth-of-type(${same.indexOf(cur) + 1})`;
                        }
                    }
                    parts.unshift(part);
                    cur = cur.parentElement;
                }
                return parts.join(' > ');
            };

            const visible = (el) => {
                if (!(el instanceof Element)) return false;
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return false;
                const style = window.getComputedStyle(el);
                return style.visibility !== 'hidden' && style.display !== 'none';
            };

            const pickTitle = (el) => {
                const selectors = [
                    '.ant-menu-title-content',
                    '.ant-menu-item-group-title',
                    '.ant-menu-submenu-title',
                    '[role=\"menuitem\"]',
                    'a',
                    'span',
                ];
                for (const sel of selectors) {
                    const node = el.querySelector(sel);
                    const text = normalizeText(node ? node.textContent : '');
                    if (text) return text;
                }
                return normalizeText(el.textContent || '');
            };

            const directChildLis = (container) => {
                const lis = Array.from(container.children || []).filter((n) => n.tagName === 'LI');
                if (lis.length > 0) return lis;
                return Array.from(container.querySelectorAll(':scope > ul > li'));
            };

            for (const sel of candidates) {
                const root = document.querySelector(sel);
                if (!root) continue;
                if (!result.root_selector) result.root_selector = sel;

                const topContainer = root.matches('ul') ? root : (root.querySelector('ul') || root);
                const nodes = [];

                const walk = (container, parentKey, depth, pathIndexes, breadcrumb) => {
                    const items = directChildLis(container);
                    items.forEach((li, idx) => {
                        if (!visible(li)) return;
                        const title = pickTitle(li);
                        if (!title) return;
                        const hrefNode = li.querySelector('a[href]');
                        const href = hrefNode ? hrefNode.getAttribute('href') || '' : '';
                        const hasChildren = li.classList.contains('ant-menu-submenu') || !!li.querySelector(':scope > ul > li');
                        const nodeType = hasChildren ? 'folder' : 'page';

                        let routePath = null;
                        let targetUrl = null;
                        if (href) {
                            if (href.startsWith('#/')) routePath = '/' + href.slice(2);
                            else if (href.startsWith('/')) routePath = href;
                            else if (href.startsWith('http://') || href.startsWith('https://')) {
                                targetUrl = href;
                                const hashIdx = href.indexOf('#/');
                                if (hashIdx >= 0) routePath = '/' + href.slice(hashIdx + 2);
                            }
                        }
                        const routeHint = (li.getAttribute('index') || li.getAttribute('data-index') || '').trim();
                        if (!routePath) {
                            if (routeHint.startsWith('#/')) routePath = '/' + routeHint.slice(2);
                            else if (routeHint.startsWith('/')) routePath = routeHint;
                        }

                        if (!targetUrl && routePath) {
                            targetUrl = `${origin}/#${routePath}`;
                        }

                        const nextPath = pathIndexes.concat(idx);
                        const nodeKey = nextPath.join('.');
                        const nextBreadcrumb = breadcrumb.concat(title);
                        const textBreadcrumb = nextBreadcrumb.join(' > ');
                        const roleNode = li.querySelector('[role=\"menuitem\"]');
                        const locatorName = title.replace(/'/g, \"\\\\'\");
                        const playwrightLocator = roleNode
                            ? `get_by_role('menuitem', name='${locatorName}')`
                            : `get_by_text('${locatorName}')`;

                        nodes.push({
                            node_id: nodeKey,
                            parent_id: parentKey,
                            title,
                            text_breadcrumb: textBreadcrumb,
                            menu_order: idx,
                            menu_level: depth,
                            path_indexes: nextPath,
                            node_type: nodeType,
                            target_url: targetUrl,
                            route_path: routePath,
                            route_hint: routeHint || null,
                            route_name: null,
                            playwright_locator: playwrightLocator,
                            is_group: nodeType === 'folder',
                            is_external: !!targetUrl && !targetUrl.startsWith(origin),
                            is_visible: true,
                            dom_css_path: cssPath(li),
                            source: 'dom',
                        });

                        const childContainer = li.querySelector(':scope > ul');
                        if (childContainer) {
                            walk(childContainer, nodeKey, depth + 1, nextPath, nextBreadcrumb);
                        }
                    });
                };

                walk(topContainer, null, 1, [], []);
                if (nodes.length > 0) {
                    result.nodes = nodes;
                    result.success = true;
                    result.root_selector = sel;
                    return result;
                }
            }
            return result;
        }""",
        [origin, menu_selector],
    )


def _extract_elements(page, root_selector: str | None, limit: int) -> list[dict[str, Any]]:
    return page.evaluate(
        """([rootSelector, limit]) => {
            const root = rootSelector ? document.querySelector(rootSelector) : document.body;
            if (!root) return [];
            const selectors = [
                'button',
                'a',
                'input',
                'select',
                'textarea',
                '[role=\"button\"]',
                '[role=\"link\"]',
                '[role=\"menuitem\"]',
                '[contenteditable=\"true\"]',
                '[tabindex]',
            ];

            const normalize = (text) => (text || '').replace(/\\s+/g, ' ').trim();
            const visible = (el) => {
                if (!(el instanceof Element)) return false;
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
            };

            const cssPath = (el) => {
                const parts = [];
                let cur = el;
                while (cur && cur.nodeType === 1 && parts.length < 7) {
                    let part = cur.tagName.toLowerCase();
                    if (cur.id) {
                        part += `#${cur.id}`;
                        parts.unshift(part);
                        break;
                    }
                    if (cur.classList && cur.classList.length) {
                        part += '.' + Array.from(cur.classList).slice(0, 2).join('.');
                    }
                    const parent = cur.parentElement;
                    if (parent) {
                        const same = Array.from(parent.children).filter((x) => x.tagName === cur.tagName);
                        if (same.length > 1) part += `:nth-of-type(${same.indexOf(cur) + 1})`;
                    }
                    parts.unshift(part);
                    cur = cur.parentElement;
                }
                return parts.join(' > ');
            };

            const classify = (el) => {
                const tag = el.tagName.toLowerCase();
                const role = (el.getAttribute('role') || '').toLowerCase();
                const type = (el.getAttribute('type') || '').toLowerCase();
                if (tag === 'a' || role === 'link' || role === 'menuitem') return 'nav_link';
                if (tag === 'input' || tag === 'select' || tag === 'textarea') return 'form_input';
                if (tag === 'button' || role === 'button' || type === 'button' || type === 'submit') return 'action_btn';
                return 'interactive';
            };

            const nearbyText = (el) => {
                const aria = normalize(el.getAttribute('aria-label') || '');
                if (aria) return aria;
                if (el.id) {
                    const label = document.querySelector(`label[for=\"${el.id}\"]`);
                    if (label) {
                        const t = normalize(label.textContent || '');
                        if (t) return t;
                    }
                }
                const parent = el.parentElement;
                if (parent) {
                    const t = normalize(parent.textContent || '');
                    if (t) return t.slice(0, 120);
                }
                return '';
            };

            const all = Array.from(root.querySelectorAll(selectors.join(',')));
            const seen = new Set();
            const out = [];
            for (const el of all) {
                if (out.length >= limit) break;
                if (!visible(el)) continue;

                const path = cssPath(el);
                if (!path || seen.has(path)) continue;
                seen.add(path);

                const text = normalize(el.innerText || el.textContent || '');
                const attrs = {
                    id: el.getAttribute('id') || null,
                    class: el.getAttribute('class') || null,
                    name: el.getAttribute('name') || null,
                    placeholder: el.getAttribute('placeholder') || null,
                    'aria-label': el.getAttribute('aria-label') || null,
                    'data-testid': el.getAttribute('data-testid') || null,
                    role: el.getAttribute('role') || null,
                    type: el.getAttribute('type') || null,
                };
                const role = attrs.role || '';
                const locatorText = (text || attrs['aria-label'] || attrs.placeholder || '').replace(/'/g, \"\\\\'\");

                const strategies = {};
                if (attrs['data-testid']) strategies.priority_1 = `[data-testid=\"${attrs['data-testid']}\"]`;
                else if (attrs.id) strategies.priority_1 = `#${attrs.id}`;
                if (role && locatorText) strategies.priority_2 = `get_by_role('${role}', name='${locatorText}')`;
                if (locatorText) strategies.priority_3 = `get_by_text('${locatorText}')`;
                strategies.priority_4 = path;

                const recommended = strategies.priority_1 || strategies.priority_2 || strategies.priority_3 || path;
                const rect = el.getBoundingClientRect();
                out.push({
                    tag_name: el.tagName.toLowerCase(),
                    element_type: classify(el),
                    text_content: text || null,
                    locators: {
                        strategies,
                        attributes: attrs,
                    },
                    playwright_locator: recommended,
                    nearby_text: nearbyText(el) || null,
                    usage_description: null,
                    bounding_box: {
                        x: rect.x,
                        y: rect.y,
                        width: rect.width,
                        height: rect.height,
                    },
                    dom_css_path: path,
                });
            }
            return out;
        }""",
        [root_selector, limit],
    )


def _infer_locator_tier_and_score(element: dict[str, Any]) -> tuple[str, float]:
    locator = str(element.get("playwright_locator") or "")
    locators = element.get("locators") or {}
    attrs = locators.get("attributes") or {}

    if attrs.get("data-testid"):
        return "data_testid", 0.98
    if attrs.get("id") and locator.startswith("#"):
        return "id", 0.94
    if locator.startswith("[data-testid"):
        return "data_testid", 0.96
    if locator.startswith("[") and attrs.get("id"):
        return "attr", 0.88
    if locator.startswith("get_by_role("):
        return "role", 0.84
    if locator.startswith("get_by_text("):
        return "text", 0.72
    if "nth-of-type" in locator or " > " in locator:
        return "css_path", 0.42
    if locator.startswith("#"):
        return "css_id", 0.86
    if locator:
        return "other", 0.58
    return "none", 0.0


def _is_global_chrome_element(element: dict[str, Any]) -> bool:
    text = _normalize_text(str(element.get("text_content") or ""))
    nearby = _normalize_text(str(element.get("nearby_text") or ""))
    css_path = str(element.get("dom_css_path") or "")
    locator = str(element.get("playwright_locator") or "")
    combined = f"{css_path} {locator} {nearby} {text}".lower()

    chrome_tokens = (
        "header",
        "aside",
        "sidebar",
        "tabbar",
        "breadcrumb",
        "vben-menu",
        "reka-dropdown",
        "theme",
        "_scroll__fixed_",
        "top-0",
    )
    content_tokens = ("main", "content", "table", "form", "modal", "drawer", "card", "panel")
    if any(token in combined for token in chrome_tokens) and not any(
        token in combined for token in content_tokens
    ):
        return True

    # Common shell-level nav summary text block
    if "概览" in text and "分析页" in text and len(text) > 12:
        return True

    # Menu/sidebar navigation links are useful for menu mapping, but noisy for page action context.
    if element.get("element_type") == "nav_link" and (
        "menu" in combined or "sidebar" in combined or "aside" in combined
    ):
        return True

    return False


def _is_business_useful_element(element: dict[str, Any]) -> bool:
    if element.get("is_global_chrome"):
        return False

    element_type = element.get("element_type")
    score = float(element.get("stability_score") or 0.0)
    text = _normalize_text(str(element.get("text_content") or ""))

    if element_type in {"action_btn", "form_input"}:
        return True
    if element_type == "nav_link":
        return bool(text) and score >= 0.7 and len(text) <= 32
    if element_type == "interactive":
        return bool(text) and score >= 0.85
    return False


def _enrich_and_filter_elements(elements: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    enriched: list[dict[str, Any]] = []
    for raw in elements:
        item = dict(raw)
        tier, score = _infer_locator_tier_and_score(item)
        item["locator_tier"] = tier
        item["stability_score"] = round(score, 3)
        item["is_global_chrome"] = _is_global_chrome_element(item)
        if _is_business_useful_element(item):
            enriched.append(item)

    # Deduplicate by locator, keeping stronger candidates.
    dedup: dict[str, dict[str, Any]] = {}
    for item in enriched:
        locator = item.get("playwright_locator") or item.get("dom_css_path") or ""
        if not locator:
            continue
        current = dedup.get(locator)
        if current is None or float(item.get("stability_score") or 0.0) > float(
            current.get("stability_score") or 0.0
        ):
            dedup[locator] = item

    filtered = sorted(
        dedup.values(),
        key=lambda x: (
            0 if x.get("element_type") == "action_btn" else 1,
            -float(x.get("stability_score") or 0.0),
            _normalize_text(str(x.get("text_content") or "")),
        ),
    )
    dropped = max(0, len(elements) - len(filtered))
    return filtered, dropped


def _extract_modal_selector(page, marker: str) -> str | None:
    return page.evaluate(
        """([selectors, marker]) => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden';
            };

            for (const sel of selectors) {
                const nodes = Array.from(document.querySelectorAll(sel));
                for (const node of nodes) {
                    if (!visible(node)) continue;
                    node.setAttribute('data-crawl-modal-marker', marker);
                    return `[data-crawl-modal-marker=\"${marker}\"]`;
                }
            }
            return null;
        }""",
        [DEFAULT_MODAL_SELECTORS, marker],
    )


def _clear_modal_marker(page, marker: str) -> None:
    page.evaluate(
        """(marker) => {
            document.querySelectorAll(`[data-crawl-modal-marker=\"${marker}\"]`)
                .forEach((el) => el.removeAttribute('data-crawl-modal-marker'));
        }""",
        marker,
    )


def _pick_trigger_candidates(elements: list[dict[str, Any]], max_count: int) -> list[dict[str, Any]]:
    keywords = ("新增", "添加", "新建", "编辑", "配置", "设置", "详情", "打开", "创建")
    picked: list[dict[str, Any]] = []
    seen = set()
    for el in elements:
        if el.get("element_type") != "action_btn":
            continue
        text = _normalize_text(el.get("text_content") or "")
        locator = el.get("playwright_locator") or ""
        key = locator or text
        if not key or key in seen:
            continue
        if text and any(k in text for k in keywords):
            picked.append(el)
            seen.add(key)
        if len(picked) >= max_count:
            break
    return picked


def _click_by_locator(page, locator_text: str) -> bool:
    if not locator_text:
        return False
    locator = None
    try:
        if locator_text.startswith("#") or ">" in locator_text or locator_text.startswith("["):
            locator = page.locator(locator_text).first
        elif locator_text.startswith("get_by_text("):
            match = re.search(r"get_by_text\('(.+)'\)", locator_text)
            if match:
                locator = page.get_by_text(match.group(1)).first
        elif locator_text.startswith("get_by_role("):
            role_match = re.search(r"get_by_role\('([^']+)'\s*,\s*name='([^']+)'\)", locator_text)
            if role_match:
                locator = page.get_by_role(role_match.group(1), name=role_match.group(2)).first
        if locator is None:
            return False
        if locator.count() < 1:
            return False
        locator.click(timeout=2500)
        return True
    except PlaywrightError:
        return False


def _crawl_single_page(page, url: str, screenshot_dir: Path, max_elements: int, max_modal_triggers: int, timeout_ms: int) -> dict[str, Any]:
    page_result: dict[str, Any] = {
        "url_pattern": _to_route_path(url) or url,
        "target_url": url,
        "page_title": None,
        "screenshot_path": None,
        "is_crawled": False,
        "crawled_at": None,
        "containers": [],
        "elements": [],
        "elements_raw_count": 0,
        "elements_filtered_out_count": 0,
        "modal_containers": [],
        "errors": [],
    }

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(700)
    except PlaywrightError as exc:
        page_result["errors"].append(f"goto failed: {exc}")
        return page_result

    page_result["page_title"] = page.title()
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", page_result["url_pattern"])[:120]
    screenshot_path = screenshot_dir / f"{safe_name or 'page'}.png"
    page.screenshot(path=str(screenshot_path), full_page=True)
    page_result["screenshot_path"] = str(screenshot_path.resolve())

    main_elements_raw = _extract_elements(page, None, max_elements)
    main_elements, dropped_main = _enrich_and_filter_elements(main_elements_raw)
    page_result["elements_raw_count"] += len(main_elements_raw)
    page_result["elements_filtered_out_count"] += dropped_main
    page_result["containers"].append(
        {
            "container_id": "page_body",
            "container_type": "page_body",
            "title": page_result["page_title"],
            "css_selector": "body",
            "is_dynamic": False,
            "is_visible_default": True,
            "trigger_element_id": None,
            "trigger_action": None,
        }
    )
    page_result["elements"].extend(
        [{**el, "container_id": "page_body"} for el in main_elements]
    )

    # Modal crawl
    trigger_candidates = _pick_trigger_candidates(main_elements, max_modal_triggers)
    for idx, trigger in enumerate(trigger_candidates):
        clicked = _click_by_locator(page, trigger.get("playwright_locator") or "")
        if not clicked:
            continue
        page.wait_for_timeout(600)
        marker = f"modal_{idx}"
        modal_selector = _extract_modal_selector(page, marker)
        if not modal_selector:
            continue
        modal_elements_raw = _extract_elements(page, modal_selector, max_elements)
        modal_elements, dropped_modal = _enrich_and_filter_elements(modal_elements_raw)
        page_result["elements_raw_count"] += len(modal_elements_raw)
        page_result["elements_filtered_out_count"] += dropped_modal
        container_id = f"modal_{idx}"
        page_result["modal_containers"].append(
            {
                "container_id": container_id,
                "container_type": "modal",
                "title": trigger.get("text_content") or f"modal_{idx}",
                "css_selector": modal_selector,
                "is_dynamic": True,
                "is_visible_default": True,
                "trigger_element_id": trigger.get("playwright_locator"),
                "trigger_action": "click",
            }
        )
        page_result["elements"].extend(
            [{**el, "container_id": container_id} for el in modal_elements]
        )
        _clear_modal_marker(page, marker)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

    page_result["is_crawled"] = True
    page_result["crawled_at"] = _now_iso()
    return page_result


def _menu_node_score(node: dict[str, Any]) -> float:
    score = 0.0
    if node.get("node_type") == "page":
        score += 10
    if node.get("target_url"):
        score += 50
    if node.get("route_path"):
        score += 40
    if node.get("source") == "dom":
        score += 8
    if node.get("source") == "console_route":
        score += 5
    locator = str(node.get("playwright_locator") or "")
    if locator.startswith("get_by_role("):
        score += 4
    elif locator.startswith("get_by_text("):
        score += 2
    return score


def _merge_menu_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    route_best: dict[str, dict[str, Any]] = {}
    others_map: dict[str, dict[str, Any]] = {}

    for raw in nodes:
        node = dict(raw)
        route_path = node.get("route_path")
        if node.get("node_type") == "page" and route_path:
            prev = route_best.get(route_path)
            if prev is None or _menu_node_score(node) > _menu_node_score(prev):
                if prev:
                    # Fill missing fields from previous version.
                    for field in ("text_breadcrumb", "playwright_locator", "title", "target_url"):
                        if not node.get(field):
                            node[field] = prev.get(field)
                route_best[route_path] = node
            continue

        key = f"{node.get('node_type')}|{node.get('text_breadcrumb') or ''}|{node.get('title') or ''}"
        prev_other = others_map.get(key)
        if prev_other is None or _menu_node_score(node) > _menu_node_score(prev_other):
            others_map[key] = node

    merged = list(others_map.values()) + list(route_best.values())
    merged.sort(
        key=lambda n: (
            int(n.get("menu_level") or 0),
            int(n.get("menu_order") or 0),
            _normalize_text(str(n.get("text_breadcrumb") or n.get("title") or "")),
        )
    )

    # Mark AI primary menu candidates.
    page_candidates = [
        node
        for node in merged
        if node.get("node_type") == "page" and node.get("target_url")
    ]
    page_candidates.sort(
        key=lambda n: (
            0 if n.get("source") == "dom" else 1,
            _normalize_text(str(n.get("text_breadcrumb") or n.get("title") or "")),
        )
    )
    rank_map = {id(node): idx + 1 for idx, node in enumerate(page_candidates)}
    for node in merged:
        rank = rank_map.get(id(node))
        node["is_ai_primary_candidate"] = rank is not None
        node["ai_candidate_rank"] = rank

    return merged


def _build_ai_menu_candidates(menu_nodes: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    candidates = [
        node
        for node in menu_nodes
        if node.get("is_ai_primary_candidate") and node.get("target_url")
    ]
    candidates.sort(key=lambda n: int(n.get("ai_candidate_rank") or 9999))
    out: list[dict[str, Any]] = []
    for node in candidates[:limit]:
        out.append(
            {
                "candidate_rank": node.get("ai_candidate_rank"),
                "title": node.get("title"),
                "text_breadcrumb": node.get("text_breadcrumb"),
                "node_type": node.get("node_type"),
                "route_path": node.get("route_path"),
                "target_url": node.get("target_url"),
                "playwright_locator": node.get("playwright_locator"),
                "source": node.get("source"),
            }
        )
    return out


def _build_menu_nodes(route_data: dict[str, Any], dom_data: dict[str, Any], origin: str, home_url: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    route_map: dict[str, dict[str, Any]] = {}

    for route in route_data.get("routes", []):
        rp = _route_path_from_menu_hint(route.get("route_path"))
        if not rp:
            continue
        route["route_path"] = rp
        if not route.get("target_url"):
            route["target_url"] = _build_url_from_route(origin, home_url, rp)
        route_map[rp] = route

    for node in dom_data.get("nodes", []):
        rp = _route_path_from_menu_hint(node.get("route_path")) or _route_path_from_menu_hint(
            node.get("route_hint")
        )
        if rp:
            node["route_path"] = rp
        route = route_map.get(rp or "")
        if route:
            node["route_name"] = route.get("route_name")
            if not node.get("target_url"):
                node["target_url"] = route.get("target_url")
        if not node.get("target_url"):
            node["target_url"] = _build_url_from_route(origin, home_url, rp)
        nodes.append(node)

    # Console routes that are not found in DOM become virtual leaf nodes
    existing_paths = {n.get("route_path") for n in nodes if n.get("route_path")}
    virtual_idx = 0
    for rp, route in route_map.items():
        if rp in existing_paths:
            continue
        title = route.get("title") or route.get("route_name") or rp
        locator_title = str(title).replace("'", "\\'")
        virtual_idx += 1
        nodes.append(
            {
                "node_id": f"virtual.{virtual_idx}",
                "parent_id": None,
                "title": title,
                "text_breadcrumb": f"控制台路由 > {title}",
                "menu_order": virtual_idx,
                "menu_level": 1,
                "path_indexes": [virtual_idx],
                "node_type": "page",
                "target_url": route.get("target_url") or _build_url_from_route(origin, home_url, rp),
                "route_path": rp,
                "route_name": route.get("route_name"),
                "playwright_locator": f"get_by_text('{locator_title}')",
                "is_group": False,
                "is_external": False,
                "is_visible": False,
                "dom_css_path": None,
                "source": "console_route",
            }
        )

    # Fill node_path for compatibility with nav_menus design
    for node in nodes:
        breadcrumb = node.get("text_breadcrumb") or node.get("title") or "node"
        segments = [_safe_slug(s) for s in breadcrumb.split(">")]
        node["node_path"] = "root." + ".".join([s for s in segments if s])
    return _merge_menu_nodes(nodes)


def _build_url_queue(menu_nodes: list[dict[str, Any]], max_pages: int, home_url: str) -> list[str]:
    queue: list[str] = []
    seen = set()
    origin = _origin_of(home_url) if home_url else ""
    for node in menu_nodes:
        if node.get("node_type") != "page":
            continue
        if not node.get("is_ai_primary_candidate"):
            continue
        route_path = node.get("route_path") or ""
        if isinstance(route_path, str) and route_path.startswith("/auth"):
            continue
        target_url = node.get("target_url")
        if not target_url:
            target_url = _build_url_from_route(origin, home_url, route_path)
        if not target_url:
            continue
        if target_url in seen:
            continue
        seen.add(target_url)
        queue.append(target_url)
        if len(queue) >= max_pages:
            break
    if not queue and home_url:
        queue.append(home_url)
    return queue


def main() -> None:
    args = parse_args()
    storage_state_path = Path(args.storage_state).resolve()
    auth_input_path = Path(args.auth_input).resolve()
    output_path = Path(args.output).resolve()
    screenshot_dir = Path(args.screenshot_dir).resolve()

    if not storage_state_path.exists():
        raise RuntimeError(f"Storage state not found: {storage_state_path}")
    if not auth_input_path.exists():
        raise RuntimeError(f"Auth input not found: {auth_input_path}")

    auth_payload = _load_json(auth_input_path)
    login_url = auth_payload.get("base_url") or DEFAULT_LOGIN_URL
    origin = _origin_of(login_url)
    home_url = args.home_url or auth_payload.get("current_url") or DEFAULT_HOME_URL
    request_headers = {str(k): str(v) for k, v in (auth_payload.get("request_headers") or {}).items() if v is not None}
    authorization = str(auth_payload.get("authorization") or "").strip()
    if authorization and not any(str(key).lower() == "authorization" for key in request_headers):
        request_headers["authorization"] = authorization
    local_storage = auth_payload.get("local_storage") or {}
    session_storage = auth_payload.get("session_storage") or {}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(storage_state=str(storage_state_path))
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)
        context.set_default_timeout(args.timeout_ms)

        if request_headers:
            page.set_extra_http_headers(request_headers)

        _apply_saved_web_storage(
            page=page,
            origin=origin,
            local_storage=local_storage,
            session_storage=session_storage,
            timeout_ms=args.timeout_ms,
        )

        state_valid = _is_state_valid(page, home_url, args.timeout_ms)
        if not state_valid:
            browser.close()
            payload = {
                "meta": {
                    "collected_at": _now_iso(),
                    "state_valid": False,
                    "next_action": "trigger_auth_task",
                    "message": "Saved session is invalid; authentication refresh required.",
                },
                "menus": [],
                "pages": [],
                "stats": {"menu_count": 0, "page_count": 0, "element_count": 0},
            }
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print("State invalid. Trigger auth workflow and stop crawling.")
            print(f"Output saved: {output_path}")
            return

        # Framework + route extraction
        framework_info = _detect_framework(page)
        route_data = _extract_routes_from_console(page, origin, home_url)
        if not route_data.get("routes"):
            route_data = _extract_routes_from_bundle(page, origin, home_url)

        # DOM fallback (and as source of menu breadcrumb/path indexes)
        expand_clicks = _expand_menu_dom(page, args.expand_rounds)
        dom_data = _extract_menu_nodes_from_dom(page, origin, args.menu_selector)

        menu_nodes = _build_menu_nodes(route_data, dom_data, origin, home_url)
        url_queue = _build_url_queue(menu_nodes, args.max_pages, home_url)

        pages: list[dict[str, Any]] = []
        for url in url_queue:
            pages.append(
                _crawl_single_page(
                    page=page,
                    url=url,
                    screenshot_dir=screenshot_dir,
                    max_elements=args.max_elements_per_page,
                    max_modal_triggers=args.max_modal_triggers,
                    timeout_ms=args.timeout_ms,
                )
            )

        element_count = sum(len(p.get("elements", [])) for p in pages)
        element_raw_count = sum(int(p.get("elements_raw_count") or 0) for p in pages)
        element_filtered_out_count = sum(
            int(p.get("elements_filtered_out_count") or 0) for p in pages
        )
        modal_container_count = sum(len(p.get("modal_containers", [])) for p in pages)
        ai_menu_candidates = _build_ai_menu_candidates(menu_nodes, limit=20)

        payload = {
            "meta": {
                "collected_at": _now_iso(),
                "system_code": urlsplit(origin).netloc,
                "base_url": origin,
                "home_url": home_url,
                "state_source": {
                    "storage_state_path": str(storage_state_path),
                    "auth_input_path": str(auth_input_path),
                },
                "state_valid": True,
                "framework_detection": framework_info,
                "route_extraction": {
                    "success": bool(route_data.get("success")),
                    "source": route_data.get("source"),
                    "route_count": len(route_data.get("routes", [])),
                },
                "dom_crawl": {
                    "success": bool(dom_data.get("success")),
                    "root_selector": dom_data.get("root_selector"),
                    "expand_clicks": expand_clicks,
                    "menu_count": len(dom_data.get("nodes", [])),
                },
                "ai_context_hints": {
                    "menu_primary_candidates": len(ai_menu_candidates),
                    "element_filtering_enabled": True,
                    "element_stability_scoring_enabled": True,
                },
            },
            "menus": menu_nodes,
            "ai_menu_candidates": ai_menu_candidates,
            "routes_console": route_data.get("routes", []),
            "pages": pages,
            "stats": {
                "menu_count": len(menu_nodes),
                "queued_page_count": len(url_queue),
                "crawled_page_count": len([p for p in pages if p.get("is_crawled")]),
                "element_raw_count": element_raw_count,
                "element_count": element_count,
                "element_filtered_out_count": element_filtered_out_count,
                "modal_container_count": modal_container_count,
            },
        }

        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()

    print("Menu map crawl completed.")
    print(f"Output saved: {output_path}")
    print(f"Menus: {len(payload['menus'])}")
    print(f"Pages crawled: {payload['stats']['crawled_page_count']}/{payload['stats']['queued_page_count']}")
    print(f"Elements: {payload['stats']['element_count']}")


if __name__ == "__main__":
    main()
