"""Distributed lock abstraction backed by Redis."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

from redis.asyncio import Redis

from src.config.settings import get_settings


class DistributedLock:
    """Redis-first lock with in-process fallback lock."""

    def __init__(self) -> None:
        self._redis: Redis | None = None
        self._local_locks: dict[str, asyncio.Lock] = {}

    async def _get_redis(self) -> Redis | None:
        if self._redis is None:
            self._redis = Redis.from_url(get_settings().redis_url, encoding="utf-8", decode_responses=True)
        return self._redis

    @asynccontextmanager
    async def acquire(self, lock_name: str, timeout: int | None = None) -> AsyncIterator[bool]:
        settings = get_settings()
        ttl = timeout or settings.redis_lock_timeout
        key = f"{settings.redis_lock_prefix}{lock_name}"
        token = str(uuid4())

        redis_client = await self._get_redis()
        if redis_client is not None:
            try:
                acquired = await redis_client.set(key, token, nx=True, ex=ttl)
            except Exception:
                # Redis unavailable: fallback to local lock.
                acquired = None
            else:
                if not acquired:
                    yield False
                    return
                try:
                    yield True
                finally:
                    try:
                        current = await redis_client.get(key)
                        if current == token:
                            await redis_client.delete(key)
                    except Exception:
                        # Ignore release failures and let key expire by TTL.
                        pass
                return

        local_lock = self._local_locks.setdefault(key, asyncio.Lock())
        if local_lock.locked():
            yield False
            return

        await local_lock.acquire()
        try:
            yield True
        finally:
            local_lock.release()

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.close()
            self._redis = None


distributed_lock = DistributedLock()
