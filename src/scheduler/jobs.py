"""Scheduled auth refresh and crawl jobs."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger
from sqlmodel import select

from src.models.database import session_scope
from src.models.web_system import WebSystem
from src.scheduler.scheduler import scheduler_manager
from src.services.auth_service import AuthService
from src.services.crawl_service import CrawlService


async def refresh_auth_job(sys_code: str) -> None:
    async with session_scope() as session:
        service = AuthService(session)
        await service.refresh_by_sys_code(sys_code)


async def crawl_menu_job(sys_code: str) -> None:
    async with session_scope() as session:
        service = CrawlService(session)
        await service.run_by_sys_code(sys_code)


async def sync_scheduler_jobs() -> int:
    """Load active systems from DB and upsert auth + crawl cron jobs."""

    scheduler = scheduler_manager.scheduler

    async with session_scope() as session:
        rows = await session.exec(select(WebSystem).where(WebSystem.is_active.is_(True)))
        systems = rows.all()

    for system in systems:
        auth_trigger = CronTrigger.from_crontab(system.auth_cron or "0 */6 * * *", timezone="Asia/Shanghai")
        scheduler.add_job(
            refresh_auth_job,
            trigger=auth_trigger,
            id=f"auth_refresh:{system.sys_code}",
            args=[system.sys_code],
            replace_existing=True,
            misfire_grace_time=300,
        )

        crawl_trigger = CronTrigger.from_crontab(system.crawl_cron or "0 2 * * *", timezone="Asia/Shanghai")
        scheduler.add_job(
            crawl_menu_job,
            trigger=crawl_trigger,
            id=f"menu_crawl:{system.sys_code}",
            args=[system.sys_code],
            replace_existing=True,
            misfire_grace_time=300,
        )

    return len(systems)
