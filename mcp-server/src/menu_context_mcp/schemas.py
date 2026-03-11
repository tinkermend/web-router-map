"""Input/output contracts and internal models for context retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

StageName = Literal["exact", "fuzzy", "semantic"]


class ContextQuery(BaseModel):
    """User intent parsed by MCP tool."""

    system_keyword: str = Field(min_length=1, max_length=200)
    page_keyword: str | None = Field(default=None, max_length=200)
    menu_keyword: str | None = Field(default=None, max_length=200)
    route_hint: str | None = Field(default=None, max_length=300)

    max_locators: int = Field(default=10, ge=5, le=15)
    max_fallback_pages: int = Field(default=2, ge=0, le=2)
    min_stability_score: float | None = Field(default=None, ge=0.0, le=1.0)
    freshness_hours: int | None = Field(default=None, ge=1, le=24 * 30)

    include_debug_trace: bool = False

    @model_validator(mode="after")
    def _normalize_keywords(self) -> "ContextQuery":
        self.system_keyword = self.system_keyword.strip()
        self.page_keyword = (self.page_keyword or "").strip() or None
        self.menu_keyword = (self.menu_keyword or "").strip() or None
        self.route_hint = (self.route_hint or "").strip() or None

        if self.page_keyword is None and self.menu_keyword is not None:
            self.page_keyword = self.menu_keyword

        if self.page_keyword is None and self.route_hint is None:
            raise ValueError("At least one of page_keyword/menu_keyword/route_hint must be provided.")
        return self


@dataclass(slots=True)
class SystemRecord:
    id: UUID
    sys_code: str
    name: str
    base_url: str
    framework_type: str
    health_status: str
    last_crawl_at: datetime | None
    match_score: float


@dataclass(slots=True)
class PageCandidate:
    menu_id: UUID
    page_id: UUID | None
    title: str
    text_breadcrumb: str | None
    node_type: str | None
    target_url: str | None
    route_path: str | None
    url_pattern: str | None
    page_title: str | None
    page_summary: str | None
    page_crawled_at: datetime | None
    last_verified_status: str | None
    last_verified_at: datetime | None
    avg_locator_stability: float
    max_locator_stability: float
    stable_locator_count: int
    stage: StageName
    stage_rank: float


@dataclass(slots=True)
class LocatorRecord:
    id: UUID
    element_type: str | None
    text_content: str | None
    nearby_text: str | None
    playwright_locator: str | None
    stability_score: float
    locator_tier: str | None
    usage_description: str | None


@dataclass(slots=True)
class ScoredCandidate:
    candidate: PageCandidate
    total_score: float
    system_match_score: float
    page_text_match_score: float
    route_match_score: float
    freshness_score: float
    locator_stability_score: float
    stale_context: bool


class SystemContext(BaseModel):
    sys_code: str
    name: str
    base_url: str
    framework_type: str
    health_status: str
    state_valid: bool


class PageContext(BaseModel):
    menu_id: str
    page_id: str | None = None
    title: str
    text_breadcrumb: str | None = None
    route_path: str | None = None
    target_url: str | None = None
    url_pattern: str | None = None
    menu_node_type: str | None = None
    last_verified_status: str | None = None
    last_verified_at: datetime | None = None
    page_crawled_at: datetime | None = None
    score: float
    recall_stage: StageName


class LocatorContext(BaseModel):
    element_type: str | None = None
    text_content: str | None = None
    nearby_text: str | None = None
    playwright_locator: str
    stability_score: float
    locator_tier: str | None = None
    usage_description: str | None = None


class FreshnessContext(BaseModel):
    last_crawl_at: datetime | None = None
    page_crawled_at: datetime | None = None
    freshness_hours: int


class TraceItem(BaseModel):
    menu_id: str
    title: str
    stage: StageName
    total_score: float
    reasons: list[str] = Field(default_factory=list)
    filtered_reasons: list[str] = Field(default_factory=list)


class ContextResponse(BaseModel):
    status: Literal["ok", "system_not_found", "page_not_found", "need_recrawl"]
    stale_context: bool
    system: SystemContext | None = None
    target_page: PageContext | None = None
    locators: list[LocatorContext] = Field(default_factory=list)
    fallback_pages: list[PageContext] = Field(default_factory=list)
    freshness: FreshnessContext | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    debug_trace: list[TraceItem] | None = None


# =============================================
# Storage State Query Models
# =============================================

class StorageStateQuery(BaseModel):
    """Query parameters for storage state retrieval."""

    system_name: str = Field(min_length=1, max_length=200, description="System name keyword for fuzzy matching")

    @model_validator(mode="after")
    def _normalize(self) -> "StorageStateQuery":
        self.system_name = self.system_name.strip()
        return self


class StorageStateContext(BaseModel):
    """Storage state data for Playwright session reuse."""

    cookies: list[dict[str, Any]] = Field(default_factory=list, description="Browser cookies")
    storage_state: dict[str, Any] = Field(default_factory=dict, description="Playwright storage_state() snapshot")
    local_storage: dict[str, Any] = Field(default_factory=dict, description="localStorage key-value pairs")
    session_storage: dict[str, Any] = Field(default_factory=dict, description="sessionStorage key-value pairs")


class StorageStateResponse(BaseModel):
    """Response containing storage state for session reuse."""

    status: Literal["ok", "system_not_found", "no_valid_state", "state_expired"]
    system: SystemContext | None = None
    state: StorageStateContext | None = None
    state_id: str | None = None
    is_valid: bool = False
    validated_at: datetime | None = None
    expires_at: datetime | None = None
    auth_mode: str | None = None
    reasons: list[str] = Field(default_factory=list)
    usage_hint: str | None = Field(
        default=None,
        description="Hint for using this data in Playwright scripts (only present when status='ok')"
    )
