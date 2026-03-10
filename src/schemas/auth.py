"""Pydantic schemas for auth endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AuthRefreshRequest(BaseModel):
    """Request body for manual auth refresh trigger."""

    headed: bool = False
    timeout_ms: int | None = None


class AuthRefreshResponse(BaseModel):
    """Result summary for auth refresh task."""

    sys_code: str
    status: str
    message: str
    state_id: UUID | None = None
    cookies_count: int = 0
    local_storage_count: int = 0
    session_storage_count: int = 0
    authorization_captured: bool = False
    validated: bool | None = None
    validate_status_code: int | None = None
    started_at: datetime
    finished_at: datetime


class LatestStateResponse(BaseModel):
    """Latest valid state overview for a system."""

    sys_code: str
    state_id: UUID | None
    is_valid: bool | None
    auth_mode: str | None
    playback_strategy: str | None
    validated_at: datetime | None
    last_auth_at: datetime | None
    request_headers: dict[str, str] = Field(default_factory=dict)
    cookies_count: int = 0


class ManualStatePayload(BaseModel):
    """Manual state injection payload."""

    storage_state: dict[str, Any]
    cookies: list[dict[str, Any]] = Field(default_factory=list)
    local_storage: dict[str, Any] = Field(default_factory=dict)
    session_storage: dict[str, Any] = Field(default_factory=dict)
    request_headers: dict[str, str] = Field(default_factory=dict)
    authorization_value: str | None = None
    authorization_schema: str | None = None
    playback_strategy: str = "auto"
