"""MCP server entrypoint exposing context-retrieval tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from menu_context_mcp.config import get_settings
from menu_context_mcp.observability import configure_logging
from menu_context_mcp.schemas import ContextQuery, StorageStateQuery
from menu_context_mcp.service import ContextRetrievalService


def create_mcp_server() -> FastMCP:
    settings = get_settings()
    configure_logging(settings.log_level)

    service = ContextRetrievalService(settings=settings)

    mcp = FastMCP(
        name=settings.mcp_server_name,
        instructions=(
            "Retrieve minimal and executable menu-map context for Playwright tasks. "
            "The service applies staged recall, scoring, stale checks and locator filtering."
        ),
    )

    @mcp.tool(
        name="get_page_playwright_context",
        description=(
            "Get AI-ready system/page/locator context for a target page. "
            "Returns Top-1 page + Top-2 fallback pages + 5-15 stable locators."
        ),
    )
    async def get_page_playwright_context(
        system_keyword: str,
        page_keyword: str | None = None,
        menu_keyword: str | None = None,
        route_hint: str | None = None,
        max_locators: int = 10,
        max_fallback_pages: int = 2,
        min_stability_score: float | None = None,
        freshness_hours: int | None = None,
        include_debug_trace: bool = False,
    ) -> dict[str, Any]:
        query = ContextQuery(
            system_keyword=system_keyword,
            page_keyword=page_keyword,
            menu_keyword=menu_keyword,
            route_hint=route_hint,
            max_locators=max_locators,
            max_fallback_pages=max_fallback_pages,
            min_stability_score=min_stability_score,
            freshness_hours=freshness_hours,
            include_debug_trace=include_debug_trace,
        )
        result = await service.get_page_playwright_context(query)
        return result.model_dump(mode="json", exclude_none=True)

    @mcp.tool(
        name="get_menu_interaction_context",
        description=(
            "Compatibility alias: menu-oriented query for interaction context retrieval. "
            "Equivalent to get_page_playwright_context."
        ),
    )
    async def get_menu_interaction_context(
        system_keyword: str,
        menu_keyword: str,
        route_hint: str | None = None,
        max_locators: int = 10,
        include_debug_trace: bool = False,
    ) -> dict[str, Any]:
        query = ContextQuery(
            system_keyword=system_keyword,
            menu_keyword=menu_keyword,
            route_hint=route_hint,
            max_locators=max_locators,
            include_debug_trace=include_debug_trace,
        )
        result = await service.get_page_playwright_context(query)
        return result.model_dump(mode="json", exclude_none=True)

    @mcp.tool(
        name="get_storage_state_for_session",
        description=(
            "Get browser storage state (cookies, localStorage, sessionStorage) for Playwright session reuse. "
            "Use this to skip login in generated Playwright scripts by injecting the returned storage_state "
            "into browser.new_context(storage_state=...). Supports fuzzy matching on system name."
        ),
    )
    async def get_storage_state_for_session(
        system_name: str,
    ) -> dict[str, Any]:
        query = StorageStateQuery(system_name=system_name)
        result = await service.get_storage_state_for_session(query)
        return result.model_dump(mode="json", exclude_none=True)

    return mcp


def main() -> None:
    settings = get_settings()
    server = create_mcp_server()
    if settings.mcp_transport == "stdio":
        server.run(transport="stdio", log_level=settings.log_level)
        return

    server.run(
        transport=settings.mcp_transport,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
