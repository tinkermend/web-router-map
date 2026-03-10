"""Auth-related API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from src.api.deps import get_db
from src.schemas.auth import (
    AuthRefreshRequest,
    AuthRefreshResponse,
    LatestStateResponse,
    ManualStatePayload,
)
from src.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/refresh/{sys_code}", response_model=AuthRefreshResponse)
async def refresh_auth_state(
    sys_code: str,
    req: AuthRefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthRefreshResponse:
    service = AuthService(db)
    result = await service.refresh_by_sys_code(sys_code, headed=req.headed, timeout_ms=req.timeout_ms)
    return AuthRefreshResponse(**result.__dict__)


@router.get("/state/{sys_code}", response_model=LatestStateResponse)
async def get_latest_state(
    sys_code: str,
    db: AsyncSession = Depends(get_db),
) -> LatestStateResponse:
    service = AuthService(db)
    system, state = await service.get_latest_state(sys_code)
    if system is None:
        raise HTTPException(status_code=404, detail=f"System not found: {sys_code}")

    if state is None:
        return LatestStateResponse(
            sys_code=system.sys_code,
            state_id=None,
            is_valid=None,
            auth_mode=None,
            playback_strategy=None,
            validated_at=None,
            last_auth_at=system.last_auth_at,
            request_headers={},
            cookies_count=0,
        )

    return LatestStateResponse(
        sys_code=system.sys_code,
        state_id=state.id,
        is_valid=state.is_valid,
        auth_mode=state.auth_mode,
        playback_strategy=state.playback_strategy,
        validated_at=state.validated_at,
        last_auth_at=system.last_auth_at,
        request_headers=state.request_headers or {},
        cookies_count=len(state.cookies or []),
    )


@router.post("/manual-state/{sys_code}")
async def inject_manual_state(
    sys_code: str,
    payload: ManualStatePayload,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    service = AuthService(db)
    state_id = await service.inject_manual_state(sys_code, payload.model_dump())
    if state_id is None:
        raise HTTPException(status_code=404, detail=f"System not found: {sys_code}")
    return {"sys_code": sys_code, "state_id": str(state_id)}
