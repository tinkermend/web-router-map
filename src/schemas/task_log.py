"""Schemas for task log query endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TaskLogItem(BaseModel):
    """One task execution record."""

    task_type: str
    task_id: str | None
    status: str
    retry_count: int | None
    target_url: str | None
    error_message: str | None
    pages_found: int | None
    elements_found: int | None
    duration_ms: int | None
    started_at: datetime | None
    finished_at: datetime | None


class TaskLogListResponse(BaseModel):
    """Task log list payload for one system."""

    sys_code: str
    total: int
    items: list[TaskLogItem]
