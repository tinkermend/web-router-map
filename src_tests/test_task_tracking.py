from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import select

from src.config.settings import get_settings
from src.crawler.auth_crawler import AuthCapture
from src.models.crawl_log import CrawlLog
from src.models.database import close_db, init_db, ping_db, session_scope
from src.models.storage_state import StorageState
from src.models.web_system import WebSystem
from src.scheduler.locks import distributed_lock
from src.services.auth_service import AuthService
from src.services.crawl_service import CrawlService
from src.services.validator_service import ValidationResult


@pytest.fixture(autouse=True)
async def _prepare_env(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "test-task-tracker-key")
    monkeypatch.setenv("AUTH_MAX_RETRIES", "1")
    get_settings.cache_clear()
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")
    await init_db()
    yield
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
    assert log.pages_found == 2
    assert log.elements_found == 3
    assert log.duration_ms is not None


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
