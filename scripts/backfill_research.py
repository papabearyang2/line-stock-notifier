"""Backfill official daily batches from the newest trading session backwards."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from app.providers.market import is_trading_day
from app.research_service import refresh_research_day, refresh_research_indices


async def run(start: date, end: date, *, include_flows: bool, pause: float) -> None:
    days: list[date] = []
    cursor = end
    while cursor >= start:
        if await is_trading_day(cursor):
            days.append(cursor)
        cursor -= timedelta(days=1)

    print(f"將回補 {len(days)} 個交易日：{end} 起往前至 {start}")
    succeeded = partial = failed = 0
    for index, day in enumerate(days, start=1):
        result = await refresh_research_day(
            day,
            include_flows=include_flows,
            include_indices=False,
        )
        status = result["status"]
        succeeded += status == "success"
        partial += status == "partial"
        failed += status == "failed"
        if index == 1 or index % 10 == 0 or status != "success" or index == len(days):
            print(
                f"[{index}/{len(days)}] {day} {status} "
                f"quotes={result['quotes']} flows={result['flows']}"
            )
        if pause:
            await asyncio.sleep(pause)

    indices = await refresh_research_indices(start, end)
    print(
        f"完成：success={succeeded} partial={partial} failed={failed}; "
        f"index_points={indices['indices']} index_status={indices['status']}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--calendar-days",
        type=int,
        default=190,
        help="Used only when --start is omitted; 190 calendar days covers about 130 trading days.",
    )
    parser.add_argument("--no-flows", action="store_true")
    parser.add_argument("--pause", type=float, default=0.35)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    start_date = arguments.start or arguments.end - timedelta(days=arguments.calendar_days)
    asyncio.run(
        run(
            start_date,
            arguments.end,
            include_flows=not arguments.no_flows,
            pause=max(arguments.pause, 0),
        )
    )
