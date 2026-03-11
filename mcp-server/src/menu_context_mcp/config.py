"""Runtime settings for the standalone MCP server."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings for MCP context service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://aiops:AIOps!1234@127.0.0.1:5432/navai",
        validation_alias=AliasChoices("MCP_DATABASE_URL", "DATABASE_URL"),
    )
    database_schema: str = Field(
        default="navai",
        validation_alias=AliasChoices("MCP_DATABASE_SCHEMA", "DATABASE_SCHEMA"),
    )

    database_pool_size: int = Field(default=5, validation_alias=AliasChoices("MCP_DB_POOL_SIZE", "DATABASE_POOL_SIZE"))
    database_max_overflow: int = Field(
        default=10,
        validation_alias=AliasChoices("MCP_DB_MAX_OVERFLOW", "DATABASE_MAX_OVERFLOW"),
    )
    database_pool_timeout: int = Field(
        default=30,
        validation_alias=AliasChoices("MCP_DB_POOL_TIMEOUT", "DATABASE_POOL_TIMEOUT"),
    )

    default_min_stability_score: float = Field(default=0.7, validation_alias="MCP_MIN_STABILITY_SCORE")
    default_freshness_hours: int = Field(default=168, validation_alias="MCP_FRESHNESS_HOURS")
    max_candidates_per_stage: int = Field(default=20, validation_alias="MCP_MAX_CANDIDATES_PER_STAGE")

    mcp_server_name: str = Field(default="web-router-map-context", validation_alias="MCP_SERVER_NAME")
    mcp_transport: Literal["stdio", "http", "sse", "streamable-http"] = Field(
        default="stdio",
        validation_alias="MCP_TRANSPORT",
    )
    mcp_host: str = Field(default="127.0.0.1", validation_alias="MCP_HOST")
    mcp_port: int = Field(default=8765, validation_alias="MCP_PORT")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias="MCP_LOG_LEVEL",
    )


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings instance."""

    return Settings()
