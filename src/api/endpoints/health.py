"""Service health endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from src.models.database import ping_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="Liveness and DB readiness probe")
async def health() -> dict[str, str | bool]:
    db_ok = await ping_db()
    return {"status": "ok" if db_ok else "degraded", "db": db_ok}
