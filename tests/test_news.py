from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import unquote_plus

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

    requested_urls = []

    async def get(_client, url, **kwargs):
        requested_urls.append(unquote_plus(str(url)))
        request = httpx.Request("GET", url)
        return httpx.Response(200, content=rss, request=request)

    monkeypatch.setattr(market_catalog, "get", no_company)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)

    articles = await GoogleNewsProvider().get_stock_news("2330", limit=3)

    assert [article.title for article in articles] == ["最新", "較舊", "無日期"]
    assert [article.source for article in articles] == ["工商時報"] * 3
    assert len(requested_urls) == 1
    assert "site:ctee.com.tw" in requested_urls[0]


async def test_news_falls_back_to_goodinfo_when_ctee_has_no_results(monkeypatch):
    published = format_datetime(datetime(2099, 1, 2, tzinfo=timezone.utc), usegmt=True)
    empty_rss = "<rss><channel></channel></rss>"
    goodinfo_rss = f"""
    <rss><channel><item>
      <title>2330 台積電新聞及公告</title>
      <link>https://example.com/goodinfo</link>
      <pubDate>{published}</pubDate>
    </item></channel></rss>
    """
    requested_urls = []

    async def company(_):
        return type("Company", (), {"name": "台積電"})()

    async def get(_client, url, **kwargs):
        decoded_url = unquote_plus(str(url))
        requested_urls.append(decoded_url)
        content = empty_rss if "site:ctee.com.tw" in decoded_url else goodinfo_rss
        return httpx.Response(200, content=content, request=httpx.Request("GET", url))

    monkeypatch.setattr(market_catalog, "get", company)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)

    articles = await GoogleNewsProvider().get_stock_news("2330")

    assert [article.source for article in articles] == ["Goodinfo"]
    assert len(requested_urls) == 2
    assert "site:ctee.com.tw" in requested_urls[0]
    assert "site:goodinfo.tw" in requested_urls[1]
