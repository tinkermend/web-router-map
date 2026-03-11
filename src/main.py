"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import api_router
from src.config.settings import get_settings
from src.infrastructure import init_sentry, setup_logging, setup_uvicorn_logging
from src.infrastructure.logging import get_logger
from src.models.database import close_db, init_db
from src.scheduler import distributed_lock, scheduler_manager
from src.scheduler.jobs import sync_scheduler_jobs

app_logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    setup_logging(
        log_level=settings.log_level,
        log_file=settings.log_file,
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        json_format=settings.log_json,
    )
    setup_uvicorn_logging()
    init_sentry()

    app_logger.info("Application startup initiated")
    try:
        await init_db()
        app_logger.info("Database initialized")
        scheduler_manager.start()
        if scheduler_manager.scheduler.running:
            synced_count = await sync_scheduler_jobs()
            app_logger.bind(synced_systems=synced_count).info("Scheduler jobs synchronized")

        yield
    except Exception:  # pragma: no cover - startup/runtime safety
        app_logger.exception("Application runtime crashed")
        raise
    finally:
        app_logger.info("Application shutdown initiated")
        scheduler_manager.shutdown()
        await distributed_lock.close()
        await close_db()
        app_logger.info("Application shutdown completed")


app = FastAPI(title="web-router-map", lifespan=lifespan)
app.include_router(api_router)
