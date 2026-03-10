"""Application settings."""

from __future__ import annotations

import base64
from functools import lru_cache
from hashlib import sha256

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _build_default_encryption_key() -> str:
    """Return url-safe base64 encoded 32-byte key for AES-256-GCM."""

    digest = sha256(b"web-router-map-dev-encryption-key").digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


class Settings(BaseSettings):
    """Global application settings sourced from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://aiops:AIOps!1234@127.0.0.1:5432/navai",
        validation_alias="DATABASE_URL",
    )
    database_schema: str = Field(default="navai", validation_alias="DATABASE_SCHEMA")
    database_pool_size: int = Field(default=10, validation_alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=20, validation_alias="DATABASE_MAX_OVERFLOW")
    database_pool_timeout: int = Field(default=30, validation_alias="DATABASE_POOL_TIMEOUT")
    database_pool_recycle: int = Field(default=1800, validation_alias="DATABASE_POOL_RECYCLE")

    # API
    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")
    api_workers: int = Field(default=4, validation_alias="API_WORKERS")

    # Redis / Lock
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", validation_alias="REDIS_URL")
    redis_lock_timeout: int = Field(default=300, validation_alias="REDIS_LOCK_TIMEOUT")
    redis_lock_prefix: str = Field(default="wrm:lock:", validation_alias="REDIS_LOCK_PREFIX")

    # Playwright
    playwright_headless: bool = Field(default=True, validation_alias="PLAYWRIGHT_HEADLESS")
    playwright_timeout: int = Field(default=60_000, validation_alias="PLAYWRIGHT_TIMEOUT")
    playwright_slow_mo: int = Field(default=0, validation_alias="PLAYWRIGHT_SLOW_MO")

    # Auth refresh strategy
    auth_max_retries: int = Field(default=3, validation_alias="AUTH_MAX_RETRIES")
    auth_retry_delay_seconds: int = Field(default=5, validation_alias="AUTH_RETRY_DELAY_SECONDS")

    # Scheduler
    scheduler_enabled: bool = Field(default=False, validation_alias="SCHEDULER_ENABLED")
    default_auth_cron: str = Field(default="0 */6 * * *", validation_alias="DEFAULT_AUTH_CRON")
    default_crawl_cron: str = Field(default="0 2 * * *", validation_alias="DEFAULT_CRAWL_CRON")

    # Security
    encryption_key: str = Field(default_factory=_build_default_encryption_key, validation_alias="ENCRYPTION_KEY")

    # Monitoring
    sentry_dsn: str | None = Field(default=None, validation_alias="SENTRY_DSN")
    sentry_environment: str = Field(default="development", validation_alias="SENTRY_ENVIRONMENT")
    sentry_traces_sample_rate: float = Field(default=0.1, validation_alias="SENTRY_TRACES_SAMPLE_RATE")

    # Debug
    debug: bool = Field(default=False, validation_alias="DEBUG")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""

    return Settings()
