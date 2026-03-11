"""UI element model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class UIElement(SQLModel, table=True):
    """Actionable element metadata used by AI test generation."""

    __tablename__ = "ui_elements"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    page_id: UUID = Field(foreign_key="app_pages.id", index=True)
    container_id: UUID | None = Field(default=None, foreign_key="ui_containers.id")

    tag_name: str = Field(max_length=50)
    element_type: str | None = Field(default=None, max_length=50)
    text_content: str | None = None
    locators: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    playwright_locator: str | None = None
    dom_css_path: str | None = None
    locator_tier: str | None = Field(default=None, max_length=32)
    stability_score: float | None = None
    is_global_chrome: bool | None = None
    is_business_useful: bool | None = None
    nearby_text: str | None = None
    usage_description: str | None = None
    screenshot_slice_path: str | None = Field(default=None, max_length=1000)
    bounding_box: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    )
