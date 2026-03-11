"""Logging helpers for retrieval observability."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger


def configure_logging(level: str) -> None:
    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        level=level,
        serialize=False,
    )


def log_retrieval_event(event: str, payload: dict[str, Any]) -> None:
    logger.bind(event=event).info(json.dumps(payload, ensure_ascii=False, default=str))
