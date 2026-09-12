import json
from datetime import datetime, timezone

import pandas as pd

from app.providers.company_research import company_research_provider
from app.providers.finmind import finmind_provider
from app.providers.news import Article, news_provider


async def test_financials_parses_aliases_and_preserves_missing_values(monkeypatch):
    statements = pd.DataFrame(
        {
            "report_date": ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"],
            "account": ["EPS"] * 4,
            "amount": [1.0, 2.0, 3.0, 4.0],
        }
    )
    cash = pd.DataFrame(
        {
            "date": ["2024-06-30", "2024-06-30"],
            "type": ["CashFlowsFromOperatingActivities", "CashProvidedByInvestingActivities"],
            "value": [120, pd.NA],
        }
    )
    revenue = pd.DataFrame(
        {
            "revenue_year": [2024, 2024],
            "revenue_month": [1, 2],
            "Revenue": [1000, pd.NA],
            "create_time": ["2024-02-10", ""],
        }
    )
    per = pd.DataFrame({"trade_date": ["2024-01-02"], "pe_ratio": [18.5]})
    frames = {
        "TaiwanStockFinancialStatements": statements,
        "TaiwanStockCashFlowsStatement": cash,
        "TaiwanStockMonthRevenue": revenue,
        "TaiwanStockPER": per,
    }

    async def dataset(name, code, start_date, end_date=None):
        return frames[name]

    monkeypatch.setattr(finmind_provider, "dataset", dataset)
    monkeypatch.setattr("app.providers.company_research.read_goodinfo_snapshot", lambda *args: None)
    result = await company_research_provider.financials("6274")

    assert result["eps"]["items"][-1]["single_quarter_eps"] == 4
    assert result["eps"]["items"][-1]["ttm_eps"] == 10
    assert result["cash_flow"]["items"][0]["operating"] == 120
    assert result["cash_flow"]["items"][0]["investing"] is None
    assert result["cash_flow"]["items"][0]["financing"] is None
    assert result["cash_flow"]["items"][0]["basis"] == "reported_cumulative_ytd"
    assert result["monthly_revenue"]["items"] == [
        {
            "month": "2024-01",
            "revenue": 1000,
            "unit": "source_reported",
            "finmind_observed_at": "2024-02-10",
        }
    ]
    assert result["pe"]["items"][0]["pe"] == 18.5
    assert all(
        section["source_url"]
        for section in (result["eps"], result["cash_flow"], result["pe"])
    )
    json.dumps(result)


async def test_financials_marks_failed_or_empty_sections_unavailable(monkeypatch):
    async def dataset(name, code, start_date, end_date=None):
        if name == "TaiwanStockFinancialStatements":
            raise RuntimeError("offline")
        return pd.DataFrame()

    monkeypatch.setattr(finmind_provider, "dataset", dataset)
    monkeypatch.setattr("app.providers.company_research.read_goodinfo_snapshot", lambda *args: None)
    result = await company_research_provider.financials("6274")

    assert result["status"] == "unavailable"
    assert result["eps"]["last_verified"] is None
    assert result["eps"]["items"] == []
    assert result["monthly_revenue"]["status"] == "unavailable"
    assert result["monthly_revenue"]["items"] == []


async def test_financials_reads_persisted_goodinfo_only_when_finmind_is_missing(monkeypatch):
    async def dataset(name, code, start_date, end_date=None):
        return pd.DataFrame()

    def snapshot(code, section):
        if section == "monthly_revenue":
            return {
                "status": "available",
                "items": [{"month": "2026-07", "revenue": 100}],
                "source": "Goodinfo（補充）",
                "source_url": "https://goodinfo.tw/tw/ShowSaleMonChart.asp?STOCK_ID=6274",
                "last_verified": "2026-08-31T00:00:00+00:00",
            }
        return None

    monkeypatch.setattr(finmind_provider, "dataset", dataset)
    monkeypatch.setattr("app.providers.company_research.read_goodinfo_snapshot", snapshot)
    result = await company_research_provider.financials("6274")

    assert result["monthly_revenue"]["source"] == "Goodinfo（補充）"
    assert result["monthly_revenue"]["items"][0]["month"] == "2026-07"
    assert result["monthly_revenue"]["sources"][0]["type"] == "secondary_html"


async def test_dividends_are_aggregated_but_fill_remains_unavailable(monkeypatch):
    frame = pd.DataFrame(
        {
            "date": ["2025-03-01"],
            "distribution_year": [2024],
            "announcement_date": ["2025-02-28"],
            "cash_earnings_distribution": [3.0],
            "cash_statutory_surplus": [0.5],
            "stock_earnings_distribution": [pd.NA],
            "stock_statutory_surplus": [0],
            "cash_ex_dividend_date": ["2025-07-01"],
            "cash_dividend_payment_date": [pd.NA],
        }
    )

    async def dataset(name, code, start_date, end_date=None):
        assert name == "TaiwanStockDividend"
        return frame

    monkeypatch.setattr(finmind_provider, "dataset", dataset)
    result = await company_research_provider.dividends("6274")
    item = result["items"][0]

    assert result["source"] == "FinMind (aggregated)"
    assert result["official_reference_urls"]["twse"]["calculation_result"]
    assert item["cash_dividend_per_share"] == 3.5
    assert item["stock_dividend_per_share"] is None
    assert item["cash_payment_date"] is None
    assert item["pre_ex_close"] is None
    assert item["fill"]["status"] == "unavailable"
    json.dumps(result)


async def test_news_conservatively_deduplicates_normalized_titles(monkeypatch):
    older = datetime(2026, 8, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 8, 1, 2, tzinfo=timezone.utc)

    async def get_stock_news(code, days=30, limit=100):
        return [
            Article("台燿：營收創高 - 財經報", "https://news/old", "財經報", older, code),
            Article("台燿營收創高－財經報", "https://news/new", "財經報", newer, code),
            Article("台燿擴產", "https://news/other", "另一媒體", None, code),
        ]

    monkeypatch.setattr(news_provider, "get_stock_news", get_stock_news)
    result = await company_research_provider.news("6274", days=30)

    assert result["status"] == "available"
    assert len(result["items"]) == 2
    event = next(item for item in result["items"] if "營收創高" in item["title"])
    assert event["aggregator_url"] == "https://news/new"
    assert event["original_url"] is None
    assert event["first_published_at"] == older.isoformat()
    assert event["kind"] == "media_report"
    assert "sentiment" not in event
    json.dumps(result)
