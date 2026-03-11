"""Core retrieval service implementing SQL recall + rerank + trim policy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from menu_context_mcp.config import Settings
from menu_context_mcp.db import session_scope
from menu_context_mcp.observability import log_retrieval_event
from menu_context_mcp.ranking import explain_score, score_candidate
from menu_context_mcp.repository import ContextRepository, dedupe_candidates
from menu_context_mcp.schemas import (
    ContextQuery,
    ContextResponse,
    FreshnessContext,
    LocatorContext,
    LocatorRecord,
    PageCandidate,
    PageContext,
    ScoredCandidate,
    StorageStateContext,
    StorageStateQuery,
    StorageStateResponse,
    SystemContext,
    TraceItem,
)


class RepositoryProtocol(Protocol):
    async def resolve_system(self, session, system_keyword: str): ...

    async def fetch_exact_candidates(self, session, *, system_id, query: ContextQuery, min_stability_score: float, limit: int): ...

    async def fetch_fuzzy_candidates(self, session, *, system_id, query: ContextQuery, min_stability_score: float, limit: int): ...

    async def fetch_semantic_candidates(
        self,
        session,
        *,
        system_id,
        query: ContextQuery,
        min_stability_score: float,
        limit: int,
    ): ...

    async def fetch_locators(self, session, *, page_id, min_stability_score: float, limit: int): ...

    async def fetch_valid_storage_state(self, session, system_id: str): ...


class ContextRetrievalService:
    """Retrieve context for AI using staged recall, scoring and hard trimming."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: RepositoryProtocol | None = None,
        session_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository or ContextRepository()
        self.session_provider = session_provider or session_scope

    async def get_page_playwright_context(self, query: ContextQuery) -> ContextResponse:
        min_stability = query.min_stability_score or self.settings.default_min_stability_score
        freshness_hours = query.freshness_hours or self.settings.default_freshness_hours

        async with self.session_provider() as session:
            system = await self.repository.resolve_system(session, query.system_keyword)
            if system is None:
                response = ContextResponse(
                    status="system_not_found",
                    stale_context=True,
                    reasons=["system_not_found"],
                )
                self._log_query(query=query, response=response, trace=[])
                return response

            candidates: list[PageCandidate] = []
            candidates.extend(
                await self.repository.fetch_exact_candidates(
                    session,
                    system_id=system.id,
                    query=query,
                    min_stability_score=min_stability,
                    limit=self.settings.max_candidates_per_stage,
                )
            )
            candidates.extend(
                await self.repository.fetch_fuzzy_candidates(
                    session,
                    system_id=system.id,
                    query=query,
                    min_stability_score=min_stability,
                    limit=self.settings.max_candidates_per_stage,
                )
            )

            deduped = dedupe_candidates(candidates)
            if len(deduped) < 3:
                semantic = await self.repository.fetch_semantic_candidates(
                    session,
                    system_id=system.id,
                    query=query,
                    min_stability_score=min_stability,
                    limit=self.settings.max_candidates_per_stage,
                )
                deduped = dedupe_candidates([*deduped, *semantic])

            if not deduped:
                response = ContextResponse(
                    status="need_recrawl",
                    stale_context=True,
                    system=SystemContext(
                        sys_code=system.sys_code,
                        name=system.name,
                        base_url=system.base_url,
                        framework_type=system.framework_type,
                        health_status=system.health_status,
                        state_valid=system.health_status not in {"offline", "auth_failed"},
                    ),
                    reasons=["no_page_candidate", "need_recollect"],
                    constraints=self._build_constraints(min_stability),
                )
                self._log_query(query=query, response=response, trace=[])
                return response

            now = datetime.now(timezone.utc)
            scored_candidates: list[ScoredCandidate] = [
                score_candidate(
                    query=query,
                    candidate=item,
                    system_match_score=system.match_score,
                    freshness_hours=freshness_hours,
                    now=now,
                )
                for item in deduped
            ]
            scored_candidates.sort(
                key=lambda x: (
                    -x.total_score,
                    x.stale_context,
                    -x.candidate.max_locator_stability,
                    -x.candidate.stage_rank,
                )
            )

            primary = scored_candidates[0]
            fallback = scored_candidates[1 : 1 + query.max_fallback_pages]

            locators: list[LocatorRecord] = []
            if primary.candidate.page_id is not None:
                locators = await self.repository.fetch_locators(
                    session,
                    page_id=primary.candidate.page_id,
                    min_stability_score=min_stability,
                    limit=query.max_locators,
                )

            status = "ok"
            reasons: list[str] = []
            if primary.stale_context:
                reasons.append("primary_page_stale")
            if not locators:
                status = "need_recrawl"
                reasons.append("no_stable_locator")

            trace_items = self._build_trace(scored_candidates)
            response = ContextResponse(
                status=status,
                stale_context=primary.stale_context,
                system=SystemContext(
                    sys_code=system.sys_code,
                    name=system.name,
                    base_url=system.base_url,
                    framework_type=system.framework_type,
                    health_status=system.health_status,
                    state_valid=system.health_status not in {"offline", "auth_failed"},
                ),
                target_page=self._to_page_context(primary),
                locators=[self._to_locator_context(item) for item in locators],
                fallback_pages=[self._to_page_context(item) for item in fallback],
                freshness=FreshnessContext(
                    last_crawl_at=system.last_crawl_at,
                    page_crawled_at=primary.candidate.page_crawled_at,
                    freshness_hours=freshness_hours,
                ),
                constraints=self._build_constraints(min_stability),
                reasons=reasons,
                debug_trace=trace_items if query.include_debug_trace else None,
            )
            self._log_query(query=query, response=response, trace=trace_items)
            return response

    @staticmethod
    def _to_page_context(scored: ScoredCandidate) -> PageContext:
        candidate = scored.candidate
        return PageContext(
            menu_id=str(candidate.menu_id),
            page_id=str(candidate.page_id) if candidate.page_id else None,
            title=candidate.title,
            text_breadcrumb=candidate.text_breadcrumb,
            route_path=candidate.route_path,
            target_url=candidate.target_url,
            url_pattern=candidate.url_pattern,
            menu_node_type=candidate.node_type,
            last_verified_status=candidate.last_verified_status,
            last_verified_at=candidate.last_verified_at,
            page_crawled_at=candidate.page_crawled_at,
            score=scored.total_score,
            recall_stage=candidate.stage,
        )

    @staticmethod
    def _to_locator_context(locator: LocatorRecord) -> LocatorContext:
        return LocatorContext(
            element_type=locator.element_type,
            text_content=locator.text_content,
            nearby_text=locator.nearby_text,
            playwright_locator=locator.playwright_locator or "",
            stability_score=locator.stability_score,
            locator_tier=locator.locator_tier,
            usage_description=locator.usage_description,
        )

    @staticmethod
    def _build_constraints(min_stability_score: float) -> dict[str, object]:
        return {
            "use_only_provided_locators": True,
            "verify_before_execute": True,
            "allowed_element_types": ["action_btn", "form_input", "nav_link"],
            "min_stability_score": min_stability_score,
            "max_fallback_pages": 2,
        }

    @staticmethod
    def _build_trace(scored: list[ScoredCandidate]) -> list[TraceItem]:
        return [
            TraceItem(
                menu_id=str(item.candidate.menu_id),
                title=item.candidate.text_breadcrumb or item.candidate.title,
                stage=item.candidate.stage,
                total_score=item.total_score,
                reasons=explain_score(item),
                filtered_reasons=[],
            )
            for item in scored[:10]
        ]

    @staticmethod
    def _log_query(query: ContextQuery, response: ContextResponse, trace: list[TraceItem]) -> None:
        log_retrieval_event(
            "mcp_context_retrieval",
            {
                "system_keyword": query.system_keyword,
                "page_keyword": query.page_keyword,
                "route_hint": query.route_hint,
                "status": response.status,
                "stale_context": response.stale_context,
                "reasons": response.reasons,
                "target_page": response.target_page.model_dump(mode="json") if response.target_page else None,
                "trace": [item.model_dump(mode="json") for item in trace],
            },
        )

    async def get_storage_state_for_session(self, query: StorageStateQuery) -> StorageStateResponse:
        """Get storage state for Playwright session reuse."""
        async with self.session_provider() as session:
            system = await self.repository.resolve_system(session, query.system_name)
            if system is None:
                return StorageStateResponse(
                    status="system_not_found",
                    reasons=[f"No system found matching '{query.system_name}'"],
                )

            system_context = SystemContext(
                sys_code=system.sys_code,
                name=system.name,
                base_url=system.base_url,
                framework_type=system.framework_type,
                health_status=system.health_status,
                state_valid=system.health_status not in {"offline", "auth_failed"},
            )

            storage_state = await self.repository.fetch_valid_storage_state(session, str(system.id))
            if storage_state is None:
                return StorageStateResponse(
                    status="no_valid_state",
                    system=system_context,
                    reasons=["No valid storage state found for this system", "Please run authentication first"],
                )

            now = datetime.now(timezone.utc)
            if storage_state.expires_at and storage_state.expires_at < now:
                return StorageStateResponse(
                    status="state_expired",
                    system=system_context,
                    state_id=storage_state.id,
                    is_valid=False,
                    validated_at=storage_state.validated_at,
                    expires_at=storage_state.expires_at,
                    auth_mode=storage_state.auth_mode,
                    reasons=["Storage state has expired", "Please run authentication to refresh"],
                )

            state_context = StorageStateContext(
                cookies=storage_state.cookies,
                storage_state=storage_state.storage_state,
                local_storage=storage_state.local_storage,
                session_storage=storage_state.session_storage,
            )

            log_retrieval_event(
                "mcp_storage_state_retrieval",
                {
                    "system_name": query.system_name,
                    "system_code": system.sys_code,
                    "state_id": storage_state.id,
                    "auth_mode": storage_state.auth_mode,
                    "is_valid": storage_state.is_valid,
                },
            )

            return StorageStateResponse(
                status="ok",
                system=system_context,
                state=state_context,
                state_id=storage_state.id,
                is_valid=storage_state.is_valid,
                validated_at=storage_state.validated_at,
                expires_at=storage_state.expires_at,
                auth_mode=storage_state.auth_mode,
                reasons=[],
                usage_hint=self._build_usage_hint(system.base_url, storage_state.auth_mode),
            )

    @staticmethod
    def _build_usage_hint(base_url: str, auth_mode: str | None) -> str:
        hints = [
            "Use storage_state with browser.new_context(storage_state=response.state.storage_state)",
            f"Navigate to {base_url} after context creation to restore session",
        ]
        if auth_mode == "bearer":
            hints.append("For API calls, extract Authorization header from state.storage_state or use cookies")
        elif auth_mode == "cookie_session":
            hints.append("Session is cookie-based, storage_state injection should be sufficient")
        return "; ".join(hints)
