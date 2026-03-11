from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.scheduler import jobs


def test_build_cron_trigger_returns_none_for_invalid_value():
    trigger = jobs._build_cron_trigger("bad-cron", sys_code="demo", task_type="auth")
    assert trigger is None


def test_build_cron_trigger_returns_trigger_for_valid_value():
    trigger = jobs._build_cron_trigger("0 */6 * * *", sys_code="demo", task_type="auth")
    assert trigger is not None


@pytest.mark.asyncio
async def test_sync_scheduler_jobs_skips_invalid_job_cron(monkeypatch):
    added_ids: list[str] = []

    class DummyScheduler:
        def add_job(self, _func, *, trigger, id, args, replace_existing, misfire_grace_time):
            assert trigger is not None
            assert args
            assert replace_existing is True
            assert misfire_grace_time == 300
            added_ids.append(id)

    systems = [
        SimpleNamespace(
            sys_code="ok-system",
            is_active=True,
            auth_cron="0 */6 * * *",
            crawl_cron="invalid cron expression",
        )
    ]

    class _Rows:
        def all(self):
            return systems

    class _Session:
        async def exec(self, _stmt):
            return _Rows()

    @asynccontextmanager
    async def _fake_session_scope():
        yield _Session()

    monkeypatch.setattr(jobs, "scheduler_manager", SimpleNamespace(scheduler=DummyScheduler()))
    monkeypatch.setattr(jobs, "session_scope", _fake_session_scope)
    monkeypatch.setattr(
        jobs,
        "get_settings",
        lambda: SimpleNamespace(
            default_auth_cron="0 */6 * * *",
            default_crawl_cron="0 2 * * *",
        ),
    )

    synced_count = await jobs.sync_scheduler_jobs()
    assert synced_count == 1
    assert "auth_refresh:ok-system" in added_ids
    assert "menu_crawl:ok-system" not in added_ids
