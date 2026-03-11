#!/usr/bin/env python3
"""Manual CLI to run menu map crawl from DB-managed session state."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.database import close_db, init_db, session_scope
from src.services.crawl_service import CrawlService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl menu map using latest valid state from database")
    parser.add_argument("--sys-code", required=True, help="Target system code from web_systems.sys_code")
    parser.add_argument("--home-url", default="", help="Override crawl home url")
    parser.add_argument("--menu-selector", default="", help="Optional explicit menu selector")
    parser.add_argument("--max-pages", type=int, default=10, help="Max pages to crawl")
    parser.add_argument("--max-elements-per-page", type=int, default=180, help="Max elements per page")
    parser.add_argument("--max-modal-triggers", type=int, default=8, help="Max modal trigger attempts per page")
    parser.add_argument("--expand-rounds", type=int, default=6, help="DOM menu expand rounds")
    parser.add_argument("--timeout-ms", type=int, default=45_000, help="Playwright timeout")
    parser.add_argument(
        "--framework-hint",
        choices=("auto", "vue2", "vue3", "react"),
        default="auto",
        help="Optional framework hint for route extraction.",
    )
    parser.add_argument(
        "--strict-mode",
        action="store_true",
        help="Fail run when coverage score is low instead of degraded success.",
    )
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    await init_db()
    async with session_scope() as session:
        service = CrawlService(session)
        result = await service.run_by_sys_code(
            args.sys_code,
            headed=args.headed,
            timeout_ms=args.timeout_ms,
            max_pages=args.max_pages,
            max_elements_per_page=args.max_elements_per_page,
            max_modal_triggers=args.max_modal_triggers,
            expand_rounds=args.expand_rounds,
            menu_selector=args.menu_selector,
            home_url=args.home_url,
            framework_hint=args.framework_hint,
            strict_mode=args.strict_mode,
        )
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
        if result.status not in {"success", "auth_triggered"}:
            return 1

    await close_db()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(parse_args())))


if __name__ == "__main__":
    main()
