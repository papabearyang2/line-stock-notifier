from datetime import date
from time import monotonic

import httpx
import pytest

from app.providers import market
from app.providers.market import Company, MarketCatalog


@pytest.fixture
def catalog() -> MarketCatalog:
    result = MarketCatalog()
    result._companies = {
        "2330": Company(code="2330", name="台積電"),
        "2454": Company(code="2454", name="聯發科"),
    }
    result._loaded_at = monotonic()
    return result


@pytest.mark.asyncio
async def test_find_by_name_returns_exact_company(catalog: MarketCatalog):
    assert await catalog.find_by_name("台積電") == Company(code="2330", name="台積電")


@pytest.mark.asyncio
async def test_get_many_preserves_requested_codes(catalog: MarketCatalog):
    companies = await catalog.get_many(["2454", "9999", "2330"])

    assert list(companies) == ["2454", "2330"]


@pytest.mark.asyncio
async def test_trading_day_uses_twse_holiday_schedule(monkeypatch):
    market._closed_days.clear()

    async def get(*args, **kwargs):
        request = httpx.Request("GET", market.TWSE_HOLIDAY_URL)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": [
                    ["2026-09-25", "中秋節", "依規定放假1日。"],
                    ["2026-02-23", "農曆春節後開始交易日", "開始交易。"],
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", get)

    assert not await market.is_trading_day(date(2026, 9, 25))
    assert await market.is_trading_day(date(2026, 2, 23))
    assert not await market.is_trading_day(date(2026, 8, 22))
