"""Redis client configuration with distributed lock helper."""

from __future__ import annotations

from redis.asyncio import Redis

from src.config.settings import get_settings


class RedisClient:
    """Singleton async Redis client wrapper."""

    _client: Redis | None = None

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def client(self) -> Redis:
        if RedisClient._client is None:
            RedisClient._client = Redis.from_url(
                self.settings.redis_url,
                decode_responses=True,
            )
        return RedisClient._client

    async def close(self) -> None:
        if RedisClient._client is not None:
            await RedisClient._client.close()
            RedisClient._client = None

    async def acquire_lock(self, lock_name: str, timeout: int | None = None) -> bool:
        key = f"{self.settings.redis_lock_prefix}{lock_name}"
        ttl = timeout or self.settings.redis_lock_timeout
        return bool(await self.client.set(key, "1", nx=True, ex=ttl))

    async def release_lock(self, lock_name: str) -> None:
        key = f"{self.settings.redis_lock_prefix}{lock_name}"
        await self.client.delete(key)
