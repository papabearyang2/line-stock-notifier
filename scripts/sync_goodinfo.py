"""Audit or run the pilot-gated Goodinfo automation procedure."""

from __future__ import annotations

import argparse
import asyncio
import json

from app.goodinfo_backfill import (
    audit_goodinfo_gaps,
    run_goodinfo_automation,
    run_goodinfo_backfill_cycle,
)
from app.providers.goodinfo import goodinfo_provider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("research", "market"), default="research")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--direct-cycle", action="store_true")
    parser.add_argument("--include-pages", action="store_true")
    return parser.parse_args()


async def run(arguments: argparse.Namespace) -> None:
    try:
        if arguments.years < 1:
            raise ValueError("years must be positive")
        if arguments.max_pages is not None and arguments.max_pages < 1:
            raise ValueError("max-pages must be positive")
        if arguments.audit_only:
            result = audit_goodinfo_gaps(
                scope=arguments.scope,
                max_pages_per_run=arguments.max_pages,
            )
        elif arguments.direct_cycle:
            result = await run_goodinfo_backfill_cycle(
                scope=arguments.scope,
                years=arguments.years,
                max_pages=arguments.max_pages,
            )
        else:
            result = await run_goodinfo_automation(
                scope=arguments.scope,
                years=arguments.years,
                max_pages=arguments.max_pages,
            )
        if not arguments.include_pages:
            result = dict(result)
            pages = result.pop("pages", result.pop("browser_export_queue", []))
            result["next_pages"] = pages[:20]
            result["next_pages_truncated"] = max(0, len(pages) - len(result["next_pages"]))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await goodinfo_provider.close()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
