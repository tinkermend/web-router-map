"""Crawl-related API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from src.api.deps import get_db
from src.schemas.crawl import CrawlRunRequest, CrawlRunResponse
from src.services.crawl_service import CrawlService

router = APIRouter(prefix="/crawl", tags=["crawl"])


@router.post("/run/{sys_code}", response_model=CrawlRunResponse)
async def run_menu_crawl(
    sys_code: str,
    req: CrawlRunRequest,
    db: AsyncSession = Depends(get_db),
) -> CrawlRunResponse:
    service = CrawlService(db)
    result = await service.run_by_sys_code(
        sys_code,
        headed=req.headed,
        timeout_ms=req.timeout_ms,
        max_pages=req.max_pages,
        max_elements_per_page=req.max_elements_per_page,
        max_modal_triggers=req.max_modal_triggers,
        expand_rounds=req.expand_rounds,
        menu_selector=req.menu_selector,
        home_url=req.home_url,
    )
    return CrawlRunResponse(**result.__dict__)
