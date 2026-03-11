"""Database retrieval layer for MCP context queries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from menu_context_mcp.schemas import ContextQuery, LocatorRecord, MenuNodeRecord, PageCandidate, StageName, SystemRecord


@dataclass(slots=True)
class StorageStateRecord:
    """Storage state record from database."""
    id: str
    system_id: str
    storage_state: dict
    cookies: list
    local_storage: dict
    session_storage: dict
    auth_mode: str | None
    is_valid: bool
    validated_at: datetime | None
    expires_at: datetime | None

_ALLOWED_ELEMENT_TYPES = ("action_btn", "form_input", "nav_link")
_DYNAMIC_ID_LOCATOR_RE = re.compile(
    r"#(?:el-id-\d+(?:-\d+)?|:r\d+:?[a-z0-9_-]*|headlessui-[\w-]*-\d+|radix-[\w-]*-\d+|[a-f0-9]{10,}|[a-z0-9_-]*\d{4,}[a-z0-9_-]*)\b",
    flags=re.IGNORECASE,
)
_MODERN_PLAYWRIGHT_PREFIXES = (
    "get_by_role(",
    "get_by_text(",
    "get_by_label(",
    "get_by_placeholder(",
    "get_by_test_id(",
)

_BASE_CANDIDATE_SQL = """
SELECT
    m.id AS menu_id,
    p.id AS page_id,
    m.title,
    m.text_breadcrumb,
    m.node_type,
    m.target_url,
    m.route_path,
    p.url_pattern,
    p.page_title,
    p.page_summary,
    p.crawled_at AS page_crawled_at,
    m.last_verified_status,
    m.last_verified_at,
    COALESCE(el.avg_stability, 0) AS avg_locator_stability,
    COALESCE(el.max_stability, 0) AS max_locator_stability,
    COALESCE(el.stable_count, 0) AS stable_locator_count,
    {stage_rank_sql} AS stage_rank
FROM nav_menus AS m
LEFT JOIN app_pages AS p
    ON p.menu_id = m.id AND p.system_id = m.system_id
LEFT JOIN LATERAL (
    SELECT
        AVG(COALESCE(e.stability_score, 0)) AS avg_stability,
        MAX(COALESCE(e.stability_score, 0)) AS max_stability,
        COUNT(*) FILTER (WHERE COALESCE(e.stability_score, 0) >= :min_stability_score) AS stable_count
    FROM ui_elements AS e
    WHERE e.page_id = p.id
      AND e.element_type IN ('action_btn', 'form_input', 'nav_link')
      AND COALESCE(e.is_global_chrome, FALSE) IS FALSE
) AS el ON TRUE
WHERE m.system_id = :system_id
  AND m.node_type = 'page'
  AND ({stage_filter})
ORDER BY
    stage_rank DESC,
    COALESCE(m.ai_candidate_rank, 99999) ASC,
    COALESCE(el.max_stability, 0) DESC,
    p.crawled_at DESC NULLS LAST,
    m.updated_at DESC NULLS LAST
LIMIT :limit
"""


class ContextRepository:
    """Repository encapsulating context retrieval SQL."""

    async def resolve_system(self, session: AsyncSession, system_keyword: str) -> SystemRecord | None:
        sql = text(
            """
            SELECT
                id,
                sys_code,
                name,
                base_url,
                framework_type,
                health_status,
                last_crawl_at,
                CASE
                    WHEN LOWER(sys_code) = LOWER(:keyword) THEN 1.0
                    WHEN LOWER(name) = LOWER(:keyword) THEN 0.95
                    WHEN sys_code ILIKE :pattern THEN 0.8
                    WHEN name ILIKE :pattern THEN 0.75
                    ELSE 0.0
                END AS match_score
            FROM web_systems
            WHERE is_active IS TRUE
              AND (
                LOWER(sys_code) = LOWER(:keyword)
                OR LOWER(name) = LOWER(:keyword)
                OR sys_code ILIKE :pattern
                OR name ILIKE :pattern
              )
            ORDER BY match_score DESC, last_crawl_at DESC NULLS LAST, updated_at DESC NULLS LAST
            LIMIT 1
            """
        )
        params = {"keyword": system_keyword, "pattern": f"%{system_keyword}%"}
        row = (await session.execute(sql, params)).mappings().first()
        if row is None:
            return None

        return SystemRecord(
            id=row["id"],
            sys_code=row["sys_code"],
            name=row["name"],
            base_url=row["base_url"],
            framework_type=row["framework_type"] or "unknown",
            health_status=row["health_status"] or "unknown",
            last_crawl_at=row["last_crawl_at"],
            match_score=float(row["match_score"] or 0.0),
        )

    async def fetch_exact_candidates(
        self,
        session: AsyncSession,
        *,
        system_id: str,
        query: ContextQuery,
        min_stability_score: float,
        limit: int,
    ) -> list[PageCandidate]:
        conditions: list[str] = []
        params: dict = {
            "system_id": system_id,
            "min_stability_score": min_stability_score,
            "limit": limit,
            "page_keyword": query.page_keyword,
            "route_hint": query.route_hint,
        }

        if query.page_keyword:
            conditions.append(
                "(" \
                "LOWER(COALESCE(m.title, '')) = LOWER(:page_keyword) "
                "OR LOWER(COALESCE(m.text_breadcrumb, '')) = LOWER(:page_keyword) "
                "OR LOWER(COALESCE(p.page_title, '')) = LOWER(:page_keyword)"
                ")"
            )
        if query.route_hint:
            conditions.append(
                "(" \
                "LOWER(COALESCE(m.route_path, '')) = LOWER(:route_hint) "
                "OR LOWER(COALESCE(m.target_url, '')) = LOWER(:route_hint) "
                "OR LOWER(COALESCE(p.url_pattern, '')) = LOWER(:route_hint)"
                ")"
            )

        if not conditions:
            return []

        stage_filter = " OR ".join(conditions)
        sql = text(
            _BASE_CANDIDATE_SQL.format(
                stage_rank_sql="1.0",
                stage_filter=stage_filter,
            )
        )
        rows = (await session.execute(sql, params)).mappings().all()
        return [self._map_candidate(row, stage="exact") for row in rows]

    async def fetch_fuzzy_candidates(
        self,
        session: AsyncSession,
        *,
        system_id: str,
        query: ContextQuery,
        min_stability_score: float,
        limit: int,
    ) -> list[PageCandidate]:
        fuzzy_keyword = query.page_keyword or query.route_hint
        if fuzzy_keyword is None:
            return []

        params = {
            "system_id": system_id,
            "min_stability_score": min_stability_score,
            "limit": limit,
            "pattern": f"%{fuzzy_keyword}%",
        }
        stage_filter = "(" \
            "COALESCE(m.title, '') ILIKE :pattern " \
            "OR COALESCE(m.text_breadcrumb, '') ILIKE :pattern " \
            "OR COALESCE(p.page_title, '') ILIKE :pattern " \
            "OR COALESCE(m.route_path, '') ILIKE :pattern " \
            "OR COALESCE(m.target_url, '') ILIKE :pattern " \
            "OR COALESCE(p.url_pattern, '') ILIKE :pattern" \
            ")"

        sql = text(
            _BASE_CANDIDATE_SQL.format(
                stage_rank_sql="0.7",
                stage_filter=stage_filter,
            )
        )

        rows = (await session.execute(sql, params)).mappings().all()
        return [self._map_candidate(row, stage="fuzzy") for row in rows]

    async def fetch_semantic_candidates(
        self,
        session: AsyncSession,
        *,
        system_id: str,
        query: ContextQuery,
        min_stability_score: float,
        limit: int,
    ) -> list[PageCandidate]:
        semantic_query = _clean_search_terms(" ".join(filter(None, [query.page_keyword, query.route_hint])))
        if not semantic_query:
            return []

        params = {
            "system_id": system_id,
            "min_stability_score": min_stability_score,
            "limit": limit,
            "semantic_query": semantic_query,
        }

        stage_filter = """
            to_tsvector(
                'simple',
                CONCAT_WS(' ',
                    COALESCE(m.title, ''),
                    COALESCE(m.text_breadcrumb, ''),
                    COALESCE(m.route_path, ''),
                    COALESCE(m.target_url, ''),
                    COALESCE(p.page_title, ''),
                    COALESCE(p.page_summary, ''),
                    COALESCE(p.url_pattern, '')
                )
            ) @@ websearch_to_tsquery('simple', :semantic_query)
        """

        rank_sql = """
            COALESCE(
                ts_rank_cd(
                    to_tsvector(
                        'simple',
                        CONCAT_WS(' ',
                            COALESCE(m.title, ''),
                            COALESCE(m.text_breadcrumb, ''),
                            COALESCE(m.route_path, ''),
                            COALESCE(m.target_url, ''),
                            COALESCE(p.page_title, ''),
                            COALESCE(p.page_summary, ''),
                            COALESCE(p.url_pattern, '')
                        )
                    ),
                    websearch_to_tsquery('simple', :semantic_query)
                ),
                0
            )
        """

        sql = text(_BASE_CANDIDATE_SQL.format(stage_rank_sql=rank_sql, stage_filter=stage_filter))
        rows = (await session.execute(sql, params)).mappings().all()
        return [self._map_candidate(row, stage="semantic") for row in rows]

    async def fetch_locators(
        self,
        session: AsyncSession,
        *,
        page_id: str,
        min_stability_score: float,
        limit: int,
    ) -> list[LocatorRecord]:
        sql = text(
            """
            SELECT
                e.id,
                e.element_type,
                e.text_content,
                e.nearby_text,
                e.playwright_locator,
                COALESCE(e.stability_score, 0) AS stability_score,
                e.locator_tier,
                e.usage_description
            FROM ui_elements AS e
            WHERE e.page_id = :page_id
              AND e.element_type IN ('action_btn', 'form_input', 'nav_link')
              AND COALESCE(e.is_global_chrome, FALSE) IS FALSE
              AND COALESCE(e.is_business_useful, TRUE) IS TRUE
              AND COALESCE(e.stability_score, 0) >= :min_stability_score
              AND COALESCE(e.playwright_locator, '') <> ''
            ORDER BY COALESCE(e.stability_score, 0) DESC, e.updated_at DESC NULLS LAST
            LIMIT :limit
            """
        )

        params = {
            "page_id": page_id,
            "min_stability_score": min_stability_score,
            "limit": limit * 2,
        }

        rows = (await session.execute(sql, params)).mappings().all()
        locators: list[LocatorRecord] = []
        for row in rows:
            item = LocatorRecord(
                id=row["id"],
                element_type=row["element_type"],
                text_content=row["text_content"],
                nearby_text=row["nearby_text"],
                playwright_locator=row["playwright_locator"],
                stability_score=float(row["stability_score"] or 0.0),
                locator_tier=row["locator_tier"],
                usage_description=row["usage_description"],
            )
            if self._is_noise(item):
                continue
            locators.append(item)
            if len(locators) >= limit:
                break
        return locators

    async def fetch_navigation_chain(
        self,
        session: AsyncSession,
        *,
        system_id: str,
        menu_id: str,
    ) -> list[MenuNodeRecord]:
        """Fetch menu chain from root to target menu for deterministic navigation."""
        sql = text(
            """
            WITH RECURSIVE menu_chain AS (
                SELECT
                    m.id,
                    m.parent_id,
                    m.title,
                    m.text_breadcrumb,
                    m.node_type,
                    m.route_path,
                    m.target_url,
                    m.playwright_locator,
                    0::int AS depth_from_target
                FROM nav_menus AS m
                WHERE m.system_id = :system_id
                  AND m.id = CAST(:menu_id AS uuid)
                UNION ALL
                SELECT
                    p.id,
                    p.parent_id,
                    p.title,
                    p.text_breadcrumb,
                    p.node_type,
                    p.route_path,
                    p.target_url,
                    p.playwright_locator,
                    c.depth_from_target + 1
                FROM nav_menus AS p
                JOIN menu_chain AS c
                  ON c.parent_id = p.id
                WHERE p.system_id = :system_id
            )
            SELECT
                id,
                parent_id,
                title,
                text_breadcrumb,
                node_type,
                route_path,
                target_url,
                playwright_locator,
                depth_from_target
            FROM menu_chain
            ORDER BY depth_from_target DESC, title ASC
            """
        )
        rows = (await session.execute(sql, {"system_id": system_id, "menu_id": menu_id})).mappings().all()
        return [
            MenuNodeRecord(
                id=row["id"],
                parent_id=row["parent_id"],
                title=row["title"] or "",
                text_breadcrumb=row["text_breadcrumb"],
                node_type=row["node_type"],
                route_path=row["route_path"],
                target_url=row["target_url"],
                playwright_locator=row["playwright_locator"],
                depth_from_target=int(row["depth_from_target"] or 0),
            )
            for row in rows
        ]

    @staticmethod
    def _map_candidate(row: dict, stage: StageName) -> PageCandidate:
        return PageCandidate(
            menu_id=row["menu_id"],
            page_id=row["page_id"],
            title=row["title"] or "",
            text_breadcrumb=row["text_breadcrumb"],
            node_type=row["node_type"],
            target_url=row["target_url"],
            route_path=row["route_path"],
            url_pattern=row["url_pattern"],
            page_title=row["page_title"],
            page_summary=row["page_summary"],
            page_crawled_at=row["page_crawled_at"],
            last_verified_status=row["last_verified_status"],
            last_verified_at=row["last_verified_at"],
            avg_locator_stability=float(row["avg_locator_stability"] or 0.0),
            max_locator_stability=float(row["max_locator_stability"] or 0.0),
            stable_locator_count=int(row["stable_locator_count"] or 0),
            stage=stage,
            stage_rank=float(row["stage_rank"] or 0.0),
        )

    @staticmethod
    def _is_noise(locator: LocatorRecord) -> bool:
        locator_expr = (locator.playwright_locator or "").strip()
        if locator_expr and not locator_expr.startswith(_MODERN_PLAYWRIGHT_PREFIXES):
            if _DYNAMIC_ID_LOCATOR_RE.search(locator_expr):
                return True

        content = " ".join(
            filter(
                None,
                [
                    locator.text_content,
                    locator.nearby_text,
                    locator.playwright_locator,
                ],
            )
        ).lower()
        if not content:
            return False
        noise_tokens = ("header", "sidebar", "theme", "account", "avatar", "profile")
        return any(token in content for token in noise_tokens)

    async def fetch_valid_storage_state(
        self,
        session: AsyncSession,
        system_id: str,
    ) -> StorageStateRecord | None:
        """Fetch the current valid storage state for a system."""
        sql = text(
            """
            SELECT
                ss.id,
                ss.system_id,
                ss.storage_state,
                ss.cookies,
                ss.local_storage,
                ss.session_storage,
                ss.auth_mode,
                ss.is_valid,
                ss.validated_at,
                ss.expires_at
            FROM storage_states AS ss
            WHERE ss.system_id = :system_id
              AND ss.is_valid IS TRUE
            ORDER BY ss.validated_at DESC NULLS LAST, ss.created_at DESC
            LIMIT 1
            """
        )
        row = (await session.execute(sql, {"system_id": system_id})).mappings().first()
        if row is None:
            return None

        return StorageStateRecord(
            id=str(row["id"]),
            system_id=str(row["system_id"]),
            storage_state=row["storage_state"] or {},
            cookies=row["cookies"] or [],
            local_storage=row["local_storage"] or {},
            session_storage=row["session_storage"] or {},
            auth_mode=row["auth_mode"],
            is_valid=bool(row["is_valid"]),
            validated_at=row["validated_at"],
            expires_at=row["expires_at"],
        )


def dedupe_candidates(candidates: Sequence[PageCandidate]) -> list[PageCandidate]:
    """Merge duplicates and keep strongest stage result per menu_id."""

    stage_priority = {"exact": 3, "fuzzy": 2, "semantic": 1}
    deduped: dict[str, PageCandidate] = {}

    for item in candidates:
        key = str(item.menu_id)
        current = deduped.get(key)
        if current is None:
            deduped[key] = item
            continue

        if stage_priority[item.stage] > stage_priority[current.stage]:
            deduped[key] = item
            continue

        if item.stage == current.stage and item.stage_rank > current.stage_rank:
            deduped[key] = item

    return list(deduped.values())


def _clean_search_terms(raw: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff\s]+", " ", raw)
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact
