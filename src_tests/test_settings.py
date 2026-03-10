import pytest
from pydantic import ValidationError

from src.config.settings import get_settings


def _clear_database_env(monkeypatch) -> None:
    for key in [
        "DATABASE_URL",
        "DATABASE_SCHEMA",
        "DATABASE_POOL_SIZE",
        "DATABASE_MAX_OVERFLOW",
        "DATABASE_POOL_TIMEOUT",
        "DATABASE_POOL_RECYCLE",
        "REDIS_URL",
        "REDIS_LOCK_TIMEOUT",
        "REDIS_LOCK_PREFIX",
        "PLAYWRIGHT_HEADLESS",
        "PLAYWRIGHT_TIMEOUT",
        "PLAYWRIGHT_SLOW_MO",
        "AUTH_MAX_RETRIES",
        "AUTH_RETRY_DELAY_SECONDS",
        "SCHEDULER_ENABLED",
        "DEFAULT_AUTH_CRON",
        "ENCRYPTION_KEY",
        "DEBUG",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_settings_defaults(monkeypatch) -> None:
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("REDIS_LOCK_TIMEOUT", "300")
    monkeypatch.setenv("REDIS_LOCK_PREFIX", "wrm:lock:")
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "true")
    monkeypatch.setenv("PLAYWRIGHT_TIMEOUT", "60000")
    monkeypatch.setenv("PLAYWRIGHT_SLOW_MO", "0")
    monkeypatch.setenv("AUTH_MAX_RETRIES", "3")
    monkeypatch.setenv("AUTH_RETRY_DELAY_SECONDS", "5")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("DEFAULT_AUTH_CRON", "0 */6 * * *")
    monkeypatch.setenv("ENCRYPTION_KEY", "test-default-encryption-key")
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.database_url == "postgresql+asyncpg://aiops:AIOps!1234@127.0.0.1:5432/navai"
    assert settings.database_schema == "navai"
    assert settings.database_pool_size == 10
    assert settings.database_max_overflow == 20
    assert settings.database_pool_timeout == 30
    assert settings.database_pool_recycle == 1800
    assert settings.redis_url == "redis://127.0.0.1:6379/0"
    assert settings.redis_lock_timeout == 300
    assert settings.redis_lock_prefix == "wrm:lock:"
    assert settings.playwright_headless is True
    assert settings.playwright_timeout == 60_000
    assert settings.playwright_slow_mo == 0
    assert settings.auth_max_retries == 3
    assert settings.auth_retry_delay_seconds == 5
    assert settings.scheduler_enabled is False
    assert settings.default_auth_cron == "0 */6 * * *"
    assert settings.encryption_key == "test-default-encryption-key"
    assert settings.debug is False


def test_settings_env_override(monkeypatch) -> None:
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://foo:bar@localhost:5432/demo")
    monkeypatch.setenv("DATABASE_SCHEMA", "demo")
    monkeypatch.setenv("DATABASE_POOL_SIZE", "5")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "7")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT", "12")
    monkeypatch.setenv("DATABASE_POOL_RECYCLE", "99")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6380/1")
    monkeypatch.setenv("REDIS_LOCK_TIMEOUT", "99")
    monkeypatch.setenv("REDIS_LOCK_PREFIX", "lock:x:")
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "false")
    monkeypatch.setenv("PLAYWRIGHT_TIMEOUT", "1234")
    monkeypatch.setenv("PLAYWRIGHT_SLOW_MO", "50")
    monkeypatch.setenv("AUTH_MAX_RETRIES", "8")
    monkeypatch.setenv("AUTH_RETRY_DELAY_SECONDS", "12")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("DEFAULT_AUTH_CRON", "*/10 * * * *")
    monkeypatch.setenv("ENCRYPTION_KEY", "secret-key")
    monkeypatch.setenv("DEBUG", "true")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.database_url == "postgresql+asyncpg://foo:bar@localhost:5432/demo"
    assert settings.database_schema == "demo"
    assert settings.database_pool_size == 5
    assert settings.database_max_overflow == 7
    assert settings.database_pool_timeout == 12
    assert settings.database_pool_recycle == 99
    assert settings.redis_url == "redis://localhost:6380/1"
    assert settings.redis_lock_timeout == 99
    assert settings.redis_lock_prefix == "lock:x:"
    assert settings.playwright_headless is False
    assert settings.playwright_timeout == 1234
    assert settings.playwright_slow_mo == 50
    assert settings.auth_max_retries == 8
    assert settings.auth_retry_delay_seconds == 12
    assert settings.scheduler_enabled is True
    assert settings.default_auth_cron == "*/10 * * * *"
    assert settings.encryption_key == "secret-key"
    assert settings.debug is True


def test_settings_requires_encryption_key(monkeypatch) -> None:
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(ValidationError):
        get_settings()
