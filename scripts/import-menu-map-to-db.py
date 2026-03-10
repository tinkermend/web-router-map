#!/usr/bin/env python3
"""
Import crawled menu-map JSON into PostgreSQL tables.

Target tables:
- web_systems
- nav_menus
- app_pages
- ui_containers
- ui_elements
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import get_settings
from src.models.database import session_scope

DEFAULT_INPUT = "output/playwright/ele-menu-map-optimized.json"


@dataclass
class ImportStats:
    menu_count: int = 0
    page_count: int = 0
    container_count: int = 0
    element_count: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import menu-map JSON to DB.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to menu-map JSON file.")
    parser.add_argument(
        "--schema",
        default="",
        help="Target DB schema. Empty means DATABASE_SCHEMA from settings.",
    )
    parser.add_argument(
        "--system-code",
        default="",
        help="Override system code. Empty means use payload meta.system_code.",
    )
    parser.add_argument(
        "--system-name",
        default="",
        help="Override system name. Empty means system_code.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete existing nav/page/element data for this system before import.",
    )
    parser.add_argument(
        "--ensure-tables",
        action="store_true",
        help="Ensure required tables exist before import.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print planned counts without writing DB.",
    )
    return parser.parse_args()


def _validate_schema(schema: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError(f"Invalid schema identifier: {schema!r}")
    return schema


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to read payload JSON: {path}") from exc


def _extract_system_meta(payload: dict[str, Any], override_code: str, override_name: str) -> tuple[str, str, str]:
    meta = payload.get("meta") or {}
    system_code = override_code or meta.get("system_code")
    if not system_code:
        raise RuntimeError("system_code not found in payload meta and not provided by --system-code")
    system_name = override_name or system_code
    base_url = str(meta.get("base_url") or "")
    if not base_url:
        raise RuntimeError("base_url missing in payload meta")
    return str(system_code), str(system_name), base_url


async def ensure_tables(schema: str) -> None:
    ddl = [
        'CREATE EXTENSION IF NOT EXISTS "pgcrypto"',
        'CREATE EXTENSION IF NOT EXISTS "ltree"',
        f'CREATE SCHEMA IF NOT EXISTS "{schema}"',
        f"""
        CREATE TABLE IF NOT EXISTS "{schema}".nav_menus (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            system_id UUID NOT NULL REFERENCES "{schema}".web_systems(id) ON DELETE CASCADE,
            parent_id UUID REFERENCES "{schema}".nav_menus(id) ON DELETE CASCADE,
            node_path LTREE,
            title VARCHAR(255) NOT NULL,
            text_breadcrumb TEXT,
            icon VARCHAR(100),
            menu_order INT DEFAULT 0,
            menu_level INT DEFAULT 1,
            path_indexes JSONB,
            node_type VARCHAR(20) DEFAULT 'page',
            target_url VARCHAR(500),
            route_path VARCHAR(500),
            route_name VARCHAR(100),
            playwright_locator TEXT,
            last_verified_status VARCHAR(20),
            last_verified_at TIMESTAMPTZ,
            is_group BOOLEAN DEFAULT FALSE,
            is_external BOOLEAN DEFAULT FALSE,
            is_visible BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS "{schema}".app_pages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            system_id UUID NOT NULL REFERENCES "{schema}".web_systems(id) ON DELETE CASCADE,
            menu_id UUID REFERENCES "{schema}".nav_menus(id) ON DELETE SET NULL,
            url_pattern VARCHAR(500) NOT NULL,
            route_name VARCHAR(100),
            page_title VARCHAR(255),
            page_summary TEXT,
            description TEXT,
            keywords TEXT[],
            meta_info JSONB,
            component_path VARCHAR(500),
            screenshot_path VARCHAR(500),
            is_crawled BOOLEAN DEFAULT FALSE,
            crawled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS "{schema}".ui_containers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            page_id UUID NOT NULL REFERENCES "{schema}".app_pages(id) ON DELETE CASCADE,
            container_type VARCHAR(50) NOT NULL DEFAULT 'page_body',
            title VARCHAR(255),
            xpath_root VARCHAR(500),
            css_selector VARCHAR(500),
            trigger_element_id UUID,
            trigger_action VARCHAR(50) DEFAULT 'click',
            is_dynamic BOOLEAN DEFAULT FALSE,
            is_visible_default BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS "{schema}".ui_elements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            page_id UUID NOT NULL REFERENCES "{schema}".app_pages(id) ON DELETE CASCADE,
            container_id UUID REFERENCES "{schema}".ui_containers(id) ON DELETE SET NULL,
            tag_name VARCHAR(50) NOT NULL,
            element_type VARCHAR(50),
            text_content TEXT,
            locators JSONB NOT NULL,
            playwright_locator TEXT,
            nearby_text TEXT,
            usage_description TEXT,
            screenshot_slice_path VARCHAR(500),
            bounding_box JSONB,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f'CREATE INDEX IF NOT EXISTS idx_menu_parent ON "{schema}".nav_menus(system_id, parent_id)',
        f'CREATE INDEX IF NOT EXISTS idx_page_system ON "{schema}".app_pages(system_id)',
        f'CREATE INDEX IF NOT EXISTS idx_element_page ON "{schema}".ui_elements(page_id)',
    ]
    async with session_scope() as session:
        for stmt in ddl:
            await session.exec(text(stmt))


async def upsert_system(schema: str, system_code: str, system_name: str, base_url: str, framework_type: str) -> str:
    sql = text(
        f"""
        INSERT INTO "{schema}".web_systems (
            id, sys_code, name, base_url, framework_type, health_status, last_crawl_at
        )
        VALUES (:id, :sys_code, :name, :base_url, :framework_type, 'online', NOW())
        ON CONFLICT (sys_code)
        DO UPDATE SET
            name = EXCLUDED.name,
            base_url = EXCLUDED.base_url,
            framework_type = EXCLUDED.framework_type,
            health_status = 'online',
            last_crawl_at = NOW(),
            updated_at = NOW()
        RETURNING id
        """
    )
    async with session_scope() as session:
        result = await session.exec(
            sql,
            {
                "id": str(uuid.uuid4()),
                "sys_code": system_code,
                "name": system_name,
                "base_url": base_url,
                "framework_type": framework_type or "unknown",
            },
        )
        system_id = result.scalar_one()
    return str(system_id)


async def replace_existing_data(schema: str, system_id: str) -> None:
    stmts = [
        text(f'DELETE FROM "{schema}".app_pages WHERE system_id = :system_id'),
        text(f'DELETE FROM "{schema}".nav_menus WHERE system_id = :system_id'),
    ]
    async with session_scope() as session:
        for stmt in stmts:
            await session.exec(stmt, {"system_id": system_id})


async def import_payload(schema: str, system_id: str, payload: dict[str, Any]) -> ImportStats:
    stats = ImportStats()
    menus = payload.get("menus") or []
    pages = payload.get("pages") or []

    # Insert menus with in-memory key mapping for parent linkage.
    menu_uuid_by_node_key: dict[str, str] = {}
    route_to_menu_uuid: dict[str, str] = {}
    ordered_menus = sorted(menus, key=lambda m: (int(m.get("menu_level") or 0), int(m.get("menu_order") or 0)))

    async with session_scope() as session:
        for idx, menu in enumerate(ordered_menus, start=1):
            menu_id = str(uuid.uuid4())
            node_key = str(menu.get("node_id") or f"node_{idx}")
            parent_key = menu.get("parent_id")
            parent_id = menu_uuid_by_node_key.get(str(parent_key)) if parent_key else None
            node_path = menu.get("node_path")
            if not node_path:
                node_path = "root.node"

            await session.exec(
                text(
                    f"""
                    INSERT INTO "{schema}".nav_menus (
                        id, system_id, parent_id, node_path, title, text_breadcrumb, icon,
                        menu_order, menu_level, path_indexes, node_type, target_url, route_path,
                        route_name, playwright_locator, is_group, is_external, is_visible
                    )
                    VALUES (
                        :id, :system_id, :parent_id, CAST(:node_path AS ltree), :title, :text_breadcrumb, :icon,
                        :menu_order, :menu_level, CAST(:path_indexes AS jsonb), :node_type, :target_url, :route_path,
                        :route_name, :playwright_locator, :is_group, :is_external, :is_visible
                    )
                    """
                ),
                {
                    "id": menu_id,
                    "system_id": system_id,
                    "parent_id": parent_id,
                    "node_path": str(node_path),
                    "title": str(menu.get("title") or "未命名"),
                    "text_breadcrumb": menu.get("text_breadcrumb"),
                    "icon": menu.get("icon"),
                    "menu_order": int(menu.get("menu_order") or 0),
                    "menu_level": int(menu.get("menu_level") or 1),
                    "path_indexes": json.dumps(menu.get("path_indexes") or []),
                    "node_type": str(menu.get("node_type") or "page"),
                    "target_url": menu.get("target_url"),
                    "route_path": menu.get("route_path"),
                    "route_name": menu.get("route_name"),
                    "playwright_locator": menu.get("playwright_locator"),
                    "is_group": bool(menu.get("is_group", False)),
                    "is_external": bool(menu.get("is_external", False)),
                    "is_visible": bool(menu.get("is_visible", True)),
                },
            )
            menu_uuid_by_node_key[node_key] = menu_id
            route_path = menu.get("route_path")
            if route_path and menu.get("node_type") == "page":
                route_to_menu_uuid[str(route_path)] = menu_id
            stats.menu_count += 1

    async with session_scope() as session:
        for page in pages:
            page_id = str(uuid.uuid4())
            url_pattern = str(page.get("url_pattern") or page.get("target_url") or "").strip()
            if not url_pattern:
                continue
            route_path = url_pattern if url_pattern.startswith("/") else None
            menu_id = route_to_menu_uuid.get(route_path or "")

            meta_info = {
                "target_url": page.get("target_url"),
                "errors": page.get("errors") or [],
                "elements_raw_count": page.get("elements_raw_count", 0),
                "elements_filtered_out_count": page.get("elements_filtered_out_count", 0),
            }

            await session.exec(
                text(
                    f"""
                    INSERT INTO "{schema}".app_pages (
                        id, system_id, menu_id, url_pattern, route_name, page_title, page_summary,
                        description, meta_info, screenshot_path, is_crawled, crawled_at
                    )
                    VALUES (
                        :id, :system_id, :menu_id, :url_pattern, :route_name, :page_title, :page_summary,
                        :description, CAST(:meta_info AS jsonb), :screenshot_path, :is_crawled, :crawled_at
                    )
                    """
                ),
                {
                    "id": page_id,
                    "system_id": system_id,
                    "menu_id": menu_id,
                    "url_pattern": url_pattern,
                    "route_name": route_path.strip("/").replace("/", "_") if route_path else None,
                    "page_title": page.get("page_title"),
                    "page_summary": None,
                    "description": None,
                    "meta_info": json.dumps(meta_info, ensure_ascii=False),
                    "screenshot_path": page.get("screenshot_path"),
                    "is_crawled": bool(page.get("is_crawled", False)),
                    "crawled_at": page.get("crawled_at"),
                },
            )
            stats.page_count += 1

            container_uuid_by_local_id: dict[str, str] = {}
            containers = (page.get("containers") or []) + (page.get("modal_containers") or [])
            for container in containers:
                container_id = str(uuid.uuid4())
                local_id = str(container.get("container_id") or f"container_{len(container_uuid_by_local_id)+1}")
                await session.exec(
                    text(
                        f"""
                        INSERT INTO "{schema}".ui_containers (
                            id, page_id, container_type, title, xpath_root, css_selector,
                            trigger_element_id, trigger_action, is_dynamic, is_visible_default
                        )
                        VALUES (
                            :id, :page_id, :container_type, :title, NULL, :css_selector,
                            NULL, :trigger_action, :is_dynamic, :is_visible_default
                        )
                        """
                    ),
                    {
                        "id": container_id,
                        "page_id": page_id,
                        "container_type": container.get("container_type") or "page_body",
                        "title": container.get("title"),
                        "css_selector": container.get("css_selector"),
                        "trigger_action": container.get("trigger_action") or "click",
                        "is_dynamic": bool(container.get("is_dynamic", False)),
                        "is_visible_default": bool(container.get("is_visible_default", True)),
                    },
                )
                container_uuid_by_local_id[local_id] = container_id
                stats.container_count += 1

            for element in page.get("elements") or []:
                element_id = str(uuid.uuid4())
                local_container_id = str(element.get("container_id") or "page_body")
                container_id = container_uuid_by_local_id.get(local_container_id)

                locators = dict(element.get("locators") or {})
                locators_meta = {
                    "locator_tier": element.get("locator_tier"),
                    "stability_score": element.get("stability_score"),
                    "is_global_chrome": element.get("is_global_chrome"),
                    "dom_css_path": element.get("dom_css_path"),
                }
                locators["meta"] = locators_meta

                await session.exec(
                    text(
                        f"""
                        INSERT INTO "{schema}".ui_elements (
                            id, page_id, container_id, tag_name, element_type, text_content,
                            locators, playwright_locator, nearby_text, usage_description, screenshot_slice_path, bounding_box
                        )
                        VALUES (
                            :id, :page_id, :container_id, :tag_name, :element_type, :text_content,
                            CAST(:locators AS jsonb), :playwright_locator, :nearby_text, :usage_description, NULL, CAST(:bounding_box AS jsonb)
                        )
                        """
                    ),
                    {
                        "id": element_id,
                        "page_id": page_id,
                        "container_id": container_id,
                        "tag_name": element.get("tag_name") or "unknown",
                        "element_type": element.get("element_type"),
                        "text_content": element.get("text_content"),
                        "locators": json.dumps(locators, ensure_ascii=False),
                        "playwright_locator": element.get("playwright_locator"),
                        "nearby_text": element.get("nearby_text"),
                        "usage_description": element.get("usage_description"),
                        "bounding_box": json.dumps(element.get("bounding_box") or {}, ensure_ascii=False),
                    },
                )
                stats.element_count += 1
    return stats


async def run() -> None:
    args = parse_args()
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise RuntimeError(f"Input file not found: {input_path}")

    settings = get_settings()
    schema = _validate_schema(args.schema or settings.database_schema)
    payload = _load_payload(input_path)
    system_code, system_name, base_url = _extract_system_meta(
        payload, args.system_code, args.system_name
    )

    if args.dry_run:
        menu_count = len(payload.get("menus") or [])
        page_count = len(payload.get("pages") or [])
        element_count = sum(len((p.get("elements") or [])) for p in payload.get("pages") or [])
        print("Dry-run only. No DB writes.")
        print(f"input={input_path}")
        print(f"schema={schema} system_code={system_code} system_name={system_name}")
        print(f"menus={menu_count} pages={page_count} elements={element_count}")
        return

    if args.ensure_tables:
        await ensure_tables(schema)

    framework = str(
        ((payload.get("meta") or {}).get("framework_detection") or {}).get("framework_type") or "unknown"
    )
    system_id = await upsert_system(schema, system_code, system_name, base_url, framework)

    if args.replace_existing:
        await replace_existing_data(schema, system_id)

    stats = await import_payload(schema, system_id, payload)
    print("Import completed.")
    print(f"schema={schema} system_id={system_id}")
    print(
        f"inserted menus={stats.menu_count}, pages={stats.page_count}, "
        f"containers={stats.container_count}, elements={stats.element_count}"
    )


if __name__ == "__main__":
    asyncio.run(run())
