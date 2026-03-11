from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlmodel import select

from src.config.settings import get_settings
from src.crawler.auth_crawler import AuthCapture
from src.models.app_page import AppPage
from src.models.crawl_log import CrawlLog
from src.models.database import close_db, get_session_factory, init_db, ping_db, session_scope
from src.models.nav_menu import NavMenu
from src.models.storage_state import StorageState
from src.models.web_system import WebSystem
from src.scheduler.locks import distributed_lock
from src.services.auth_service import AuthAnalysis, AuthService
from src.services.crawl_service import CrawlService, _build_payload_fingerprint
from src.services.validator_service import ValidationResult


@pytest.fixture(autouse=True)
async def _prepare_env(monkeypatch):
    test_schema = f"ut_task_{uuid4().hex[:10]}"
    monkeypatch.setenv("DATABASE_SCHEMA", test_schema)
    monkeypatch.setenv("ENCRYPTION_KEY", "test-task-tracker-key")
    monkeypatch.setenv("AUTH_MAX_RETRIES", "1")
    get_settings.cache_clear()
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")
    await init_db()
    yield
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.exec(text(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE'))
        await session.commit()
    await close_db()
    get_settings.cache_clear()


async def _create_system(*, with_credentials: bool = True) -> WebSystem:
    sys_code = f"ut-task-{uuid4().hex[:8]}"
    base_url = f"https://{sys_code}.example.com"
    async with session_scope() as session:
        system = WebSystem(
            sys_code=sys_code,
            name="UT Task System",
            base_url=base_url,
            login_url=f"{base_url}/login",
            login_username="user" if with_credentials else None,
            login_password="pass" if with_credentials else None,
            auth_mode_hint="unknown",
            playback_strategy_default="auto",
            is_active=True,
        )
        session.add(system)
        await session.flush()
        system_id = system.id

    async with session_scope() as session:
        return await session.get(WebSystem, system_id)


async def _create_valid_state(system_id):
    async with session_scope() as session:
        state = StorageState(
            system_id=system_id,
            storage_state={"cookies": [], "origins": []},
            cookies=[],
            local_storage={},
            session_storage={},
            request_headers={},
            auth_mode="unknown",
            playback_strategy="auto",
            is_valid=True,
        )
        session.add(state)
        await session.flush()
        system = await session.get(WebSystem, system_id)
        system.latest_valid_state_id = state.id


async def _latest_task_log(system_id, task_type: str) -> CrawlLog | None:
    async with session_scope() as session:
        stmt = (
            select(CrawlLog)
            .where(CrawlLog.system_id == system_id, CrawlLog.task_type == task_type)
            .order_by(CrawlLog.started_at.desc(), CrawlLog.id.desc())
        )
        return (await session.exec(stmt)).first()


@pytest.mark.asyncio
async def test_auth_refresh_writes_success_task_log(monkeypatch):
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system = await _create_system(with_credentials=True)

    async with session_scope() as session:
        service = AuthService(session)

        async def fake_login_and_capture(**_kwargs):
            return AuthCapture(
                base_url=system.login_url,
                current_url="https://example.com/#/home",
                storage_state={"cookies": [], "origins": []},
                cookies=[],
                local_storage={},
                session_storage={},
                request_headers={"authorization": "Bearer demo-token"},
                authorization="Bearer demo-token",
            )

        async def fake_validate(_system, _capture):
            return ValidationResult(is_valid=True, status_code=200, response_ms=25, error=None)

        async def fake_save_state(_system, _capture, _analysis, _validation):
            return uuid4()

        monkeypatch.setattr(service.crawler, "login_and_capture", fake_login_and_capture)
        monkeypatch.setattr("src.services.auth_service.validate_capture", fake_validate)
        monkeypatch.setattr(service, "_save_state", fake_save_state)

        result = await service.refresh_by_sys_code(system.sys_code)

    assert result.status == "success"
    log = await _latest_task_log(system.id, "auth")
    assert log is not None
    assert log.status == "success"
    assert log.retry_count == 0
    assert log.duration_ms is not None


@pytest.mark.asyncio
async def test_auth_refresh_writes_failed_task_log(monkeypatch):
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system = await _create_system(with_credentials=True)

    async with session_scope() as session:
        service = AuthService(session)

        async def fake_login_and_capture(**_kwargs):
            raise RuntimeError("login failed")

        monkeypatch.setattr(service.crawler, "login_and_capture", fake_login_and_capture)

        result = await service.refresh_by_sys_code(system.sys_code)

    assert result.status == "failed"
    log = await _latest_task_log(system.id, "auth")
    assert log is not None
    assert log.status == "failed"
    assert "login failed" in (log.error_message or "")


@pytest.mark.asyncio
async def test_auth_refresh_writes_skipped_task_log_when_lock_held(monkeypatch):
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system = await _create_system(with_credentials=True)

    @asynccontextmanager
    async def fake_acquire(*_args, **_kwargs):
        yield False

    monkeypatch.setattr(distributed_lock, "acquire", fake_acquire)

    async with session_scope() as session:
        service = AuthService(session)
        result = await service.refresh_by_sys_code(system.sys_code)

    assert result.status == "skipped"
    log = await _latest_task_log(system.id, "auth")
    assert log is not None
    assert log.status == "skipped"


@pytest.mark.asyncio
async def test_auth_service_save_state_updates_existing_state_instead_of_inserting():
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system = await _create_system(with_credentials=True)

    async with session_scope() as session:
        service = AuthService(session)

        capture_v1 = AuthCapture(
            base_url=system.login_url,
            current_url=f"{system.base_url}/#/home",
            storage_state={"cookies": [{"name": "sid", "value": "v1"}], "origins": []},
            cookies=[{"name": "sid", "value": "v1"}],
            local_storage={"token": "token-v1"},
            session_storage={},
            request_headers={"authorization": "Bearer token-v1"},
            authorization="Bearer token-v1",
        )
        analysis_v1 = AuthAnalysis(
            auth_mode="hybrid",
            playback_strategy="hybrid",
            authorization_source="request_header",
            authorization_schema="Bearer",
            authorization_value="Bearer token-v1",
            auth_fingerprint="fp-v1",
        )
        state_id_v1 = await service._save_state(
            system,
            capture_v1,
            analysis_v1,
            ValidationResult(is_valid=True, status_code=200, response_ms=20, error=None),
        )

        capture_v2 = AuthCapture(
            base_url=system.login_url,
            current_url=f"{system.base_url}/#/dashboard",
            storage_state={"cookies": [{"name": "sid", "value": "v2"}], "origins": []},
            cookies=[{"name": "sid", "value": "v2"}],
            local_storage={"token": "token-v2"},
            session_storage={"refresh_token": "refresh-v2"},
            request_headers={"authorization": "Bearer token-v2"},
            authorization="Bearer token-v2",
        )
        analysis_v2 = AuthAnalysis(
            auth_mode="hybrid",
            playback_strategy="hybrid",
            authorization_source="request_header",
            authorization_schema="Bearer",
            authorization_value="Bearer token-v2",
            auth_fingerprint="fp-v2",
        )
        state_id_v2 = await service._save_state(
            system,
            capture_v2,
            analysis_v2,
            ValidationResult(is_valid=True, status_code=200, response_ms=35, error=None),
        )

        states = (
            await session.exec(
                select(StorageState)
                .where(StorageState.system_id == system.id)
                .order_by(StorageState.created_at.desc(), StorageState.id.desc())
            )
        ).all()
        persisted_system = await session.get(WebSystem, system.id)

    assert state_id_v2 == state_id_v1
    assert len(states) == 1
    assert states[0].is_valid is True
    assert states[0].storage_state["cookies"][0]["value"] == "v2"
    assert states[0].validate_response_ms == 35
    assert persisted_system is not None
    assert persisted_system.latest_valid_state_id == state_id_v1


@pytest.mark.asyncio
async def test_crawl_service_writes_success_task_log(tmp_path: Path, monkeypatch):
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system = await _create_system(with_credentials=True)
    await _create_valid_state(system.id)

    async with session_scope() as session:
        service = CrawlService(session)

        async def fake_run_crawler_script(**_kwargs):
            output = tmp_path / "crawl-output.json"
            payload = {
                "meta": {"state_valid": True},
                "menus": [],
                "pages": [],
                "stats": {},
            }
            output.write_text(json.dumps(payload), encoding="utf-8")
            return output

        async def fake_persist_payload(_system_id, _payload):
            return {"menus_saved": 1, "pages_saved": 2, "elements_saved": 3}

        monkeypatch.setattr(service, "_run_crawler_script", fake_run_crawler_script)
        monkeypatch.setattr(service, "_persist_payload", fake_persist_payload)

        result = await service.run_by_sys_code(system.sys_code)

    assert result.status == "success"
    log = await _latest_task_log(system.id, "crawl_menu")
    assert log is not None
    assert log.status == "success"
    assert log.changed is True
    assert log.pages_found == 2
    assert log.elements_found == 3
    assert log.duration_ms is not None


@pytest.mark.asyncio
async def test_crawl_service_skips_persist_when_payload_unchanged(tmp_path: Path, monkeypatch):
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system = await _create_system(with_credentials=True)
    await _create_valid_state(system.id)
    payload = {
        "meta": {"state_valid": True},
        "menus": [{"title": "分析页", "route_path": "/analytics", "target_url": "https://example.com/#/analytics"}],
        "pages": [
            {
                "url_pattern": "/analytics",
                "target_url": "https://example.com/#/analytics",
                "elements": [{"tag_name": "button", "element_type": "action_btn", "dom_css_path": "div > button"}],
            }
        ],
    }
    payload_fp = _build_payload_fingerprint(payload)
    old_crawled_at = datetime.now(timezone.utc) - timedelta(days=10)

    async with session_scope() as session:
        page = AppPage(
            system_id=system.id,
            url_pattern="/analytics",
            crawled_at=old_crawled_at,
            is_crawled=True,
        )
        session.add(page)
        await session.flush()
        existing_page_id = page.id

    async with session_scope() as session:
        service = CrawlService(session)

        async def fake_run_crawler_script(**_kwargs):
            output = tmp_path / "crawl-output-unchanged.json"
            output.write_text(json.dumps(payload), encoding="utf-8")
            return output

        async def fake_persist_payload(*_args, **_kwargs):
            raise AssertionError("_persist_payload should not be called when payload is unchanged")

        async def fake_existing_fp(_system_id):
            return payload_fp

        async def fake_snapshot_counts(_system_id):
            return {"menus": 12, "pages": 6, "elements": 52}

        monkeypatch.setattr(service, "_run_crawler_script", fake_run_crawler_script)
        monkeypatch.setattr(service, "_persist_payload", fake_persist_payload)
        monkeypatch.setattr(service, "_build_existing_snapshot_fingerprint", fake_existing_fp)
        monkeypatch.setattr(service, "_current_snapshot_counts", fake_snapshot_counts)

        result = await service.run_by_sys_code(system.sys_code)

    assert result.status == "success"
    assert result.menus_saved == 0
    assert result.pages_saved == 0
    assert result.elements_saved == 0
    assert "unchanged" in result.message.lower()
    log = await _latest_task_log(system.id, "crawl_menu")
    assert log is not None
    assert log.status == "success"
    assert log.changed is False
    assert log.pages_found == 6
    assert log.elements_found == 52
    async with session_scope() as session:
        refreshed_page = await session.get(AppPage, existing_page_id)
    assert refreshed_page is not None
    assert refreshed_page.crawled_at is not None
    assert refreshed_page.crawled_at > old_crawled_at


@pytest.mark.asyncio
async def test_crawl_service_writes_failed_task_log(monkeypatch):
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system = await _create_system(with_credentials=True)
    await _create_valid_state(system.id)

    async with session_scope() as session:
        service = CrawlService(session)

        async def fake_run_crawler_script(**_kwargs):
            raise RuntimeError("crawl exploded")

        monkeypatch.setattr(service, "_run_crawler_script", fake_run_crawler_script)

        result = await service.run_by_sys_code(system.sys_code)

    assert result.status == "failed"
    log = await _latest_task_log(system.id, "crawl_menu")
    assert log is not None
    assert log.status == "failed"
    assert "crawl exploded" in (log.error_message or "")
    assert log.duration_ms is not None


@pytest.mark.asyncio
async def test_crawl_service_preserves_existing_snapshot_for_low_confidence_payload(tmp_path: Path, monkeypatch):
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system = await _create_system(with_credentials=True)
    await _create_valid_state(system.id)
    payload = {
        "meta": {
            "state_valid": True,
            "coverage_score": 0.2,
            "failure_categories": ["payload_low_confidence"],
            "framework_detection": {"framework_type": "react"},
            "route_extraction": {"extractor_chain": ["react_runtime", "bundle_scripts"]},
        },
        "menus": [{"title": "分析页", "route_path": "/analytics", "target_url": "https://example.com/#/analytics"}],
        "pages": [{"url_pattern": "/analytics", "target_url": "https://example.com/#/analytics", "elements": []}],
    }

    async with session_scope() as session:
        service = CrawlService(session)

        async def fake_run_crawler_script(**_kwargs):
            output = tmp_path / "crawl-output-low-confidence.json"
            output.write_text(json.dumps(payload), encoding="utf-8")
            return output

        async def fake_existing_fp(_system_id):
            return "existing-fp"

        async def fake_snapshot_counts(_system_id):
            return {"menus": 7, "pages": 3, "elements": 28}

        async def fake_persist_payload(*_args, **_kwargs):
            raise AssertionError("_persist_payload should not run for low-confidence overwrite guard")

        monkeypatch.setattr(service, "_run_crawler_script", fake_run_crawler_script)
        monkeypatch.setattr(service, "_build_existing_snapshot_fingerprint", fake_existing_fp)
        monkeypatch.setattr(service, "_current_snapshot_counts", fake_snapshot_counts)
        monkeypatch.setattr(service, "_persist_payload", fake_persist_payload)

        result = await service.run_by_sys_code(system.sys_code)

    assert result.status == "success"
    assert result.degraded is True
    assert result.quality_score == 0.2
    assert result.framework_detected == "react"
    assert result.extractor_chain == ["react_runtime", "bundle_scripts"]
    assert result.failure_categories == ["payload_low_confidence"]
    assert "Preserved existing snapshot" in result.message


@pytest.mark.asyncio
async def test_crawl_service_strict_mode_fails_low_confidence_payload(tmp_path: Path, monkeypatch):
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system = await _create_system(with_credentials=True)
    await _create_valid_state(system.id)
    payload = {
        "meta": {
            "state_valid": True,
            "coverage_score": 0.1,
            "failure_categories": ["payload_low_confidence"],
            "framework_detection": {"framework_type": "vue2"},
            "route_extraction": {"extractor_chain": ["vue2_runtime"]},
        },
        "menus": [{"title": "系统管理", "route_path": "/system", "target_url": "https://example.com/#/system"}],
        "pages": [{"url_pattern": "/system", "target_url": "https://example.com/#/system", "elements": []}],
    }

    async with session_scope() as session:
        service = CrawlService(session)

        async def fake_run_crawler_script(**_kwargs):
            output = tmp_path / "crawl-output-strict-low-confidence.json"
            output.write_text(json.dumps(payload), encoding="utf-8")
            return output

        async def fake_persist_payload(*_args, **_kwargs):
            raise AssertionError("_persist_payload should not run in strict low-confidence flow")

        monkeypatch.setattr(service, "_run_crawler_script", fake_run_crawler_script)
        monkeypatch.setattr(service, "_persist_payload", fake_persist_payload)

        result = await service.run_by_sys_code(system.sys_code, strict_mode=True)

    assert result.status == "failed"
    assert result.degraded is True
    assert result.quality_score == 0.1
    assert "Strict mode rejected" in result.message


@pytest.mark.asyncio
async def test_persist_payload_reuses_existing_menu_row_by_route_path():
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system = await _create_system(with_credentials=True)

    async with session_scope() as session:
        existing_menu = NavMenu(
            system_id=system.id,
            title="旧菜单",
            text_breadcrumb="旧路径 > 关于我们",
            menu_order=9,
            menu_level=2,
            path_indexes=[9, 9],
            node_type="page",
            target_url=f"{system.base_url}/#/layout/about",
            route_path="/layout/about",
            source="dom",
        )
        session.add(existing_menu)
        await session.flush()
        existing_menu_id = existing_menu.id

    payload = {
        "meta": {"state_valid": True},
        "menus": [
            {
                "node_id": "menu-1",
                "title": "关于我们",
                "text_breadcrumb": "控制台路由 > 关于我们",
                "menu_order": 1,
                "menu_level": 1,
                "path_indexes": [1],
                "node_type": "page",
                "target_url": f"{system.base_url}/#/layout/about",
                "route_path": "/layout/about",
                "source": "vue3_runtime",
            }
        ],
        "pages": [],
    }

    async with session_scope() as session:
        service = CrawlService(session)
        result = await service._persist_payload(system.id, payload)
        assert result["menus_saved"] == 1

    async with session_scope() as session:
        menus = (
            await session.exec(
                select(NavMenu)
                .where(NavMenu.system_id == system.id, NavMenu.route_path == "/layout/about")
            )
        ).all()

    assert len(menus) == 1
    assert menus[0].id == existing_menu_id
    assert menus[0].title == "关于我们"
    assert menus[0].source == "vue3_runtime"
