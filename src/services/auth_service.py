"""Authentication refresh service for StorageState/cookie lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.config.settings import get_settings
from src.crawler.auth_crawler import AuthCapture, AuthCrawler, TOKEN_KEYS
from src.infrastructure.logging import get_logger
from src.models.storage_state import StorageState
from src.models.web_system import WebSystem
from src.scheduler.locks import distributed_lock
from src.services.crypto_service import CryptoService
from src.services.task_tracker import TaskTracker
from src.services.validator_service import ValidationResult, validate_capture

AUTH_COOKIE_KEYS = {"session", "sessionid", "jsessionid", "token", "auth", "access_token"}
service_logger = get_logger(__name__)


@dataclass(slots=True)
class AuthAnalysis:
    """Derived auth strategy from captured artifacts."""

    auth_mode: str
    playback_strategy: str
    authorization_source: str | None
    authorization_schema: str | None
    authorization_value: str | None
    auth_fingerprint: str | None


@dataclass(slots=True)
class AuthRefreshResult:
    """Service-level refresh result."""

    sys_code: str
    status: str
    message: str
    state_id: UUID | None
    cookies_count: int
    local_storage_count: int
    session_storage_count: int
    authorization_captured: bool
    validated: bool | None
    validate_status_code: int | None
    started_at: datetime
    finished_at: datetime


class AuthService:
    """Manage login, capture and state persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        settings = get_settings()
        self.settings = settings
        self.crawler = AuthCrawler()
        self.crypto = CryptoService(settings.encryption_key)
        self.task_tracker = TaskTracker(session)

    async def refresh_by_sys_code(
        self,
        sys_code: str,
        *,
        headed: bool = False,
        timeout_ms: int | None = None,
    ) -> AuthRefreshResult:
        started_at = _utc_now()
        task_log_id: UUID | None = None
        auth_logger = service_logger.bind(sys_code=sys_code, task_type="auth", headed=headed)
        auth_logger.info("Auth refresh started")
        system = await self._get_active_system(sys_code)
        if system is None:
            finished_at = _utc_now()
            auth_logger.warning("Auth refresh aborted: system not found or inactive")
            return AuthRefreshResult(
                sys_code=sys_code,
                status="failed",
                message=f"System not found or inactive: {sys_code}",
                state_id=None,
                cookies_count=0,
                local_storage_count=0,
                session_storage_count=0,
                authorization_captured=False,
                validated=None,
                validate_status_code=None,
                started_at=started_at,
                finished_at=finished_at,
            )

        task_log = await self.task_tracker.start(
            system_id=system.id,
            task_type="auth",
            target_url=system.login_url or system.base_url,
        )
        task_log_id = task_log.id
        auth_logger = auth_logger.bind(log_id=str(task_log_id))
        auth_logger.info("Auth task log created")

        username = self._resolve_secret(system.login_username)
        password = self._resolve_secret(system.login_password)
        if not username or not password:
            finished_at = _utc_now()
            auth_logger.error("Auth refresh failed: missing credentials")
            await self.task_tracker.finish(
                log_id=task_log_id,
                status="failed",
                error_message="Missing login credentials in web_systems.",
                retry_count=0,
            )
            return AuthRefreshResult(
                sys_code=sys_code,
                status="failed",
                message="Missing login credentials in web_systems.",
                state_id=None,
                cookies_count=0,
                local_storage_count=0,
                session_storage_count=0,
                authorization_captured=False,
                validated=None,
                validate_status_code=None,
                started_at=started_at,
                finished_at=finished_at,
            )

        timeout_ms = timeout_ms or self.settings.playwright_timeout
        lock_name = f"auth:{system.sys_code}"

        async with distributed_lock.acquire(lock_name) as acquired:
            if not acquired:
                finished_at = _utc_now()
                auth_logger.warning("Auth refresh skipped: lock is already held")
                await self.task_tracker.finish(
                    log_id=task_log_id,
                    status="skipped",
                    error_message="Auth refresh lock is already held by another task.",
                    retry_count=0,
                )
                return AuthRefreshResult(
                    sys_code=sys_code,
                    status="skipped",
                    message="Auth refresh lock is already held by another task.",
                    state_id=None,
                    cookies_count=0,
                    local_storage_count=0,
                    session_storage_count=0,
                    authorization_captured=False,
                    validated=None,
                    validate_status_code=None,
                    started_at=started_at,
                    finished_at=finished_at,
                )

            last_error: str | None = None
            for attempt in range(1, self.settings.auth_max_retries + 1):
                try:
                    auth_logger.bind(
                        attempt=attempt,
                        max_retries=self.settings.auth_max_retries,
                    ).info("Auth refresh attempt started")
                    capture = await self.crawler.login_and_capture(
                        login_url=system.login_url or system.base_url,
                        username=username,
                        password=password,
                        login_auth=system.login_auth,
                        login_selectors=system.login_selectors or {},
                        timeout_ms=timeout_ms,
                        headed=headed,
                        slow_mo=self.settings.playwright_slow_mo,
                    )
                    analysis = analyze_auth_payload(
                        request_headers=capture.request_headers,
                        local_storage=capture.local_storage,
                        session_storage=capture.session_storage,
                        cookies=capture.cookies,
                        default_playback_strategy=system.playback_strategy_default,
                    )
                    validation = await validate_capture(system, capture)
                    if validation.is_valid is False:
                        raise RuntimeError(
                            f"validate endpoint rejected auth state: {validation.status_code or validation.error}"
                        )

                    state_id = await self._save_state(system, capture, analysis, validation)
                    finished_at = _utc_now()
                    await self.task_tracker.finish(
                        log_id=task_log_id,
                        status="success",
                        retry_count=max(0, attempt - 1),
                    )
                    auth_logger.bind(
                        attempt=attempt,
                        state_id=str(state_id),
                        cookies_count=len(capture.cookies),
                        local_storage_count=len(capture.local_storage),
                        session_storage_count=len(capture.session_storage),
                    ).info("Auth refresh succeeded")
                    return AuthRefreshResult(
                        sys_code=sys_code,
                        status="success",
                        message="StorageState refreshed successfully.",
                        state_id=state_id,
                        cookies_count=len(capture.cookies),
                        local_storage_count=len(capture.local_storage),
                        session_storage_count=len(capture.session_storage),
                        authorization_captured=bool(analysis.authorization_value),
                        validated=validation.is_valid,
                        validate_status_code=validation.status_code,
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                except Exception as exc:  # pragma: no cover - runtime flow
                    last_error = str(exc)
                    auth_logger.bind(
                        attempt=attempt,
                        max_retries=self.settings.auth_max_retries,
                    ).exception("Auth refresh attempt failed")
                    await self._mark_auth_failure(system.id, last_error)
                    if attempt < self.settings.auth_max_retries:
                        await asyncio.sleep(self.settings.auth_retry_delay_seconds)

            finished_at = _utc_now()
            auth_logger.bind(last_error=last_error or "").error("Auth refresh exhausted all retries")
            await self.task_tracker.finish(
                log_id=task_log_id,
                status="failed",
                error_message=f"Auth refresh failed after retries: {last_error}",
                retry_count=max(0, self.settings.auth_max_retries - 1),
            )
            return AuthRefreshResult(
                sys_code=sys_code,
                status="failed",
                message=f"Auth refresh failed after retries: {last_error}",
                state_id=None,
                cookies_count=0,
                local_storage_count=0,
                session_storage_count=0,
                authorization_captured=False,
                validated=False,
                validate_status_code=None,
                started_at=started_at,
                finished_at=finished_at,
            )

    async def inject_manual_state(
        self,
        sys_code: str,
        payload: dict[str, Any],
    ) -> UUID | None:
        """Persist manually provided auth payload as latest valid state."""

        manual_logger = service_logger.bind(sys_code=sys_code, task_type="auth")
        system = await self._get_active_system(sys_code)
        if system is None:
            manual_logger.warning("Manual state injection skipped: system not found or inactive")
            return None

        capture = AuthCapture(
            base_url=system.login_url or system.base_url,
            current_url=system.base_url,
            storage_state=payload.get("storage_state") or {},
            cookies=payload.get("cookies") or [],
            local_storage=payload.get("local_storage") or {},
            session_storage=payload.get("session_storage") or {},
            request_headers=payload.get("request_headers") or {},
            authorization=payload.get("authorization_value"),
        )
        analysis = analyze_auth_payload(
            request_headers=capture.request_headers,
            local_storage=capture.local_storage,
            session_storage=capture.session_storage,
            cookies=capture.cookies,
            default_playback_strategy=str(payload.get("playback_strategy") or system.playback_strategy_default),
        )
        if payload.get("authorization_value"):
            analysis = AuthAnalysis(
                auth_mode=analysis.auth_mode,
                playback_strategy=analysis.playback_strategy,
                authorization_source="manual",
                authorization_schema=payload.get("authorization_schema") or analysis.authorization_schema,
                authorization_value=str(payload["authorization_value"]),
                auth_fingerprint=_fingerprint(str(payload["authorization_value"])),
            )
        state_id = await self._save_state(
            system,
            capture,
            analysis,
            ValidationResult(is_valid=None, status_code=None, response_ms=None, error=None),
        )
        manual_logger.bind(state_id=str(state_id)).info("Manual state injection succeeded")
        return state_id

    async def get_latest_state(self, sys_code: str) -> tuple[WebSystem | None, StorageState | None]:
        system = await self._get_active_system(sys_code)
        if system is None:
            return None, None

        if not system.latest_valid_state_id:
            return system, None

        state = await self.session.get(StorageState, system.latest_valid_state_id)
        return system, state

    async def _get_active_system(self, sys_code: str) -> WebSystem | None:
        stmt = select(WebSystem).where(WebSystem.sys_code == sys_code, WebSystem.is_active.is_(True))
        result = await self.session.exec(stmt)
        return result.first()

    def _resolve_secret(self, raw_value: str | None) -> str:
        if not raw_value:
            return ""
        try:
            decrypted = self.crypto.decrypt(raw_value)
            return decrypted or raw_value
        except Exception:
            return raw_value

    async def _mark_auth_failure(self, system_id: UUID, error: str) -> None:
        now = _utc_now()
        service_logger.bind(system_id=str(system_id), error=error).warning("Marking auth failure on system")
        stmt = (
            update(WebSystem)
            .where(WebSystem.id == system_id)
            .values(
                auth_fail_count=WebSystem.auth_fail_count + 1,
                last_auth_error=error,
                health_status="auth_failed",
                updated_at=now,
            )
        )
        await self.session.exec(stmt)
        await self.session.commit()

    async def _save_state(
        self,
        system: WebSystem,
        capture: AuthCapture,
        analysis: AuthAnalysis,
        validation: ValidationResult,
    ) -> UUID:
        now = _utc_now()

        invalidate_stmt = (
            update(StorageState)
            .where(StorageState.system_id == system.id, StorageState.is_valid.is_(True))
            .values(is_valid=False, updated_at=now)
        )
        await self.session.exec(invalidate_stmt)

        encrypted_auth = self.crypto.encrypt(analysis.authorization_value)
        state = StorageState(
            system_id=system.id,
            storage_state=capture.storage_state,
            cookies=capture.cookies,
            local_storage=capture.local_storage,
            session_storage=capture.session_storage,
            request_headers=capture.request_headers,
            auth_mode=analysis.auth_mode,
            playback_strategy=analysis.playback_strategy,
            authorization_source=analysis.authorization_source,
            authorization_schema=analysis.authorization_schema,
            authorization_value=encrypted_auth,
            auth_fingerprint=analysis.auth_fingerprint,
            validate_status_code=validation.status_code,
            validate_response_ms=validation.response_ms,
            validate_error=validation.error,
            is_valid=True,
            validated_at=now,
        )
        self.session.add(state)
        await self.session.flush()

        update_system_stmt = (
            update(WebSystem)
            .where(WebSystem.id == system.id)
            .values(
                latest_valid_state_id=state.id,
                last_auth_at=now,
                last_auth_validation_at=now if validation.is_valid is not None else system.last_auth_validation_at,
                auth_fail_count=0,
                last_auth_error=None,
                health_status="online",
                updated_at=now,
            )
        )
        await self.session.exec(update_system_stmt)
        await self.session.commit()
        return state.id


def analyze_auth_payload(
    *,
    request_headers: dict[str, str],
    local_storage: dict[str, Any],
    session_storage: dict[str, Any],
    cookies: list[dict[str, Any]],
    default_playback_strategy: str,
) -> AuthAnalysis:
    """Infer auth mode and primary authorization source from captured artifacts."""

    headers = {str(k).lower(): str(v) for k, v in (request_headers or {}).items() if v is not None}
    header_auth = headers.get("authorization")
    header_schema, _header_value = _split_authorization(header_auth)

    local_token = _first_token(local_storage)
    session_token = _first_token(session_storage)
    cookie_token = _first_auth_cookie_value(cookies)

    source: str | None = None
    schema: str | None = None
    value: str | None = None

    if header_auth:
        source = "request_header"
        schema = header_schema
        value = header_auth
    elif local_token:
        source = "local_storage"
        schema = "Bearer"
        value = local_token
    elif session_token:
        source = "session_storage"
        schema = "Bearer"
        value = session_token
    elif cookie_token:
        source = "cookie"
        value = cookie_token

    bearer_present = bool(header_auth or local_token or session_token)
    cookie_present = bool(cookie_token)
    if bearer_present and cookie_present:
        auth_mode = "hybrid"
    elif bearer_present:
        auth_mode = "bearer"
    elif cookie_present:
        auth_mode = "cookie_session"
    else:
        auth_mode = "unknown"

    default_playback_strategy = (default_playback_strategy or "auto").lower()
    if default_playback_strategy in {"header", "cookie", "hybrid"}:
        playback_strategy = default_playback_strategy
    elif auth_mode == "bearer":
        playback_strategy = "header"
    elif auth_mode == "cookie_session":
        playback_strategy = "cookie"
    elif auth_mode == "hybrid":
        playback_strategy = "hybrid"
    else:
        playback_strategy = "auto"

    return AuthAnalysis(
        auth_mode=auth_mode,
        playback_strategy=playback_strategy,
        authorization_source=source,
        authorization_schema=schema,
        authorization_value=value,
        auth_fingerprint=_fingerprint(value),
    )


def _split_authorization(auth_value: str | None) -> tuple[str | None, str | None]:
    if not auth_value:
        return None, None
    value = auth_value.strip()
    if not value:
        return None, None
    parts = value.split(" ", 1)
    if len(parts) == 2 and parts[0]:
        return parts[0], parts[1]
    return None, value


def _first_token(storage: dict[str, Any]) -> str | None:
    for key in TOKEN_KEYS:
        value = storage.get(key)
        if value:
            return str(value)

    lower_map = {str(k).lower(): v for k, v in storage.items()}
    for key, value in lower_map.items():
        if any(token in key for token in ("token", "authorization", "jwt")) and value:
            return str(value)
    return None


def _first_auth_cookie_value(cookies: list[dict[str, Any]]) -> str | None:
    for cookie in cookies:
        name = str(cookie.get("name") or "").lower()
        if name in AUTH_COOKIE_KEYS and cookie.get("value"):
            return str(cookie["value"])
    return None


def _fingerprint(auth_value: str | None) -> str | None:
    if not auth_value:
        return None
    return hashlib.sha256(auth_value.encode("utf-8")).hexdigest()[:32]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
