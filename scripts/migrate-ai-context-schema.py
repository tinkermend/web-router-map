#!/usr/bin/env python3
"""Upgrade DB schema for AI-friendly flattened crawl context fields."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.database import close_db, get_session_factory, init_db


DDL_STATEMENTS = [
    """
    ALTER TABLE nav_menus
        ADD COLUMN IF NOT EXISTS source VARCHAR(50),
        ADD COLUMN IF NOT EXISTS is_ai_primary_candidate BOOLEAN,
        ADD COLUMN IF NOT EXISTS ai_candidate_rank INT
    """,
    """
    ALTER TABLE app_pages
        ADD COLUMN IF NOT EXISTS actionable_element_count INT,
        ADD COLUMN IF NOT EXISTS elements_raw_count INT,
        ADD COLUMN IF NOT EXISTS elements_filtered_out_count INT
    """,
    """
    ALTER TABLE ui_elements
        ADD COLUMN IF NOT EXISTS dom_css_path TEXT,
        ADD COLUMN IF NOT EXISTS locator_tier VARCHAR(32),
        ADD COLUMN IF NOT EXISTS stability_score DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS is_global_chrome BOOLEAN,
        ADD COLUMN IF NOT EXISTS is_business_useful BOOLEAN
    """,
    "CREATE INDEX IF NOT EXISTS idx_nav_menus_ai_rank ON nav_menus (system_id, ai_candidate_rank) WHERE is_ai_primary_candidate IS TRUE",
    "CREATE INDEX IF NOT EXISTS idx_app_pages_element_counts ON app_pages (system_id, actionable_element_count)",
    "CREATE INDEX IF NOT EXISTS idx_ui_elements_stability ON ui_elements (page_id, stability_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ui_elements_locator_tier ON ui_elements (locator_tier)",
]


BACKFILL_STATEMENTS = [
    """
    UPDATE nav_menus
    SET source = COALESCE(source, 'dom')
    WHERE source IS NULL
    """,
    """
    UPDATE nav_menus
    SET is_ai_primary_candidate = COALESCE(is_ai_primary_candidate, node_type = 'page' AND target_url IS NOT NULL)
    WHERE is_ai_primary_candidate IS NULL
    """,
    """
    WITH ranked AS (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY system_id
                ORDER BY COALESCE(menu_level, 9999), COALESCE(menu_order, 9999), COALESCE(text_breadcrumb, title, '')
            ) AS rn
        FROM nav_menus
        WHERE is_ai_primary_candidate IS TRUE
    )
    UPDATE nav_menus AS m
    SET ai_candidate_rank = r.rn
    FROM ranked AS r
    WHERE m.id = r.id
      AND m.ai_candidate_rank IS NULL
    """,
    """
    UPDATE nav_menus
    SET node_path = CAST(
        CASE
            WHEN path_indexes IS NOT NULL
                 AND jsonb_typeof(path_indexes) = 'array'
                 AND jsonb_array_length(path_indexes) > 0
            THEN
                'root.' || (
                    SELECT string_agg('n_' || COALESCE(NULLIF(value, ''), '0'), '.' ORDER BY ord)
                    FROM jsonb_array_elements_text(path_indexes) WITH ORDINALITY AS arr(value, ord)
                )
            ELSE 'root.n_0'
        END
        AS ltree
    )
    WHERE node_path IS NULL
    """,
    """
    UPDATE app_pages
    SET
        elements_raw_count = COALESCE(
            elements_raw_count,
            CASE
                WHEN COALESCE(meta_info ->> 'elements_raw_count', '') ~ '^-?\\d+$'
                THEN (meta_info ->> 'elements_raw_count')::INT
                ELSE NULL
            END
        ),
        elements_filtered_out_count = COALESCE(
            elements_filtered_out_count,
            CASE
                WHEN COALESCE(meta_info ->> 'elements_filtered_out_count', '') ~ '^-?\\d+$'
                THEN (meta_info ->> 'elements_filtered_out_count')::INT
                ELSE NULL
            END
        )
    """,
    """
    UPDATE app_pages
    SET actionable_element_count = COALESCE(
        actionable_element_count,
        GREATEST(COALESCE(elements_raw_count, 0) - COALESCE(elements_filtered_out_count, 0), 0)
    )
    WHERE actionable_element_count IS NULL
    """,
    """
    UPDATE ui_elements
    SET
        dom_css_path = COALESCE(dom_css_path, locators ->> 'dom_css_path'),
        locator_tier = COALESCE(locator_tier, NULLIF(locators #>> '{quality,locator_tier}', '')),
        stability_score = COALESCE(
            stability_score,
            CASE
                WHEN COALESCE(locators #>> '{quality,stability_score}', '') ~ '^-?\\d+(\\.\\d+)?$'
                THEN (locators #>> '{quality,stability_score}')::DOUBLE PRECISION
                ELSE NULL
            END
        ),
        is_global_chrome = COALESCE(
            is_global_chrome,
            CASE
                WHEN LOWER(COALESCE(locators #>> '{quality,is_global_chrome}', '')) IN ('true', 'false')
                THEN (locators #>> '{quality,is_global_chrome}')::BOOLEAN
                ELSE NULL
            END
        ),
        is_business_useful = COALESCE(is_business_useful, TRUE)
    """
]


async def _run() -> None:
    await init_db()
    session_factory = get_session_factory()
    async with session_factory() as session:
        for ddl in DDL_STATEMENTS:
            await session.exec(text(ddl))
        for update_sql in BACKFILL_STATEMENTS:
            await session.exec(text(update_sql))
        await session.commit()


async def _run_with_cleanup() -> None:
    try:
        await _run()
    finally:
        await close_db()


def main() -> None:
    asyncio.run(_run_with_cleanup())


if __name__ == "__main__":
    main()
