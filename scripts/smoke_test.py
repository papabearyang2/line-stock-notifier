"""Read-only live provider smoke test. Run manually; not part of unit tests."""

import asyncio

from app.charts import gross_margin_chart, pe_river_chart
from app.providers.finmind import finmind_provider
from app.providers.market import market_catalog
from app.providers.news import news_provider


async def main() -> None:
    await market_catalog.refresh(force=True)
    company = await market_catalog.get("2330")
    news = await news_provider.get_stock_news("2330", 3, 2)
    valuation = await finmind_provider.valuation_history("2330", 1)
    gross_margin = await finmind_provider.gross_margin_history("2330", 2)
    gross_path, gross_source = await gross_margin_chart("2330")
    river_path, river_source = await pe_river_chart("2330")
    print("company", company)
    print("news", len(news))
    print("valuation", len(valuation), list(valuation.columns))
    print("gross_margin", len(gross_margin), list(gross_margin.columns))
    print("gross_chart", gross_path, gross_source)
    print("river_chart", river_path, river_source)


if __name__ == "__main__":
    asyncio.run(main())
