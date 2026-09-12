"""Backfill one stock's missing Goodinfo supplements, newest periods first."""

from __future__ import annotations

import argparse
import asyncio

from app.research_supplements import backfill_goodinfo_stock
from app.providers.goodinfo import goodinfo_provider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-code", required=True, help="四碼台股代號，例如 6274")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--force", action="store_true", help="忽略補充資料 TTL，重新檢查")
    parser.add_argument(
        "--sections",
        nargs="+",
        choices=["monthly_revenue", "eps", "cash_flow", "pe", "dividends", "product_mix"],
        help="只補指定區段；預設補所有缺漏區段",
    )
    return parser.parse_args()


async def run(arguments: argparse.Namespace) -> None:
    try:
        result = await backfill_goodinfo_stock(
            arguments.stock_code,
            years=arguments.years,
            force=arguments.force,
            sections=arguments.sections,
        )
        print(result)
        if result.get("status") == "partial":
            print(
                "Goodinfo 可能要求瀏覽器驗證；請從瀏覽器匯出 HTML，"
                "再用 scripts/import_goodinfo_html.py 匯入。"
            )
    finally:
        await goodinfo_provider.close()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
