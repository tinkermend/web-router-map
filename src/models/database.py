"""Database infrastructure based on SQLModel async stack."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from src.config.settings import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validated_schema(schema: str) -> str:
    """Validate schema identifier to avoid SQL injection in DDL statements."""

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
        pool_recycle=settings.database_pool_recycle,
        pool_pre_ping=True,
        echo=settings.debug,
        connect_args=connect_args,
    )


def get_engine() -> AsyncEngine:
    """Get (or lazily create) async database engine singleton."""

    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get async session factory singleton."""

    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-friendly dependency generator for async DB session."""

    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional async session context manager with commit/rollback."""

    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping_db() -> bool:
    """Check whether database is reachable."""

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def init_db() -> None:
    """Initialize DB prerequisites in an idempotent way."""

    # Ensure table metadata is registered before create_all.
    from src.models import storage_state as _storage_state  # noqa: F401
    from src.models import web_system as _web_system  # noqa: F401

    schema = _validated_schema(get_settings().database_schema)
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "ltree"'))
        await conn.run_sync(SQLModel.metadata.create_all)


async def close_db() -> None:
    """Dispose DB engine and clear cached singletons."""

    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
