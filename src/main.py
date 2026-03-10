"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import api_router
from src.models.database import close_db, init_db
from src.scheduler import distributed_lock, scheduler_manager
from src.scheduler.jobs import sync_auth_jobs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    scheduler_manager.start()
    if scheduler_manager.scheduler.running:
        await sync_auth_jobs()

    try:
        yield
    finally:
        scheduler_manager.shutdown()
        await distributed_lock.close()
        await close_db()


app = FastAPI(title="web-router-map", lifespan=lifespan)
app.include_router(api_router)
