"""UI container model."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, text
from sqlmodel import Field, SQLModel


class UIContainer(SQLModel, table=True):
    """Container regions on a page (page_body / modal)."""

    __tablename__ = "ui_containers"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    page_id: UUID = Field(foreign_key="app_pages.id", index=True)
    container_type: str = Field(max_length=50)
    title: str | None = Field(default=None, max_length=255)
    xpath_root: str | None = Field(default=None, max_length=1000)
    css_selector: str | None = Field(default=None, max_length=1000)
    trigger_element_id: UUID | None = Field(default=None, foreign_key="ui_elements.id")
    trigger_action: str | None = Field(default=None, max_length=50)
    is_dynamic: bool | None = None
    is_visible_default: bool | None = None

    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
