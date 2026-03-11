"""Loguru-based logging configuration."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger


def setup_logging(
    *,
    log_level: str = "INFO",
    log_file: str | None = None,
    rotation: str = "100 MB",
    retention: str = "30 days",
    json_format: bool = False,
) -> None:
    """Configure console/file handlers for Loguru."""

    logger.remove()

    if json_format:
        logger.add(sys.stdout, level=log_level, serialize=True)
    else:
        logger.add(
            sys.stdout,
            level=log_level,
            colorize=True,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | <level>{message}</level>",
            backtrace=True,
            diagnose=False,
        )

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(path),
            level=log_level,
            rotation=rotation,
            retention=retention,
            compression="zip",
            encoding="utf-8",
            serialize=json_format,
        )


class _InterceptHandler(logging.Handler):
    """Forward stdlib logging records to Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.bind(logger_name=record.name).opt(
            depth=depth,
            exception=record.exc_info,
        ).log(level, record.getMessage())


def setup_uvicorn_logging() -> None:
    """Redirect uvicorn access/error loggers to Loguru sinks."""

    intercept = _InterceptHandler()
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target_logger = logging.getLogger(logger_name)
        target_logger.handlers = [intercept]
        target_logger.propagate = False


def get_logger(name: str):
    """Return logger bound with a component name."""

    return logger.bind(component=name)


def log_function_call(func):
    """Decorator that logs function start/end and exceptions."""

    def wrapper(*args, **kwargs):
        component_logger = get_logger(func.__module__)
        component_logger.debug(f"Entering {func.__name__}")
        try:
            result = func(*args, **kwargs)
            component_logger.debug(f"Exiting {func.__name__}")
            return result
        except Exception:
            component_logger.exception(f"Exception in {func.__name__}")
            raise

    return wrapper
