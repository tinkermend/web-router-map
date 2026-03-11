"""Database helpers for MCP retrieval service."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from menu_context_mcp.config import get_settings

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _validated_schema(schema: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema identifier: {schema!r}")
    return schema


def _build_engine() -> AsyncEngine:
    settings = get_settings()
    schema = _validated_schema(settings.database_schema)

    connect_args: dict = {}
    if settings.database_url.startswith("postgresql+asyncpg://"):
        connect_args["server_settings"] = {"search_path": f"{schema},public"}

    return create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(bind=get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Read-only session scope for retrieval operations."""

    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


async def close_db() -> None:
    """Gracefully close pooled connections."""

    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
