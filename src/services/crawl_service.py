"""Menu map crawling service based on DB-managed storage state."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, text, update
from sqlmodel import desc, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.models.app_page import AppPage
from src.models.nav_menu import NavMenu
from src.models.storage_state import StorageState
from src.models.ui_container import UIContainer
from src.models.ui_element import UIElement
from src.models.web_system import WebSystem
from src.scheduler.locks import distributed_lock
from src.services.auth_service import AuthService
from src.services.task_tracker import TaskTracker

DEFAULT_HOME_PATH = "#/analytics"
DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_ELEMENTS_PER_PAGE = 180
DEFAULT_MAX_MODAL_TRIGGERS = 8
DEFAULT_EXPAND_ROUNDS = 6
DEFAULT_TIMEOUT_MS = 45_000


@dataclass(slots=True)
class CrawlRunResult:
    """Result summary for one crawl run."""

    sys_code: str
    status: str
    message: str
    crawl_log_id: UUID | None
    auth_triggered: bool
    menus_saved: int
    pages_saved: int
    elements_saved: int
    output_path: str | None
    started_at: datetime
    finished_at: datetime


class CrawlService:
    """Orchestrates menu crawl and DB persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.task_tracker = TaskTracker(session)

    async def run_by_sys_code(
        self,
        sys_code: str,
        *,
        headed: bool = False,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_elements_per_page: int = DEFAULT_MAX_ELEMENTS_PER_PAGE,
        max_modal_triggers: int = DEFAULT_MAX_MODAL_TRIGGERS,
        expand_rounds: int = DEFAULT_EXPAND_ROUNDS,
        menu_selector: str = "",
        home_url: str = "",
    ) -> CrawlRunResult:
        started_at = _utc_now()
        task_log_id: UUID | None = None
        system = await self._get_active_system(sys_code)
        if system is None:
            finished_at = _utc_now()
            return CrawlRunResult(
                sys_code=sys_code,
                status="failed",
                message=f"System not found or inactive: {sys_code}",
                crawl_log_id=None,
                auth_triggered=False,
                menus_saved=0,
                pages_saved=0,
                elements_saved=0,
                output_path=None,
                started_at=started_at,
                finished_at=finished_at,
            )

        task_log = await self.task_tracker.start(
            system_id=system.id,
            task_type="crawl_menu",
            target_url=home_url or self._default_home_url(system),
        )
        task_log_id = task_log.id

        state = await self._get_valid_state(system)
        if state is None:
            auth_result = await AuthService(self.session).refresh_by_sys_code(sys_code)
            finished_at = _utc_now()
            await self.task_tracker.finish(
                log_id=task_log_id,
                status="skipped",
                error_message=f"No valid state. Auth refresh result: {auth_result.status}",
                retry_count=0,
            )
            return CrawlRunResult(
                sys_code=sys_code,
                status="auth_triggered",
                message=f"No valid state. Auth refresh result: {auth_result.status}",
                crawl_log_id=task_log_id,
                auth_triggered=True,
                menus_saved=0,
                pages_saved=0,
                elements_saved=0,
                output_path=None,
                started_at=started_at,
                finished_at=finished_at,
            )

        lock_name = f"crawl:{system.sys_code}"
        async with distributed_lock.acquire(lock_name) as acquired:
            if not acquired:
                finished_at = _utc_now()
                await self.task_tracker.finish(
                    log_id=task_log_id,
                    status="skipped",
                    error_message="Crawl task is already running.",
                    retry_count=0,
                )
                return CrawlRunResult(
                    sys_code=sys_code,
                    status="skipped",
                    message="Crawl task is already running.",
                    crawl_log_id=task_log_id,
                    auth_triggered=False,
                    menus_saved=0,
                    pages_saved=0,
                    elements_saved=0,
                    output_path=None,
                    started_at=started_at,
                    finished_at=finished_at,
                )

            output_path: Path | None = None
            try:
                output_path = await self._run_crawler_script(
                    system=system,
                    state=state,
                    headed=headed,
                    timeout_ms=timeout_ms,
                    max_pages=max_pages,
                    max_elements_per_page=max_elements_per_page,
                    max_modal_triggers=max_modal_triggers,
                    expand_rounds=expand_rounds,
                    menu_selector=menu_selector,
                    home_url=home_url or self._default_home_url(system),
                )
                payload = json.loads(output_path.read_text(encoding="utf-8"))

                if not bool(payload.get("meta", {}).get("state_valid")):
                    auth_result = await AuthService(self.session).refresh_by_sys_code(sys_code)
                    await self.task_tracker.finish(
                        log_id=task_log_id,
                        status="skipped",
                        error_message=f"state invalid in crawler; auth refresh={auth_result.status}",
                        retry_count=0,
                    )
                    finished_at = _utc_now()
                    return CrawlRunResult(
                        sys_code=sys_code,
                        status="auth_triggered",
                        message="State invalid during crawl. Triggered auth refresh.",
                        crawl_log_id=task_log_id,
                        auth_triggered=True,
                        menus_saved=0,
                        pages_saved=0,
                        elements_saved=0,
                        output_path=str(output_path),
                        started_at=started_at,
                        finished_at=finished_at,
                    )

                save_result = await self._persist_payload(system.id, payload)
                now = _utc_now()
                await self.session.exec(
                    update(WebSystem)
                    .where(WebSystem.id == system.id)
                    .values(last_crawl_at=now, updated_at=now)
                )
                await self.session.commit()

                await self.task_tracker.finish(
                    log_id=task_log_id,
                    status="success",
                    retry_count=0,
                    pages_found=save_result["pages_saved"],
                    elements_found=save_result["elements_saved"],
                )

                finished_at = _utc_now()
                return CrawlRunResult(
                    sys_code=sys_code,
                    status="success",
                    message="Menu map crawl and persistence completed.",
                    crawl_log_id=task_log_id,
                    auth_triggered=False,
                    menus_saved=save_result["menus_saved"],
                    pages_saved=save_result["pages_saved"],
                    elements_saved=save_result["elements_saved"],
                    output_path=str(output_path),
                    started_at=started_at,
                    finished_at=finished_at,
                )
            except Exception as exc:
                await self.session.rollback()
                await self.task_tracker.finish(
                    log_id=task_log_id,
                    status="failed",
                    error_message=str(exc),
                    retry_count=0,
                )
                finished_at = _utc_now()
                return CrawlRunResult(
                    sys_code=sys_code,
                    status="failed",
                    message=f"Crawl failed: {exc}",
                    crawl_log_id=task_log_id,
                    auth_triggered=False,
                    menus_saved=0,
                    pages_saved=0,
                    elements_saved=0,
                    output_path=str(output_path) if output_path else None,
                    started_at=started_at,
                    finished_at=finished_at,
                )

    async def _persist_payload(self, system_id: UUID, payload: dict[str, Any]) -> dict[str, int]:
        menus = payload.get("menus") or []
        pages = payload.get("pages") or []

        page_ids_subq = select(AppPage.id).where(AppPage.system_id == system_id)
        await self.session.exec(delete(UIElement).where(UIElement.page_id.in_(page_ids_subq)))
        await self.session.exec(delete(UIContainer).where(UIContainer.page_id.in_(page_ids_subq)))
        await self.session.exec(delete(AppPage).where(AppPage.system_id == system_id))
        await self.session.exec(delete(NavMenu).where(NavMenu.system_id == system_id))
        await self.session.flush()

        node_id_map: dict[str, UUID] = {}
        for index, node in enumerate(menus, start=1):
            raw_node_id = str(node.get("node_id") or f"node-{index}")
            node_id_map[raw_node_id] = uuid4()

        nav_insert_sql = text(
            """
            INSERT INTO nav_menus (
                id, system_id, parent_id, node_path, title, text_breadcrumb, icon,
                menu_order, menu_level, path_indexes, node_type, target_url, route_path,
                route_name, playwright_locator, last_verified_status, last_verified_at,
                is_group, is_external, is_visible
            ) VALUES (
                :id, :system_id, :parent_id, CAST(:node_path AS ltree), :title, :text_breadcrumb, :icon,
                :menu_order, :menu_level, CAST(:path_indexes AS jsonb), :node_type, :target_url, :route_path,
                :route_name, :playwright_locator, :last_verified_status, :last_verified_at,
                :is_group, :is_external, :is_visible
            )
            """
        )

        route_name_by_menu_id: dict[UUID, str | None] = {}
        for index, node in enumerate(menus, start=1):
            raw_node_id = str(node.get("node_id") or f"node-{index}")
            node_uuid = node_id_map[raw_node_id]
            parent_raw = str(node.get("parent_id") or "")
            parent_uuid = node_id_map.get(parent_raw)
            path_indexes = node.get("path_indexes") if isinstance(node.get("path_indexes"), list) else None
            route_name = _safe_str(node.get("route_name"), 255)
            route_name_by_menu_id[node_uuid] = route_name

            await self.session.execute(
                nav_insert_sql,
                {
                    "id": node_uuid,
                    "system_id": system_id,
                    "parent_id": parent_uuid,
                    "node_path": None,
                    "title": str(node.get("title") or node.get("route_name") or "未命名菜单")[:255],
                    "text_breadcrumb": node.get("text_breadcrumb"),
                    "icon": _safe_str(node.get("icon"), 128),
                    "menu_order": _to_int(node.get("menu_order")),
                    "menu_level": _to_int(node.get("menu_level")),
                    "path_indexes": json.dumps(path_indexes, ensure_ascii=False) if path_indexes is not None else None,
                    "node_type": _safe_str(node.get("node_type"), 20),
                    "target_url": _safe_str(node.get("target_url"), 500),
                    "route_path": _safe_str(node.get("route_path"), 500),
                    "route_name": route_name,
                    "playwright_locator": node.get("playwright_locator"),
                    "last_verified_status": "ok",
                    "last_verified_at": _utc_now(),
                    "is_group": _to_bool(node.get("is_group")),
                    "is_external": _to_bool(node.get("is_external")),
                    "is_visible": _to_bool(node.get("is_visible")),
                },
            )
        await self.session.flush()

        menu_by_route: dict[str, UUID] = {}
        menu_by_target_url: dict[str, UUID] = {}
        for node in menus:
            node_uuid = node_id_map.get(str(node.get("node_id") or ""))
            if not node_uuid:
                continue
            route_path = str(node.get("route_path") or "").strip()
            if route_path:
                menu_by_route.setdefault(route_path, node_uuid)
            target_url = str(node.get("target_url") or "").strip()
            if target_url:
                menu_by_target_url.setdefault(target_url, node_uuid)

        pages_saved = 0
        elements_saved = 0

        for page_payload in pages:
            url_pattern = str(page_payload.get("url_pattern") or page_payload.get("target_url") or "").strip()
            if not url_pattern:
                continue

            route_path = self._route_path_from_url_pattern(url_pattern)
            menu_id = menu_by_route.get(route_path or "") or menu_by_target_url.get(
                str(page_payload.get("target_url") or "")
            )
            page_model = AppPage(
                system_id=system_id,
                menu_id=menu_id,
                url_pattern=url_pattern[:500],
                route_name=self._guess_route_name(menu_id, route_name_by_menu_id),
                page_title=_safe_str(page_payload.get("page_title"), 255),
                page_summary=None,
                description=None,
                keywords=None,
                meta_info={
                    "target_url": page_payload.get("target_url"),
                    "errors": page_payload.get("errors") or [],
                    "elements_raw_count": _to_int(page_payload.get("elements_raw_count")),
                    "elements_filtered_out_count": _to_int(page_payload.get("elements_filtered_out_count")),
                },
                screenshot_path=_safe_str(page_payload.get("screenshot_path"), 1000),
                is_crawled=_to_bool(page_payload.get("is_crawled")),
                crawled_at=_parse_dt(page_payload.get("crawled_at")),
            )
            self.session.add(page_model)
            await self.session.flush()
            pages_saved += 1

            container_map: dict[str, UUID] = {}
            containers_payload = (page_payload.get("containers") or []) + (page_payload.get("modal_containers") or [])
            for container in containers_payload:
                raw_container_id = str(container.get("container_id") or f"container-{uuid4().hex}")
                container_model = UIContainer(
                    page_id=page_model.id,
                    container_type=_safe_str(container.get("container_type"), 50) or "page_body",
                    title=_safe_str(container.get("title"), 255),
                    xpath_root=_safe_str(container.get("xpath_root"), 1000),
                    css_selector=_safe_str(container.get("css_selector"), 1000),
                    trigger_element_id=None,
                    trigger_action=_safe_str(container.get("trigger_action"), 50),
                    is_dynamic=_to_bool(container.get("is_dynamic")),
                    is_visible_default=_to_bool(container.get("is_visible_default")),
                )
                self.session.add(container_model)
                await self.session.flush()
                container_map[raw_container_id] = container_model.id

            for element in page_payload.get("elements") or []:
                locators = element.get("locators")
                if not isinstance(locators, dict):
                    locators = {"dom_css_path": element.get("dom_css_path") or ""}
                ui_element = UIElement(
                    page_id=page_model.id,
                    container_id=container_map.get(str(element.get("container_id") or "")),
                    tag_name=_safe_str(element.get("tag_name"), 50) or "div",
                    element_type=_safe_str(element.get("element_type"), 50),
                    text_content=element.get("text_content"),
                    locators=locators,
                    playwright_locator=element.get("playwright_locator"),
                    nearby_text=element.get("nearby_text"),
                    usage_description=element.get("usage_description"),
                    screenshot_slice_path=None,
                    bounding_box=element.get("bounding_box") if isinstance(element.get("bounding_box"), dict) else None,
                )
                self.session.add(ui_element)
                elements_saved += 1

        await self.session.flush()
        return {
            "menus_saved": len(node_id_map),
            "pages_saved": pages_saved,
            "elements_saved": elements_saved,
        }

    async def _run_crawler_script(
        self,
        *,
        system: WebSystem,
        state: StorageState,
        headed: bool,
        timeout_ms: int,
        max_pages: int,
        max_elements_per_page: int,
        max_modal_triggers: int,
        expand_rounds: int,
        menu_selector: str,
        home_url: str,
    ) -> Path:
        runtime_dir = Path("output/playwright/runtime").resolve() / f"{system.sys_code}-{int(_utc_now().timestamp())}"
        runtime_dir.mkdir(parents=True, exist_ok=True)

        storage_state_path = runtime_dir / "storage-state.json"
        auth_input_path = runtime_dir / "auth.json"
        output_path = runtime_dir / "menu-map.json"
        screenshot_dir = runtime_dir / "screenshots"

        storage_state_path.write_text(json.dumps(state.storage_state, ensure_ascii=False), encoding="utf-8")
        auth_payload = {
            "base_url": system.login_url or system.base_url,
            "current_url": home_url,
            "authorization": None,
            "request_headers": state.request_headers or {},
            "cookies": state.cookies or [],
            "local_storage": state.local_storage or {},
            "session_storage": state.session_storage or {},
        }
        auth_input_path.write_text(json.dumps(auth_payload, ensure_ascii=False), encoding="utf-8")

        script_path = Path("scripts/crawl-menu-map.py").resolve()
        cmd = [
            sys.executable,
            str(script_path),
            "--storage-state",
            str(storage_state_path),
            "--auth-input",
            str(auth_input_path),
            "--output",
            str(output_path),
            "--screenshot-dir",
            str(screenshot_dir),
            "--home-url",
            home_url,
            "--max-pages",
            str(max_pages),
            "--max-elements-per-page",
            str(max_elements_per_page),
            "--max-modal-triggers",
            str(max_modal_triggers),
            "--expand-rounds",
            str(expand_rounds),
            "--timeout-ms",
            str(timeout_ms),
        ]
        if menu_selector:
            cmd.extend(["--menu-selector", menu_selector])
        if headed:
            cmd.append("--headed")

        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "crawl-menu-map.py failed"
                f"\nstdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        if not output_path.exists():
            raise RuntimeError(f"Crawler output missing: {output_path}")
        return output_path

    async def _get_active_system(self, sys_code: str) -> WebSystem | None:
        stmt = select(WebSystem).where(WebSystem.sys_code == sys_code, WebSystem.is_active.is_(True))
        result = await self.session.exec(stmt)
        return result.first()

    async def _get_valid_state(self, system: WebSystem) -> StorageState | None:
        state: StorageState | None = None
        if system.latest_valid_state_id:
            state = await self.session.get(StorageState, system.latest_valid_state_id)
            if state and not state.is_valid:
                state = None

        if state is None:
            stmt = (
                select(StorageState)
                .where(StorageState.system_id == system.id, StorageState.is_valid.is_(True))
                .order_by(desc(StorageState.validated_at), desc(StorageState.created_at))
            )
            result = await self.session.exec(stmt)
            state = result.first()

        if state is not None:
            now = _utc_now()
            state.last_used_at = now
            await self.session.commit()

        return state

    @staticmethod
    def _default_home_url(system: WebSystem) -> str:
        base = system.base_url.rstrip("/")
        return f"{base}/{DEFAULT_HOME_PATH}" if "#/" not in base else base

    @staticmethod
    def _route_path_from_url_pattern(url_pattern: str) -> str | None:
        raw = (url_pattern or "").strip()
        if raw.startswith("/"):
            return raw
        if "#/" in raw:
            return "/" + raw.split("#/", 1)[1].lstrip("/")
        return None

    @staticmethod
    def _guess_route_name(menu_id: UUID | None, route_name_by_menu_id: dict[UUID, str | None]) -> str | None:
        if not menu_id:
            return None
        return route_name_by_menu_id.get(menu_id)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
        return None
    if isinstance(value, int):
        return bool(value)
    return None


def _safe_str(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None
