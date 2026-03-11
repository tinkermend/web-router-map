"""Scheduled auth refresh and crawl jobs."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger
from sqlmodel import select

from src.config.settings import get_settings
from src.infrastructure.logging import get_logger
from src.models.database import session_scope
from src.models.web_system import WebSystem
from src.scheduler.scheduler import scheduler_manager
from src.services.auth_service import AuthService
from src.services.crawl_service import CrawlService

logger = get_logger(__name__)


async def refresh_auth_job(sys_code: str) -> None:
    job_logger = logger.bind(sys_code=sys_code, task_type="auth")
    job_logger.info("Scheduled auth job triggered")
    async with session_scope() as session:
        service = AuthService(session)
        result = await service.refresh_by_sys_code(sys_code)
        job_logger.bind(status=result.status).info("Scheduled auth job completed")


async def crawl_menu_job(sys_code: str) -> None:
    job_logger = logger.bind(sys_code=sys_code, task_type="crawl_menu")
    job_logger.info("Scheduled crawl job triggered")
    async with session_scope() as session:
        service = CrawlService(session)
        result = await service.run_by_sys_code(sys_code)
        job_logger.bind(status=result.status).info("Scheduled crawl job completed")


async def sync_scheduler_jobs() -> int:
    """Load active systems from DB and upsert auth + crawl cron jobs."""

    scheduler = scheduler_manager.scheduler
    settings = get_settings()
    logger.info("Synchronizing scheduler jobs from active systems")

    async with session_scope() as session:
        rows = await session.exec(select(WebSystem).where(WebSystem.is_active.is_(True)))
        systems = rows.all()

    for system in systems:
        auth_cron = (system.auth_cron or settings.default_auth_cron).strip()
        auth_trigger = _build_cron_trigger(auth_cron, sys_code=system.sys_code, task_type="auth")
        if auth_trigger is not None:
            scheduler.add_job(
                refresh_auth_job,
                trigger=auth_trigger,
                id=f"auth_refresh:{system.sys_code}",
                args=[system.sys_code],
                replace_existing=True,
                misfire_grace_time=300,
            )

        crawl_cron = (system.crawl_cron or settings.default_crawl_cron).strip()
        crawl_trigger = _build_cron_trigger(crawl_cron, sys_code=system.sys_code, task_type="crawl_menu")
        if crawl_trigger is not None:
            scheduler.add_job(
                crawl_menu_job,
                trigger=crawl_trigger,
                id=f"menu_crawl:{system.sys_code}",
                args=[system.sys_code],
                replace_existing=True,
                misfire_grace_time=300,
            )

    logger.bind(active_systems=len(systems)).info("Scheduler job synchronization completed")
    return len(systems)


def _build_cron_trigger(cron_expr: str, *, sys_code: str, task_type: str) -> CronTrigger | None:
    try:
        return CronTrigger.from_crontab(cron_expr, timezone="Asia/Shanghai")
    except ValueError:
        logger.bind(sys_code=sys_code, task_type=task_type, cron=cron_expr).error(
            "Invalid cron expression, skipped scheduler job"
        )
        return None
