"""Sentry SDK initialization and configuration."""

from __future__ import annotations

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from src.config.settings import get_settings


def init_sentry() -> None:
    """Initialize Sentry if DSN is configured."""

    settings = get_settings()
    if not settings.sentry_dsn:
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        integrations=[LoggingIntegration()],
    )
