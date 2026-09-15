from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import httpx

from app.providers.market import market_catalog


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    source: str
    published_at: datetime | None
    stock_code: str


class GoogleNewsProvider:
    RSS_URL = "https://news.google.com/rss/search"
    SOURCE_FILTERS = (
        ("site:ctee.com.tw", "工商時報"),
        ("site:goodinfo.tw", "Goodinfo"),
        ("", None),
    )

    async def get_stock_news(
        self, stock_code: str, days: int = 3, limit: int = 5
    ) -> list[Article]:
        company = await market_catalog.get(stock_code)
        name = company.name if company else ""
        subject = f'("{stock_code}" OR "{name}")' if name else f'"{stock_code}"'
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            for source_filter, source_name in self.SOURCE_FILTERS:
                query = f"{subject} 台股 {source_filter} when:{days}d"
                results = await self._search(
                    client,
                    query=query,
                    stock_code=stock_code,
                    days=days,
                    limit=limit,
                    source_name=source_name,
                )
                if results:
                    return results
        return []

    async def _search(
        self,
        client: httpx.AsyncClient,
        *,
        query: str,
        stock_code: str,
        days: int,
        limit: int,
        source_name: str | None,
    ) -> list[Article]:
        url = (
            f"{self.RSS_URL}?q={quote_plus(query)}"
            "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        )
        response = await client.get(url, headers={"User-Agent": "StockResearchBot/1.0"})
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        results: list[Article] = []
        seen: set[str] = set()
        for entry in feed.entries:
            published = self._parse_date(entry.get("published"))
            if published and published < cutoff:
                continue
            title = str(entry.get("title", "")).strip()
            link = str(entry.get("link", "")).strip()
            if not title or not link or link in seen:
                continue
            seen.add(link)
            source = ""
            source_data = entry.get("source")
            if isinstance(source_data, dict):
                source = str(source_data.get("title", ""))
            if source_name:
                source = source_name
            results.append(Article(title, link, source, published, stock_code))
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        results.sort(key=lambda article: article.published_at or oldest, reverse=True)
        return results[:limit]

    @staticmethod
    def _parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            result = parsedate_to_datetime(value)
            return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None


news_provider = GoogleNewsProvider()
