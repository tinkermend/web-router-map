"""Scheduled auth refresh jobs."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger
from sqlmodel import select

from src.models.database import session_scope
from src.models.web_system import WebSystem
from src.scheduler.scheduler import scheduler_manager
from src.services.auth_service import AuthService


async def refresh_auth_job(sys_code: str) -> None:
    async with session_scope() as session:
        service = AuthService(session)
        await service.refresh_by_sys_code(sys_code)


async def sync_auth_jobs() -> int:
    """Load active systems from DB and upsert auth cron jobs."""

    scheduler = scheduler_manager.scheduler

    async with session_scope() as session:
        rows = await session.exec(select(WebSystem).where(WebSystem.is_active.is_(True)))
        systems = rows.all()

    for system in systems:
        trigger = CronTrigger.from_crontab(system.auth_cron or "0 */6 * * *", timezone="Asia/Shanghai")
        scheduler.add_job(
            refresh_auth_job,
            trigger=trigger,
            id=f"auth_refresh:{system.sys_code}",
            args=[system.sys_code],
            replace_existing=True,
            misfire_grace_time=300,
        )

    return len(systems)
