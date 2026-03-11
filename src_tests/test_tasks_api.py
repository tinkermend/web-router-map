from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from src.api.router import api_router
from src.config.settings import get_settings
from src.models.crawl_log import CrawlLog
from src.models.database import close_db, get_session_factory, init_db, ping_db, session_scope
from src.models.web_system import WebSystem


@pytest.fixture(autouse=True)
async def _prepare_env(monkeypatch):
    test_schema = f"ut_api_{uuid4().hex[:10]}"
    monkeypatch.setenv("DATABASE_SCHEMA", test_schema)
    monkeypatch.setenv("ENCRYPTION_KEY", "test-tasks-api-key")
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


async def _create_system_with_logs() -> tuple[WebSystem, list[CrawlLog]]:
    sys_code = f"ut-api-{uuid4().hex[:8]}"
    base_url = f"https://{sys_code}.example.com"
    async with session_scope() as session:
        system = WebSystem(
            sys_code=sys_code,
            name="UT API System",
            base_url=base_url,
            login_url=f"{base_url}/login",
            login_username="user",
            login_password="pass",
            auth_mode_hint="unknown",
            playback_strategy_default="auto",
            is_active=True,
        )
        session.add(system)
        await session.flush()

        now = datetime.now(timezone.utc)
        logs = [
            CrawlLog(
                system_id=system.id,
                task_type="auth",
                task_id="auth-1",
                status="failed",
                retry_count=1,
                error_message="login failed",
                started_at=now - timedelta(minutes=3),
                finished_at=now - timedelta(minutes=2, seconds=50),
                duration_ms=10_000,
            ),
            CrawlLog(
                system_id=system.id,
                task_type="auth",
                task_id="auth-2",
                status="success",
                retry_count=0,
                started_at=now - timedelta(minutes=2),
                finished_at=now - timedelta(minutes=1, seconds=50),
                duration_ms=10_000,
            ),
            CrawlLog(
                system_id=system.id,
                task_type="crawl_menu",
                task_id="crawl-1",
                status="success",
                retry_count=0,
                pages_found=5,
                elements_found=50,
                started_at=now - timedelta(minutes=1),
                finished_at=now - timedelta(seconds=50),
                duration_ms=10_000,
            ),
        ]
        for log in logs:
            session.add(log)
        await session.flush()

        system_id = system.id

    async with session_scope() as session:
        db_system = await session.get(WebSystem, system_id)
        return db_system, logs


@pytest.mark.asyncio
async def test_tasks_logs_api_filters_and_limits():
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system, _ = await _create_system_with_logs()

    app = FastAPI()
    app.include_router(api_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/tasks/{system.sys_code}/logs",
            params={"task_type": "auth", "status": "failed", "limit": 1},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["sys_code"] == system.sys_code
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["task_type"] == "auth"
    assert body["items"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_tasks_logs_api_sorted_by_started_at_desc():
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    system, _ = await _create_system_with_logs()

    app = FastAPI()
    app.include_router(api_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/tasks/{system.sys_code}/logs",
            params={"limit": 2},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    first = body["items"][0]["started_at"]
    second = body["items"][1]["started_at"]
    assert first >= second


@pytest.mark.asyncio
async def test_tasks_logs_api_returns_404_for_unknown_sys_code():
    if not await ping_db():
        pytest.skip("PostgreSQL is not reachable in current environment.")

    app = FastAPI()
    app.include_router(api_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/tasks/not-exists/logs")

    assert resp.status_code == 404
