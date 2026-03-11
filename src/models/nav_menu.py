"""Navigation menu model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class NavMenu(SQLModel, table=True):
    """Persisted menu/tree nodes discovered from crawl."""

    __tablename__ = "nav_menus"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    system_id: UUID = Field(foreign_key="web_systems.id", index=True)
    parent_id: UUID | None = Field(default=None, foreign_key="nav_menus.id")
    node_path: str | None = None
    title: str = Field(max_length=255)
    text_breadcrumb: str | None = None
    icon: str | None = Field(default=None, max_length=128)
    menu_order: int | None = None
    menu_level: int | None = None
    path_indexes: list[int] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    node_type: str | None = Field(default=None, max_length=20)
    target_url: str | None = Field(default=None, max_length=500)
    route_path: str | None = Field(default=None, max_length=500)
    route_name: str | None = Field(default=None, max_length=255)
    playwright_locator: str | None = None
    source: str | None = Field(default=None, max_length=50)
    is_ai_primary_candidate: bool | None = None
    ai_candidate_rank: int | None = None
    last_verified_status: str | None = Field(default=None, max_length=20)
    last_verified_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    is_group: bool | None = None
    is_external: bool | None = None
    is_visible: bool | None = None

    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
