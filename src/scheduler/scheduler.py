"""APScheduler bootstrap for auth refresh jobs."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config.settings import get_settings


class SchedulerManager:
    """Lifecycle wrapper around APScheduler."""

    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None

    @property
    def scheduler(self) -> AsyncIOScheduler:
        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        return self._scheduler

    def start(self) -> None:
        settings = get_settings()
        if not settings.scheduler_enabled:
            return
        scheduler = self.scheduler
        if not scheduler.running:
            scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)


scheduler_manager = SchedulerManager()
