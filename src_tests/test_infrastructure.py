"""Test infrastructure initialization."""

import asyncio

from src.config.settings import get_settings
from src.infrastructure.logging import get_logger
from src.infrastructure.redis_client import RedisClient
from src.infrastructure.sentry_client import init_sentry


async def test_infrastructure(monkeypatch):
    """Test infrastructure components."""
    monkeypatch.setenv("ENCRYPTION_KEY", "test-infra-encryption-key")
    get_settings.cache_clear()
    settings = get_settings()

    # Test logging
    logger = get_logger("test")
    logger.info("Testing loguru logger")

    # Test Redis client
    redis = RedisClient()

    # Test Sentry initialization
    init_sentry()

    print("✓ Settings loaded successfully")
    print(f"✓ Logger configured: {type(logger)}")
    print(f"✓ Redis client ready: {redis.client}")
    print("✓ Sentry initialized")


if __name__ == "__main__":
    asyncio.run(test_infrastructure())
