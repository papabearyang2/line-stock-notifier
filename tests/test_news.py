from datetime import datetime, timezone
from email.utils import format_datetime

import httpx

from app.providers.market import market_catalog
from app.providers.news import GoogleNewsProvider


async def test_news_is_sorted_newest_first(monkeypatch):
    older = format_datetime(datetime(2099, 1, 1, tzinfo=timezone.utc), usegmt=True)
    newer = format_datetime(datetime(2099, 1, 2, tzinfo=timezone.utc), usegmt=True)
    rss = f"""
    <rss><channel>
      <item><title>較舊</title><link>https://example.com/old</link><pubDate>{older}</pubDate></item>
      <item><title>最新</title><link>https://example.com/new</link><pubDate>{newer}</pubDate></item>
      <item><title>無日期</title><link>https://example.com/unknown</link></item>
    </channel></rss>
    """

    async def no_company(_):
        return None

    async def get(*args, **kwargs):
        request = httpx.Request("GET", GoogleNewsProvider.RSS_URL)
        return httpx.Response(200, content=rss, request=request)

    monkeypatch.setattr(market_catalog, "get", no_company)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)

    articles = await GoogleNewsProvider().get_stock_news("2330", limit=3)

    assert [article.title for article in articles] == ["最新", "較舊", "無日期"]
