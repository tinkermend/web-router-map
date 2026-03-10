"""StorageState persistence model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class StorageState(SQLModel, table=True):
    """Snapshot of authenticated browser state."""

    __tablename__ = "storage_states"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    system_id: UUID = Field(foreign_key="web_systems.id", index=True)

    storage_state: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    cookies: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB, nullable=True))
    local_storage: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=True))
    session_storage: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=True))
    request_headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=True))

    auth_mode: str = Field(default="unknown", max_length=20)
    playback_strategy: str = Field(default="auto", max_length=20)
    authorization_source: str | None = Field(default=None, max_length=50)
    authorization_schema: str | None = Field(default=None, max_length=20)
    authorization_value: str | None = None
    auth_fingerprint: str | None = Field(default=None, max_length=128)

    validate_status_code: int | None = None
    validate_response_ms: int | None = None
    validate_error: str | None = None
    is_valid: bool = True
    validated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    last_used_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))

    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
