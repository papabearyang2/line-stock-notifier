from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.commands import (
    _format_published_at,
    _stock_label,
    build_news_digest,
    extract_codes,
    handle_command,
)
from app.models import Base
from app.providers.market import Company
from app.providers.news import Article


def test_extract_codes_deduplicates_and_preserves_order():
    assert extract_codes("新增 2330、2454 和 2330") == ["2330", "2454"]


def test_extract_codes_ignores_dates_and_short_numbers():
    assert extract_codes("近3天 2026/07/27 查 0050") == ["0050"]


def test_format_published_at_uses_configured_timezone():
    published_at = datetime(2026, 7, 28, 1, 30, tzinfo=timezone.utc)

    assert _format_published_at(published_at, "Asia/Taipei") == "2026-07-28 09:30"


def test_format_published_at_handles_missing_date():
    assert _format_published_at(None, "Asia/Taipei") == "日期未知"


def test_stock_label_contains_code_and_company_name():
    company = Company(code="2330", name="台積電")

    assert _stock_label("2330", company) == "2330 台積電"


async def test_unknown_text_is_silent():
    result = await handle_command(None, "conversation", "你好")  # type: ignore[arg-type]

    assert result.messages == []


async def test_stock_code_without_keyword_is_silent():
    result = await handle_command(None, "conversation", "2330")  # type: ignore[arg-type]

    assert result.messages == []


async def test_news_digest_uses_local_short_links(monkeypatch):
    article_url = "https://example.com/news/a-very-long-article-url"

    async def news(*args, **kwargs):
        return [Article("標題", article_url, "來源", None, "2330")]

    async def companies(_):
        return {}

    monkeypatch.setattr("app.commands.news_provider.get_stock_news", news)
    monkeypatch.setattr("app.commands.market_catalog.get_many", companies)
    monkeypatch.setattr(
        "app.commands.get_settings",
        lambda: SimpleNamespace(
            news_lookback_days=3,
            timezone="Asia/Taipei",
            public_base_url="https://stocks.example",
        ),
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        digest = await build_news_digest(session, ["2330"])

    assert article_url not in digest
    assert "https://stocks.example/n/" in digest
