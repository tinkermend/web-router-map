"""App page model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import ARRAY, Column, DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class AppPage(SQLModel, table=True):
    """Crawled pages and page-level metadata."""

    __tablename__ = "app_pages"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    system_id: UUID = Field(foreign_key="web_systems.id", index=True)
    menu_id: UUID | None = Field(default=None, foreign_key="nav_menus.id")

    url_pattern: str = Field(max_length=500)
    route_name: str | None = Field(default=None, max_length=255)
    page_title: str | None = Field(default=None, max_length=255)
    page_summary: str | None = None
    description: str | None = None
    keywords: list[str] | None = Field(default=None, sa_column=Column(ARRAY(Text), nullable=True))
    meta_info: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    component_path: str | None = Field(default=None, max_length=500)
    screenshot_path: str | None = Field(default=None, max_length=1000)
    is_crawled: bool | None = None
    crawled_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))

    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
