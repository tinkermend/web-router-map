"""Web system configuration model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class WebSystem(SQLModel, table=True):
    """Persistent system configuration for authentication and crawling."""

    __tablename__ = "web_systems"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    sys_code: str = Field(index=True, unique=True, max_length=50)
    name: str = Field(max_length=255)
    description: str | None = None
    base_url: str = Field(max_length=500)
    api_base_url: str | None = Field(default=None, max_length=500)
    framework_type: str = Field(default="unknown", max_length=50)

    login_url: str | None = Field(default=None, max_length=500)
    login_username: str | None = Field(default=None, max_length=128)
    login_password: str | None = Field(default=None, max_length=256)
    login_script_path: str | None = Field(default=None, max_length=500)
    login_auth: str = Field(default="none", max_length=32)
    login_selectors: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=True),
    )

    auth_mode_hint: str = Field(default="unknown", max_length=20)
    playback_strategy_default: str = Field(default="auto", max_length=20)
    auth_validate_endpoint: str | None = Field(default=None, max_length=500)
    auth_cron: str | None = Field(default="0 */6 * * *", max_length=100)
    crawl_cron: str | None = Field(default="0 2 * * *", max_length=100)

    health_status: str = Field(default="unknown", max_length=20)
    is_active: bool = True
    last_auth_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    last_auth_validation_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
    )
    auth_fail_count: int = 0
    last_auth_error: str | None = None
    latest_valid_state_id: UUID | None = Field(default=None, foreign_key="storage_states.id")
    last_crawl_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))

    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
