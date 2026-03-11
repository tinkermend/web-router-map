"""Crawl execution log model."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, text
from sqlmodel import Field, SQLModel


class CrawlLog(SQLModel, table=True):
    """Task-level execution logs for auth/crawl jobs."""

    __tablename__ = "crawl_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    system_id: UUID = Field(foreign_key="web_systems.id", index=True)
    task_type: str = Field(max_length=50)
    task_id: str | None = Field(default=None, max_length=100)
    status: str = Field(max_length=20)
    retry_count: int | None = None
    target_url: str | None = Field(default=None, max_length=500)
    error_message: str | None = None
    error_stack: str | None = None
    changed: bool | None = None
    pages_found: int | None = None
    elements_found: int | None = None
    duration_ms: int | None = None
    sentry_event_id: str | None = Field(default=None, max_length=100)
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
