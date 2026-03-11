"""API router registration."""

from fastapi import APIRouter

from src.api.endpoints.auth import router as auth_router
from src.api.endpoints.crawl import router as crawl_router
from src.api.endpoints.health import router as health_router
from src.api.endpoints.tasks import router as tasks_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(crawl_router)
api_router.include_router(tasks_router)
