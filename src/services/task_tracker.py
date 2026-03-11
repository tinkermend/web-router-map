"""Unified task tracking utilities for auth/crawl jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.models.crawl_log import CrawlLog

VALID_TASK_TYPES = {"auth", "crawl_menu"}
VALID_STATUSES = {"running", "success", "failed", "skipped"}


class TaskTracker:
    """Create and update task execution logs in `crawl_logs`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(
        self,
        *,
        system_id: UUID,
        task_type: str,
        task_id: str | None = None,
        target_url: str | None = None,
        retry_count: int = 0,
    ) -> CrawlLog:
        task_type = self._normalize_task_type(task_type)
        now = _utc_now()
        log = CrawlLog(
            system_id=system_id,
            task_type=task_type,
            task_id=task_id or str(uuid4()),
            status="running",
            retry_count=retry_count,
            target_url=target_url,
            started_at=now,
            finished_at=None,
        )
        self.session.add(log)
        await self.session.commit()
        return log

    async def finish(
        self,
        *,
        log_id: UUID,
        status: str,
        retry_count: int | None = None,
        error_message: str | None = None,
        error_stack: str | None = None,
        pages_found: int | None = None,
        elements_found: int | None = None,
        sentry_event_id: str | None = None,
    ) -> CrawlLog:
        status = self._normalize_status(status)
        log = await self.session.get(CrawlLog, log_id)
        if log is None:
            raise RuntimeError(f"Task log not found: {log_id}")

        finished_at = _utc_now()
        log.status = status
        log.finished_at = finished_at
        if retry_count is not None:
            log.retry_count = retry_count
        if error_message is not None:
            log.error_message = error_message
        if error_stack is not None:
            log.error_stack = error_stack
        if pages_found is not None:
            log.pages_found = pages_found
        if elements_found is not None:
            log.elements_found = elements_found
        if sentry_event_id is not None:
            log.sentry_event_id = sentry_event_id
        if log.started_at is not None:
            log.duration_ms = int((finished_at - log.started_at).total_seconds() * 1000)

        self.session.add(log)
        await self.session.commit()
        return log

    async def list_logs(
        self,
        *,
        system_id: UUID,
        task_type: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[CrawlLog]:
        if task_type is not None:
            task_type = self._normalize_task_type(task_type)
        if status is not None:
            status = self._normalize_status(status)

        stmt = select(CrawlLog).where(CrawlLog.system_id == system_id)
        if task_type is not None:
            stmt = stmt.where(CrawlLog.task_type == task_type)
        if status is not None:
            stmt = stmt.where(CrawlLog.status == status)

        stmt = stmt.order_by(CrawlLog.started_at.desc(), CrawlLog.id.desc()).limit(limit)
        result = await self.session.exec(stmt)
        return list(result.all())

    @staticmethod
    def _normalize_task_type(value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in VALID_TASK_TYPES:
            raise ValueError(f"Invalid task_type: {value}")
        return normalized

    @staticmethod
    def _normalize_status(value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {value}")
        return normalized


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
