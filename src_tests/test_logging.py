from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace

import pytest
from loguru import logger as loguru_logger

from src.config.settings import get_settings
from src.infrastructure.logging import get_logger, setup_logging, setup_uvicorn_logging


def test_setup_logging_creates_stdout_and_file_sinks(tmp_path):
    log_file = tmp_path / "app.log"

    setup_logging(log_level="INFO", log_file=str(log_file))
    get_logger("test.logging").info("setup sink test")

    assert log_file.exists()
    assert len(loguru_logger._core.handlers) >= 2  # noqa: SLF001


def test_setup_logging_debug_level_writes_debug_messages(tmp_path):
    log_file = tmp_path / "app.log"
    marker = "debug-level-marker"

    setup_logging(log_level="DEBUG", log_file=str(log_file))
    get_logger("test.logging").debug(marker)

    assert log_file.exists()
    assert marker in log_file.read_text(encoding="utf-8")


def test_setup_uvicorn_logging_bridges_to_loguru_file(tmp_path):
    log_file = tmp_path / "app.log"
    error_marker = "uvicorn-error-bridge"
    access_marker = "uvicorn-access-bridge"

    setup_logging(log_level="INFO", log_file=str(log_file))
    setup_uvicorn_logging()
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)

    logging.getLogger("uvicorn.error").error(error_marker)
    logging.getLogger("uvicorn.access").info(access_marker)

    content = log_file.read_text(encoding="utf-8")
    assert error_marker in content
    assert access_marker in content


@pytest.mark.asyncio
async def test_main_lifespan_initializes_file_logging(monkeypatch, tmp_path):
    log_file = tmp_path / "startup.log"
    marker = "main-lifespan-log-marker"

    monkeypatch.setenv("ENCRYPTION_KEY", "test-main-logging-key")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FILE", str(log_file))
    monkeypatch.setenv("LOG_JSON", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()

    main_mod = importlib.import_module("src.main")

    async def _noop_async():
        return None

    class _DummySchedulerManager:
        def __init__(self) -> None:
            self.scheduler = SimpleNamespace(running=False)

        def start(self) -> None:
            self.scheduler.running = False

        def shutdown(self) -> None:
            self.scheduler.running = False

    class _DummyLock:
        async def close(self) -> None:
            return None

    monkeypatch.setattr(main_mod, "init_db", _noop_async)
    monkeypatch.setattr(main_mod, "close_db", _noop_async)
    monkeypatch.setattr(main_mod, "scheduler_manager", _DummySchedulerManager())
    monkeypatch.setattr(main_mod, "distributed_lock", _DummyLock())

    async with main_mod.lifespan(main_mod.app):
        main_mod.app_logger.info(marker)

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "Application startup initiated" in content
    assert marker in content
