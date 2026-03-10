"""Auth state validation service."""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from src.crawler.auth_crawler import AuthCapture
from src.models.web_system import WebSystem


@dataclass(slots=True)
class ValidationResult:
    """Validation result for a captured auth payload."""

    is_valid: bool | None
    status_code: int | None
    response_ms: int | None
    error: str | None


async def validate_capture(
    system: WebSystem,
    capture: AuthCapture,
    timeout_seconds: int = 10,
) -> ValidationResult:
    """Validate captured auth payload against system validation endpoint."""

    endpoint = (system.auth_validate_endpoint or "").strip()
    if not endpoint:
        return ValidationResult(is_valid=None, status_code=None, response_ms=None, error=None)

    validate_url = endpoint if endpoint.startswith("http") else urljoin(system.base_url, endpoint)
    headers = {k: v for k, v in (capture.request_headers or {}).items() if v}
    if capture.authorization and "authorization" not in headers:
        headers["authorization"] = capture.authorization
    cookies = {
        str(cookie.get("name")): str(cookie.get("value"))
        for cookie in capture.cookies
        if cookie.get("name") and cookie.get("value")
    }

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                validate_url,
                headers=headers,
                cookies=cookies,
                timeout=timeout_seconds,
            )
        response_ms = int((time.perf_counter() - start) * 1000)
        is_valid = response.status_code not in {401, 403}
        return ValidationResult(
            is_valid=is_valid,
            status_code=response.status_code,
            response_ms=response_ms,
            error=None,
        )
    except Exception as exc:
        response_ms = int((time.perf_counter() - start) * 1000)
        return ValidationResult(
            is_valid=False,
            status_code=None,
            response_ms=response_ms,
            error=str(exc),
        )
