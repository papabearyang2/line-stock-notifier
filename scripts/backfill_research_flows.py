"""Backfill institutional share counts for quote dates already in the database."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from app.research_service import refresh_research_flows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--calendar-days", type=int, default=190)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    start = arguments.start or arguments.end - timedelta(days=arguments.calendar_days)
    print(asyncio.run(refresh_research_flows(start, arguments.end)))
