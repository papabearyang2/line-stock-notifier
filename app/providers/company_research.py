"""Free structured sources for the stock research page."""

from __future__ import annotations

import asyncio
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from app.providers.finmind import finmind_provider
from app.providers.news import Article, news_provider
from app.research_supplements import (
    backfill_goodinfo_stock,
    goodinfo_source_url,
    read_goodinfo_snapshot,
)


FINMIND_DOC_URL = "https://finmind.github.io/tutor/TaiwanMarket/Fundamental/"
TWSE_EX_DIVIDEND_URLS = {
    "preannouncement": "https://www.twse.com.tw/exchangeReport/TWT48U?response=html",
    "calculation_result": "https://www.twse.com.tw/exchangeReport/TWT49U?response=html",
}
TPEX_EX_DIVIDEND_URLS = {
    "preannouncement": "https://www.tpex.org.tw/zh-tw/announce/market/ex/announce.html",
    "calculation_result": "https://www.tpex.org.tw/zh-tw/announce/market/ex/cal.html",
}


class CompanyResearchProvider:
    async def financials(self, code: str, years: int = 5) -> dict[str, Any]:
        if years < 1:
            raise ValueError("years must be positive")
        start = date.today() - timedelta(days=366 * years)
        checked_at = _now()
        datasets = (
            "TaiwanStockFinancialStatements",
            "TaiwanStockCashFlowsStatement",
            "TaiwanStockMonthRevenue",
            "TaiwanStockPER",
        )
        results = await asyncio.gather(
            *(finmind_provider.dataset(name, code, start) for name in datasets),
            return_exceptions=True,
        )

        frames: list[pd.DataFrame] = []
        fetched: list[bool] = []
        for result in results:
            ok = isinstance(result, pd.DataFrame)
            frames.append(result if ok else pd.DataFrame())
            fetched.append(ok)

        eps = _eps(frames[0], years * 4)
        cash_flow = _cash_flow(frames[1], years * 4)
        monthly_revenue = _monthly_revenue(frames[2], years * 12)
        pe = _pe(frames[3])
        sections = {
            "eps": _section(
                eps,
                "period",
                fetched[0],
                checked_at,
                source="FinMind · TaiwanStockFinancialStatements",
                note=(
                    "EPS 為 FinMind 單季原始值；TTM 是最近四個連續季度相加。若期間有配股或分割，"
                    "各季加權股數基準可能不同，TTM 可能與公司重編後數字不一致。"
                ),
            ),
            "cash_flow": _section(
                cash_flow,
                "period",
                fetched[1],
                checked_at,
                source="FinMind · TaiwanStockCashFlowsStatement",
                note="保留財報累計口徑；未可靠拆成單季值。",
            ),
            "monthly_revenue": _section(
                monthly_revenue,
                "month",
                fetched[2],
                checked_at,
                source="FinMind · TaiwanStockMonthRevenue",
                note="create_time 是 FinMind 收錄時間，不是公司正式公告時間。",
            ),
            "pe": _section(
                pe,
                "date",
                fetched[3],
                checked_at,
                source="FinMind · TaiwanStockPER",
            ),
        }
        section_keys = {
            "eps": "period",
            "cash_flow": "period",
            "monthly_revenue": "month",
            "pe": "date",
        }
        goodinfo_sources: list[dict[str, Any]] = []
        supplemented_sections: list[str] = []
        for name, section in sections.items():
            snapshot = read_goodinfo_snapshot(code, name)
            if not snapshot:
                continue
            source = _supplement_source(snapshot, name, code)
            goodinfo_sources.append(source)
            items = snapshot.get("items") or []
            if not section["items"] and snapshot.get("status") == "available" and items:
                sections[name] = _section(
                    items,
                    section_keys[name],
                    True,
                    str(snapshot.get("last_verified") or checked_at),
                    source="Goodinfo（補充）",
                    source_url=str(snapshot.get("source_url") or goodinfo_source_url(code, name)),
                    source_type="secondary_html",
                    note="FinMind 無可用資料時，讀取已保存的 Goodinfo 補充資料；請以官方財報核對。",
                )
                supplemented_sections.append(name)
            elif not section["items"] and snapshot.get("reason"):
                section["reason"] = f"{section.get('reason', '查無可用資料')}；{snapshot['reason']}"
        return {
            "stock_code": str(code),
            "status": (
                "available"
                if any(section["status"] == "available" for section in sections.values())
                else "unavailable"
            ),
            "requested_years": years,
            "source": "FinMind；Goodinfo補充（資料庫）" if supplemented_sections else "FinMind",
            "source_url": FINMIND_DOC_URL,
            "sources": [
                *[
                    {
                        "name": f"FinMind · {dataset}",
                        "url": FINMIND_DOC_URL,
                        "dataset": dataset,
                        "last_verified": checked_at if ok else None,
                        "type": "aggregated_public_data",
                    }
                    for dataset, ok in zip(datasets, fetched, strict=True)
                ],
                *goodinfo_sources,
            ],
            **sections,
        }

    async def dividends(self, code: str, years: int = 5) -> dict[str, Any]:
        if years < 1:
            raise ValueError("years must be positive")
        checked_at = _now()
        try:
            frame = await finmind_provider.dataset(
                "TaiwanStockDividend",
                code,
                date.today() - timedelta(days=366 * years),
            )
            fetched = True
        except Exception:  # provider failures become an explicit unavailable section
            frame, fetched = pd.DataFrame(), False

        items = _dividends(frame)
        source = "FinMind (aggregated)"
        source_url = FINMIND_DOC_URL
        source_type = "aggregated_public_data"
        note = (
            "股利事件由 FinMind 聚合。除權息前收盤、官方參考價、歷史殖利率與填權息進度"
            "尚未由官方逐筆結果核對，因此不推算。"
        )
        goodinfo_snapshot = read_goodinfo_snapshot(code, "dividends")
        goodinfo_source = (
            _supplement_source(goodinfo_snapshot, "dividends", code)
            if goodinfo_snapshot
            else None
        )
        if not items:
            fallback = (goodinfo_snapshot or {}).get("items") or []
            if goodinfo_snapshot and goodinfo_snapshot.get("status") == "available" and fallback:
                items = list(fallback)
                fetched = True
                source = "Goodinfo（補充）"
                source_url = str(goodinfo_snapshot.get("source_url") or goodinfo_source_url(code, "dividends"))
                source_type = "secondary_html"
                note = "FinMind 無可用資料時，讀取已保存的 Goodinfo 年度股利政策；除權息日與填權息仍以官方資料核對。"
            elif goodinfo_snapshot and goodinfo_snapshot.get("reason"):
                note = f"{note} Goodinfo 補庫結果：{goodinfo_snapshot['reason']}。"
        result = _section(
            items,
            "announcement_date",
            fetched,
            checked_at,
            source=source,
            source_url=source_url,
            source_type=source_type,
            note=note,
        )
        result.update(
            {
                "stock_code": str(code),
                "requested_years": years,
                "official_reference_urls": {
                    "twse": TWSE_EX_DIVIDEND_URLS,
                    "tpex": TPEX_EX_DIVIDEND_URLS,
                },
                "sources": [
                    *result.get("sources", []),
                    *([goodinfo_source] if goodinfo_source else []),
                    {
                        "name": "TWSE 除權息公告與計算結果",
                        "url": TWSE_EX_DIVIDEND_URLS["calculation_result"],
                        "type": "official_reference",
                    },
                    {
                        "name": "TPEx 除權息公告與計算結果",
                        "url": TPEX_EX_DIVIDEND_URLS["calculation_result"],
                        "type": "official_reference",
                    },
                ],
                "derived_formulas": {
                    "cash_dividend_per_share": (
                        "CashEarningsDistribution + CashStatutorySurplus; "
                        "both components must be present"
                    ),
                    "stock_dividend_per_share": (
                        "StockEarningsDistribution + StockStatutorySurplus; "
                        "both components must be present"
                    ),
                },
            }
        )
        return result

    async def backfill_goodinfo(
        self,
        code: str,
        years: int = 5,
        *,
        force: bool = False,
        sections: list[str] | None = None,
    ) -> dict[str, Any]:
        """Explicitly run the serial Goodinfo backfill; page reads never call this."""
        return await backfill_goodinfo_stock(
            code,
            years=years,
            force=force,
            sections=sections,
        )

    async def news(self, code: str, days: int = 30) -> dict[str, Any]:
        if days not in {7, 30, 90}:
            raise ValueError("days must be one of 7, 30, or 90")
        checked_at = _now()
        try:
            articles = await news_provider.get_stock_news(code, days=days, limit=100)
            fetched = True
        except Exception:  # keep the stock page usable when RSS is unavailable
            articles, fetched = [], False

        items = _news_items(articles)
        result = _section(
            items,
            "first_published_at",
            fetched,
            checked_at,
            source="Google News RSS (aggregated media links)",
            source_url=news_provider.RSS_URL,
            note=(
                "僅列媒體報導，不做情緒判讀。Google News 連結可能是轉址；未解析到原始媒體網址時，"
                "original_url 保持空值。"
            ),
        )
        result.update({"stock_code": str(code), "requested_days": days})
        return result


def _section(
    items: list[dict[str, Any]],
    period_key: str,
    fetched: bool,
    checked_at: str,
    *,
    source: str = "FinMind",
    source_url: str = FINMIND_DOC_URL,
    source_type: str = "aggregated_public_data",
    note: str | None = None,
) -> dict[str, Any]:
    periods = sorted(str(item[period_key]) for item in items if item.get(period_key))
    result: dict[str, Any] = {
        "status": "available" if items else "unavailable",
        "source": source,
        "source_url": source_url,
        "data_period": {"start": periods[0], "end": periods[-1]} if periods else None,
        "last_verified": checked_at if fetched else None,
        "items": items,
        "sources": [
            {
                "name": source,
                "url": source_url,
                "last_verified": checked_at if fetched else None,
                "type": source_type,
            }
        ],
    }
    if not items:
        result["reason"] = "查無可用資料" if fetched else "資料來源暫時無法使用"
    if note:
        result["note"] = note
    return result


def _supplement_source(
    snapshot: dict[str, Any], section: str, code: str
) -> dict[str, Any]:
    return {
        "name": str(snapshot.get("source") or "Goodinfo（補充）"),
        "url": str(snapshot.get("source_url") or goodinfo_source_url(code, section)),
        "dataset": section,
        "last_verified": snapshot.get("last_checked") or snapshot.get("last_verified"),
        "type": "secondary_html",
        "status": snapshot.get("status"),
        "note": snapshot.get("reason"),
    }


def _eps(frame: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    values = _metric_values(
        frame,
        {
            "eps": (
                "EPS",
                "BasicEarningsLossPerShare",
                "BasicEarningsPerShare",
                "基本每股盈餘（元）",
            )
        },
    )
    rows = [
        {"period": period, "single_quarter_eps": metrics["eps"]}
        for period, metrics in sorted(values.items())
        if metrics.get("eps") is not None
    ][-limit:]
    for index, row in enumerate(rows):
        window = rows[max(0, index - 3) : index + 1]
        consecutive = len(window) == 4 and all(
            _quarter_serial(window[offset]["period"]) + 1
            == _quarter_serial(window[offset + 1]["period"])
            for offset in range(3)
        )
        row["ttm_eps"] = (
            _clean_number(sum(item["single_quarter_eps"] for item in window))
            if consecutive
            else None
        )
        row["ttm_status"] = "derived_unadjusted" if consecutive else "unavailable"
        row.update(_year_quarter(row["period"]))
    return rows


def _cash_flow(frame: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    values = _metric_values(
        frame,
        {
            "operating": (
                "CashFlowsFromOperatingActivities",
                "NetCashFlowsFromUsedInOperatingActivities",
                "NetCashInflowFromOperatingActivities",
                "營業活動之淨現金流入（流出）",
                "營業活動之淨現金流入(流出)",
            ),
            "investing": (
                "CashProvidedByInvestingActivities",
                "CashFlowsFromUsedInInvestingActivities",
                "NetCashFlowsFromUsedInInvestingActivities",
                "投資活動之淨現金流入（流出）",
                "投資活動之淨現金流入(流出)",
            ),
            "financing": (
                "CashFlowsProvidedFromFinancingActivities",
                "CashFlowsFromUsedInFinancingActivities",
                "NetCashFlowsFromUsedInFinancingActivities",
                "籌資活動之淨現金流入（流出）",
                "籌資活動之淨現金流入(流出)",
            ),
        },
    )
    rows: list[dict[str, Any]] = []
    for period, metrics in sorted(values.items()):
        if not any(
            metrics.get(name) is not None for name in ("operating", "investing", "financing")
        ):
            continue
        quarter = _year_quarter(period)
        rows.append(
            {
                "period": period,
                **quarter,
                "operating": metrics.get("operating"),
                "investing": metrics.get("investing"),
                "financing": metrics.get("financing"),
                "basis": (
                    "reported_full_year"
                    if quarter["quarter"] == 4
                    else "reported_cumulative_ytd"
                ),
                "single_quarter": None,
            }
        )
    return rows[-limit:]


def _monthly_revenue(frame: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    revenue_col = _column(frame, "revenue", "Revenue", "amount", "當月營收")
    year_col = _column(frame, "revenue_year", "year", "年度")
    month_col = _column(frame, "revenue_month", "month", "月份")
    date_col = _column(frame, "date", "report_date")
    observed_col = _column(frame, "create_time")
    if not revenue_col or not ((year_col and month_col) or date_col):
        return []

    by_month: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        month = None
        year = _integer(row.get(year_col)) if year_col else None
        month_number = _integer(row.get(month_col)) if month_col else None
        if year is not None and month_number is not None and 1 <= month_number <= 12:
            month = f"{year:04d}-{month_number:02d}"
        elif date_col:
            period = _date_text(row.get(date_col))
            month = period[:7] if period else None
        revenue = _clean_number(row.get(revenue_col))
        if month and revenue is not None:
            by_month[month] = {
                "month": month,
                "revenue": revenue,
                "unit": "source_reported",
                "finmind_observed_at": _date_text(row.get(observed_col)) if observed_col else None,
            }
    return [by_month[key] for key in sorted(by_month)][-limit:]


def _pe(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    date_col = _column(frame, "date", "trade_date")
    pe_col = _column(frame, "PER", "pe", "pe_ratio", "本益比")
    if not date_col or not pe_col:
        return []
    by_date: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        period = _date_text(row.get(date_col))
        value = _clean_number(row.get(pe_col))
        if period and value is not None:
            by_date[period] = {"date": period, "pe": value}
    return [by_date[key] for key in sorted(by_date)]


def _dividends(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    columns = {
        name: _column(frame, *aliases)
        for name, aliases in {
            "source_date": ("date",),
            "year": ("year", "distribution_year"),
            "announcement": ("AnnouncementDate", "announcement_date"),
            "cash_earnings": ("CashEarningsDistribution", "cash_earnings_distribution"),
            "cash_reserve": ("CashStatutorySurplus", "cash_statutory_surplus"),
            "stock_earnings": ("StockEarningsDistribution", "stock_earnings_distribution"),
            "stock_reserve": ("StockStatutorySurplus", "stock_statutory_surplus"),
            "cash_ex_date": ("CashExDividendTradingDate", "cash_ex_dividend_date"),
            "stock_ex_date": ("StockExDividendTradingDate", "stock_ex_dividend_date"),
            "payment_date": ("CashDividendPaymentDate", "cash_dividend_payment_date"),
            "record_date": ("RecordDate", "record_date"),
        }.items()
    }
    items: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        def get(key: str) -> Any:
            return row.get(columns[key]) if columns[key] else None

        cash_earnings, cash_reserve = _clean_number(get("cash_earnings")), _clean_number(
            get("cash_reserve")
        )
        stock_earnings, stock_reserve = _clean_number(get("stock_earnings")), _clean_number(
            get("stock_reserve")
        )
        cash_total = _complete_sum(cash_earnings, cash_reserve)
        stock_total = _complete_sum(stock_earnings, stock_reserve)
        announcement = _date_text(get("announcement")) or _date_text(get("source_date"))
        if not announcement and cash_total is None and stock_total is None:
            continue
        items.append(
            {
                "year": _integer(get("year")),
                "announcement_date": announcement,
                "cash_ex_dividend_date": _date_text(get("cash_ex_date")),
                "stock_ex_right_date": _date_text(get("stock_ex_date")),
                "record_date": _date_text(get("record_date")),
                "cash_payment_date": _date_text(get("payment_date")),
                "cash_earnings_distribution": cash_earnings,
                "cash_statutory_surplus": cash_reserve,
                "cash_dividend_per_share": cash_total,
                "stock_earnings_distribution": stock_earnings,
                "stock_statutory_surplus": stock_reserve,
                "stock_dividend_per_share": stock_total,
                "pre_ex_close": None,
                "official_reference_price": None,
                "historical_yield_pct": None,
                "fill": {
                    "status": "unavailable",
                    "first_close_fill_date": None,
                    "close_fill_trading_days": None,
                    "first_intraday_touch_date": None,
                    "reason": "尚未接入官方逐日除權息結果與收盤價驗證",
                },
            }
        )
    items.sort(key=lambda item: item.get("announcement_date") or "", reverse=True)
    return items


def _news_items(articles: list[Article]) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for article in articles:
        key = _event_key(article.title, article.source)
        if not key:
            continue
        published = _iso_datetime(article.published_at)
        candidate = {
            "title": article.title,
            "media_source": article.source or "未標示媒體",
            "kind": "media_report",
            "published_at": published,
            "first_published_at": published,
            "event_date": published[:10] if published else None,
            "original_url": None,
            "aggregator_url": article.url,
            "relevance_reason": "Google News 以股票代碼或公司名稱查得；未另做內容事實判定。",
        }
        existing = events.get(key)
        if not existing:
            events[key] = candidate
            continue
        times = [value for value in (existing["first_published_at"], published) if value]
        existing["first_published_at"] = min(times) if times else None
        existing["event_date"] = (
            existing["first_published_at"][:10] if existing["first_published_at"] else None
        )
        if published and (not existing["published_at"] or published > existing["published_at"]):
            first_published = existing["first_published_at"]
            event_date = existing["event_date"]
            events[key] = {
                **candidate,
                "first_published_at": first_published,
                "event_date": event_date,
            }
    return sorted(events.values(), key=lambda item: item["published_at"] or "", reverse=True)


def _metric_values(
    frame: pd.DataFrame, metrics: dict[str, tuple[str, ...]]
) -> dict[str, dict[str, int | float | None]]:
    if frame.empty:
        return {}
    date_col = _column(frame, "date", "report_date", "period")
    value_col = _column(frame, "value", "amount")
    account_cols = [
        column
        for column in (
            _column(frame, "type", "account", "account_name"),
            _column(frame, "origin_name", "original_name"),
        )
        if column
    ]
    if not date_col or not value_col or not account_cols:
        return {}
    aliases = {
        _normal(alias): (metric, rank)
        for metric, names in metrics.items()
        for rank, alias in enumerate(names)
    }
    selected: dict[tuple[str, str], tuple[int, int | float]] = {}
    for _, row in frame.iterrows():
        period = _date_text(row.get(date_col))
        value = _clean_number(row.get(value_col))
        matches = [aliases.get(_normal(row.get(column))) for column in account_cols]
        matches = [match for match in matches if match]
        if not period or value is None or not matches:
            continue
        metric, rank = min(matches, key=lambda match: match[1])
        key = (period, metric)
        if key not in selected or rank < selected[key][0]:
            selected[key] = (rank, value)
    result: dict[str, dict[str, int | float | None]] = {}
    for (period, metric), (_, value) in selected.items():
        result.setdefault(period, {})[metric] = value
    return result


def _column(frame: pd.DataFrame, *aliases: str) -> str | None:
    columns = {_normal(column): str(column) for column in frame.columns}
    return next((columns[_normal(alias)] for alias in aliases if _normal(alias) in columns), None)


def _normal(value: Any) -> str:
    return "".join(
        character
        for character in unicodedata.normalize(
            "NFKC", "" if value is None else str(value)
        ).casefold()
        if character.isalnum()
    )


def _date_text(value: Any) -> str | None:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date().isoformat()


def _iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _clean_number(value: Any) -> int | float | None:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _integer(value: Any) -> int | None:
    number = _clean_number(value)
    if isinstance(number, int):
        return number
    return int(number) if isinstance(number, float) and number.is_integer() else None


def _complete_sum(left: int | float | None, right: int | float | None) -> int | float | None:
    return _clean_number(left + right) if left is not None and right is not None else None


def _year_quarter(period: str) -> dict[str, int]:
    parsed = date.fromisoformat(period)
    return {"year": parsed.year, "quarter": (parsed.month - 1) // 3 + 1}


def _quarter_serial(period: str) -> int:
    parts = _year_quarter(period)
    return parts["year"] * 4 + parts["quarter"]


def _event_key(title: str, source: str) -> str:
    clean = unicodedata.normalize("NFKC", title).strip()
    if source:
        clean = re.sub(rf"\s*[-–—]\s*{re.escape(source)}\s*$", "", clean, flags=re.I)
    return _normal(clean)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


company_research_provider = CompanyResearchProvider()
