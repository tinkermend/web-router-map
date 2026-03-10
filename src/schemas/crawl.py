"""Pydantic schemas for crawl endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CrawlRunRequest(BaseModel):
    """Request payload for menu crawl run."""

    headed: bool = False
    timeout_ms: int = 45_000
    max_pages: int = 10
    max_elements_per_page: int = 180
    max_modal_triggers: int = 8
    expand_rounds: int = 6
    menu_selector: str = ""
    home_url: str = ""


class CrawlRunResponse(BaseModel):
    """Result summary of a crawl execution."""

    sys_code: str
    status: str
    message: str
    crawl_log_id: UUID | None
    auth_triggered: bool
    menus_saved: int
    pages_saved: int
    elements_saved: int
    output_path: str | None
    started_at: datetime
    finished_at: datetime
