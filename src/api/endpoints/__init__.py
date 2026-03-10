"""Endpoint modules."""

from src.api.endpoints.auth import router as auth_router
from src.api.endpoints.health import router as health_router

__all__ = ["auth_router", "health_router"]
