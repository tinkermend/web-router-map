#!/usr/bin/env python3
"""Manual CLI to refresh and persist storage state for a system."""

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
from src.services.auth_service import AuthService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh storage state and cookies for a web system")
    parser.add_argument("--sys-code", required=True, help="Target system code from web_systems.sys_code")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    parser.add_argument("--timeout-ms", type=int, default=None, help="Playwright timeout override")
    return parser.parse_args()


async def _run(sys_code: str, headed: bool, timeout_ms: int | None) -> int:
    await init_db()
    async with session_scope() as session:
        service = AuthService(session)
        result = await service.refresh_by_sys_code(sys_code, headed=headed, timeout_ms=timeout_ms)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
        if result.status != "success":
            return 1
    await close_db()
    return 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(_run(args.sys_code, args.headed, args.timeout_ms)))


if __name__ == "__main__":
    main()
