"""API dependency helpers."""

from typing import AsyncIterator

from sqlmodel.ext.asyncio.session import AsyncSession

from src.models.database import get_db_session


async def get_db() -> AsyncIterator[AsyncSession]:
    """Database dependency for FastAPI endpoints."""

    async for session in get_db_session():
        yield session

