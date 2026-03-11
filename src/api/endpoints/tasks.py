"""Task log query endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.api.deps import get_db
from src.models.web_system import WebSystem
from src.schemas.task_log import TaskLogItem, TaskLogListResponse
from src.services.task_tracker import TaskTracker

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{sys_code}/logs", response_model=TaskLogListResponse)
async def get_task_logs(
    sys_code: str,
    task_type: Literal["auth", "crawl_menu"] | None = Query(default=None),
    status: Literal["running", "success", "failed", "skipped"] | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> TaskLogListResponse:
    system = (
        await db.exec(select(WebSystem).where(WebSystem.sys_code == sys_code))
    ).first()
    if system is None:
        raise HTTPException(status_code=404, detail=f"System not found: {sys_code}")

    tracker = TaskTracker(db)
    logs = await tracker.list_logs(
        system_id=system.id,
        task_type=task_type,
        status=status,
        limit=limit,
    )

    return TaskLogListResponse(
        sys_code=sys_code,
        total=len(logs),
        items=[
            TaskLogItem(
                task_type=row.task_type,
                task_id=row.task_id,
                status=row.status,
                retry_count=row.retry_count,
                target_url=row.target_url,
                error_message=row.error_message,
                changed=row.changed,
                pages_found=row.pages_found,
                elements_found=row.elements_found,
                duration_ms=row.duration_ms,
                started_at=row.started_at,
                finished_at=row.finished_at,
            )
            for row in logs
        ],
    )
