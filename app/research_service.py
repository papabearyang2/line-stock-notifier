"""Persistence and shared queries for the daily Taiwan-stock research view."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable
from datetime import date, datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import (
    ResearchDailyQuote,
    ResearchIndexPoint,
    ResearchInstitutionalFlow,
    ResearchRefreshLog,
    ResearchSecurity,
)
from app.providers.market import Company, market_catalog
from app.providers.research_market import (
    DailyQuote,
    IndexPoint,
    InstitutionalFlow,
    ResearchSourceError,
    official_research_market_provider,
)
from app.research_catalog import formal_theme_codes, stock_profile, stock_tags, theme_definitions
from app.research_metrics import theme_total_return_percent


WINDOWS = {1, 5, 20, 60}
WEIGHTS = {"market_cap", "equal"}
VIEWS = {"industry", "theme"}


async def refresh_research_day(
    requested_date: date | None = None,
    *,
    include_flows: bool = True,
    include_indices: bool = True,
) -> dict[str, Any]:
    """Fetch official daily batches, then atomically replace each successful source slice."""
    settings = get_settings()
    day = requested_date or datetime.now(ZoneInfo(settings.timezone)).date()
    init_db()
    with SessionLocal() as session:
        log = ResearchRefreshLog(scope="daily", status="running", requested_date=day)
        session.add(log)
        session.commit()
        session.refresh(log)
        log_id = log.id

    errors: list[str] = []
    counts = {"quotes": 0, "flows": 0, "indices": 0}
    try:
        companies = await market_catalog.all_companies(official_only=True)
    except Exception as exc:
        companies = {}
        errors.append(f"公司主檔：{type(exc).__name__}")
    if not companies:
        errors.append("公司主檔沒有可用的上市櫃普通股")

    quote_batches: list[tuple[str, list[DailyQuote]]] = []
    for market, fetcher in (
        ("上市", official_research_market_provider.twse_quotes),
        ("上櫃", official_research_market_provider.tpex_quotes),
    ):
        try:
            quote_batches.append((market, await fetcher(day)))
        except ResearchSourceError as exc:
            errors.append(f"{market}行情：{exc}")

    with SessionLocal() as session:
        for market, quotes in quote_batches:
            counts["quotes"] += _replace_quotes(session, day, market, quotes, companies)
        session.commit()

    if include_flows:
        flow_batches: list[tuple[str, list[InstitutionalFlow]]] = []
        for market, fetcher in (
            ("上市", official_research_market_provider.twse_flows),
            ("上櫃", official_research_market_provider.tpex_flows),
        ):
            try:
                flow_batches.append((market, await fetcher(day)))
            except ResearchSourceError as exc:
                errors.append(f"{market}法人：{exc}")
        with SessionLocal() as session:
            for market, flows in flow_batches:
                counts["flows"] += _replace_flows(session, day, market, flows, companies)
            session.commit()

    if include_indices:
        for label, fetcher in (
            ("加權含息指數", official_research_market_provider.taiex_total_return),
            ("加權價格指數", official_research_market_provider.taiex_price),
        ):
            try:
                points = await fetcher(day)
                with SessionLocal() as session:
                    counts["indices"] += _upsert_index_points(session, points)
                    session.commit()
            except ResearchSourceError as exc:
                errors.append(f"{label}：{exc}")

    status = "success" if not errors else ("partial" if counts["quotes"] else "failed")
    completed = _utcnow()
    with SessionLocal() as session:
        log = session.get(ResearchRefreshLog, log_id)
        if log:
            log.status = status
            log.data_date = day if counts["quotes"] else None
            log.row_count = sum(counts.values())
            log.message = "；".join(errors)
            log.completed_at = completed
            session.commit()
    return {"status": status, "data_date": day.isoformat(), **counts, "errors": errors}


async def refresh_research_indices(start: date, end: date) -> dict[str, Any]:
    """Refresh each calendar month once; index endpoints already return monthly batches."""
    init_db()
    cursor = start.replace(day=1)
    months: list[date] = []
    while cursor <= end:
        months.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    # Keep the same newest-session-first policy as the daily backfill script.
    months.sort(reverse=True)
    errors: list[str] = []
    count = 0
    for month in months:
        for label, fetcher in (
            ("加權含息指數", official_research_market_provider.taiex_total_return),
            ("加權價格指數", official_research_market_provider.taiex_price),
        ):
            try:
                points = await fetcher(month)
                with SessionLocal() as session:
                    count += _upsert_index_points(session, points)
                    session.commit()
            except ResearchSourceError as exc:
                errors.append(f"{month:%Y-%m} {label}：{exc}")
    return {
        "status": "success" if not errors else ("partial" if count else "failed"),
        "months": len(months),
        "indices": count,
        "errors": errors,
    }


async def refresh_research_flows(start: date, end: date) -> dict[str, Any]:
    """Repair or backfill institutional share counts without refetching quotes."""
    init_db()
    with SessionLocal() as session:
        days = list(
            session.scalars(
                select(ResearchDailyQuote.trade_date)
                .distinct()
                .where(
                    ResearchDailyQuote.trade_date >= start,
                    ResearchDailyQuote.trade_date <= end,
                )
                .order_by(ResearchDailyQuote.trade_date.desc())
            )
        )
    try:
        companies = await market_catalog.all_companies(official_only=True)
    except Exception as exc:
        return {
            "status": "failed",
            "days": len(days),
            "flows": 0,
            "errors": [f"公司主檔：{type(exc).__name__}"],
        }
    errors: list[str] = []
    count = 0
    for day in days:
        results = await asyncio.gather(
            official_research_market_provider.twse_flows(day),
            official_research_market_provider.tpex_flows(day),
            return_exceptions=True,
        )
        with SessionLocal() as session:
            for market, result in zip(("上市", "上櫃"), results, strict=True):
                if isinstance(result, ResearchSourceError):
                    errors.append(f"{day} {market}法人：{result}")
                    continue
                if isinstance(result, Exception):
                    errors.append(f"{day} {market}法人：{type(result).__name__}")
                    continue
                count += _replace_flows(session, day, market, result, companies)
            session.commit()
    return {
        "status": "success" if not errors else ("partial" if count else "failed"),
        "days": len(days),
        "flows": count,
        "errors": errors,
    }


def overview(
    *,
    window: int = 20,
    weight: Literal["market_cap", "equal"] = "market_cap",
    view: Literal["industry", "theme"] = "industry",
    group: str | None = None,
) -> dict[str, Any]:
    _validate_overview_options(window, weight, view)
    init_db()
    with SessionLocal() as session:
        latest = _latest_complete_quote_date(session)
        if latest is None:
            return _empty_overview(window, weight, view)
        dates = list(
            session.scalars(
                select(ResearchDailyQuote.trade_date)
                .join(ResearchSecurity, ResearchSecurity.code == ResearchDailyQuote.stock_code)
                .where(ResearchDailyQuote.trade_date <= latest)
                .group_by(ResearchDailyQuote.trade_date)
                .having(func.count(func.distinct(ResearchSecurity.market)) == 2)
                .order_by(ResearchDailyQuote.trade_date.desc())
                .limit(window * 2 + 1)
            )
        )
        dates.reverse()
        quote_rows = list(
            session.execute(
                select(ResearchDailyQuote, ResearchSecurity)
                .join(ResearchSecurity, ResearchSecurity.code == ResearchDailyQuote.stock_code)
                .where(
                    ResearchDailyQuote.trade_date.in_(dates),
                    ResearchSecurity.is_active.is_(True),
                )
            ).all()
        )
        flows = list(
            session.scalars(
                select(ResearchInstitutionalFlow).where(
                    ResearchInstitutionalFlow.trade_date.in_(dates)
                )
            )
        )
        benchmark = list(
            session.scalars(
                select(ResearchIndexPoint)
                .where(
                    ResearchIndexPoint.index_code == "TAIEX_TOTAL_RETURN",
                    ResearchIndexPoint.trade_date.in_(dates),
                )
                .order_by(ResearchIndexPoint.trade_date)
            )
        )
        last_log = session.scalar(
            select(ResearchRefreshLog).order_by(ResearchRefreshLog.started_at.desc())
        )

    frame = _quote_frame(quote_rows, dates)
    benchmark_return = _series_return(
        pd.Series(
            {point.trade_date: point.value for point in benchmark},
            dtype="float64",
        ),
        dates[-(window + 1) :],
    )
    flow_lookup = _flow_amounts(flows, frame, dates[-window:])
    stock_frames = {
        str(code): selected.copy()
        for code, selected in frame.groupby("stock_code", sort=False)
    }
    market_turnover = frame.groupby("date")["turnover"].sum(min_count=1)
    group_members = _group_members(frame, view)
    selected_codes = set(group_members.get(group, [])) if group else set(frame["stock_code"])
    groups = [
        _group_summary(key, codes, frame, dates, window, weight, benchmark_return)
        for key, codes in group_members.items()
    ]
    groups.sort(key=lambda item: item.get("turnover") or -1, reverse=True)
    stocks = [
        _stock_summary(
            code,
            frame,
            dates,
            window,
            benchmark_return,
            flow_lookup.get(code),
            selected=stock_frames.get(code),
            market_turnover=market_turnover,
        )
        for code in sorted(selected_codes)
    ]
    stocks.sort(
        key=lambda item: item.get("turnover_share_change_pp")
        if item.get("turnover_share_change_pp") is not None
        else -math.inf,
        reverse=True,
    )

    complete_history = len(dates) >= window * 2 + 1
    return {
        "meta": {
            "as_of": latest.isoformat(),
            "updated_at": _iso(last_log.completed_at if last_log else None),
            "status": (
                "ok"
                if complete_history and benchmark_return is not None
                else "partial"
            ),
            "window": window,
            "weight": weight,
            "view": view,
            "group": group,
            "coverage_note": (
                "目前採當前普通股成分回看；個股遇除權息、減資或參考價跳空日會保留缺值。"
                if complete_history
                else f"目前只有 {len(dates)} 個交易日，尚不足前後各 {window} 日完整比較。"
            ),
            "return_method": (
                "個股以官方收盤價建立報酬；參考價與前收不一致的公司行動日排除。"
                "市場基準使用 TWSE 發行量加權股價報酬指數。"
            ),
            "sources": _overview_sources(),
        },
        "groups": groups,
        "stocks": stocks,
    }


def stock_snapshot(stock_code: str, *, window: int = 20) -> dict[str, Any] | None:
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {sorted(WINDOWS)}")
    payload = overview(window=window, weight="market_cap", view="industry")
    stock = next((item for item in payload["stocks"] if item["code"] == stock_code), None)
    if stock is None:
        return None
    profile = stock_profile(stock_code)
    supply_chain = profile.get("supply_chain") or {
        "status": "unavailable",
        "note": "尚無經來源覆核的供應鏈位置；同產業不等於實際供應商或客戶。",
    }
    product_mix = profile.get("product_mix") or {
        "status": "unavailable",
        "note": "公司未揭露可可靠拆分的產品／應用營收比例。",
    }
    if isinstance(supply_chain, dict) and "sources" not in supply_chain:
        supply_chain = {**supply_chain, "sources": _collect_profile_sources(supply_chain)}
    if isinstance(product_mix, dict) and "sources" not in product_mix:
        product_mix = {**product_mix, "sources": _collect_profile_sources(product_mix)}
    return {
        "meta": payload["meta"],
        "stock": stock,
        "tags": stock_tags(stock_code),
        "sources": [
            *(payload.get("meta", {}).get("sources", []) if isinstance(payload.get("meta"), dict) else []),
            *_collect_profile_sources(profile),
        ],
        "supply_chain": supply_chain,
        "product_mix": product_mix,
    }


def _collect_profile_sources(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the dated evidence links attached to a catalog profile."""
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            url = value.get("url")
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                collected.append(
                    {
                        "name": value.get("name") or "研究分類來源",
                        "url": url,
                        "published_at": value.get("published_at"),
                        "data_period": value.get("data_period"),
                        "last_verified": value.get("last_verified"),
                        "type": value.get("type") or "catalog_evidence",
                        "locator": value.get("locator"),
                    }
                )
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(profile)
    return collected


def latest_refresh_status() -> dict[str, Any]:
    init_db()
    with SessionLocal() as session:
        latest_log = session.scalar(
            select(ResearchRefreshLog).order_by(ResearchRefreshLog.started_at.desc())
        )
        latest_quote = _latest_complete_quote_date(session)
        data_log = None
        if latest_quote is not None:
            data_log = session.scalar(
                select(ResearchRefreshLog)
                .where(
                    ResearchRefreshLog.data_date == latest_quote,
                    ResearchRefreshLog.status.in_(("success", "partial")),
                )
                .order_by(ResearchRefreshLog.started_at.desc())
            )
    log = data_log or latest_log
    if not log and latest_quote is None:
        return {"status": "unavailable", "data_date": None, "message": "尚未執行資料更新"}
    if latest_quote is not None and data_log is None:
        return {
            "status": "stale",
            "data_date": latest_quote.isoformat(),
            "updated_at": _iso(log.completed_at) if log else None,
            "row_count": log.row_count if log else None,
            "message": log.message or "目前顯示最近一次成功資料",
        }
    return {
        "status": log.status,
        "data_date": latest_quote.isoformat() if latest_quote else None,
        "updated_at": _iso(log.completed_at),
        "row_count": log.row_count,
        "message": log.message or None,
    }


def _replace_quotes(
    session: Session,
    day: date,
    market: str,
    quotes: list[DailyQuote],
    companies: dict[str, Company],
) -> int:
    selected = [quote for quote in quotes if quote.code in companies]
    if not selected:
        return 0
    existing = {
        security.code: security
        for security in session.scalars(
            select(ResearchSecurity).where(
                ResearchSecurity.code.in_(quote.code for quote in selected)
            )
        )
    }
    for quote in selected:
        company = companies[quote.code]
        values = {
            "code": company.code,
            "name": company.name,
            "market": company.market,
            "industry": company.industry,
            "business": company.business,
            "is_active": True,
            "source_name": "臺灣證券交易所／證券櫃檯買賣中心",
            "source_url": (
                market_catalog.TWSE_URL if company.market == "上市" else market_catalog.TPEX_URL
            ),
            "updated_at": _utcnow(),
        }
        security = existing.get(company.code)
        if security is None:
            session.add(ResearchSecurity(**values))
        else:
            for field, value in values.items():
                setattr(security, field, value)
    market_codes = select(ResearchSecurity.code).where(ResearchSecurity.market == market)
    session.execute(
        delete(ResearchDailyQuote).where(
            ResearchDailyQuote.trade_date == day,
            ResearchDailyQuote.stock_code.in_(market_codes),
        )
    )
    source_name = "臺灣證券交易所" if market == "上市" else "證券櫃檯買賣中心"
    session.add_all(
        ResearchDailyQuote(
            stock_code=quote.code,
            trade_date=day,
            close=quote.close,
            raw_change=quote.raw_change,
            trade_volume=quote.trade_volume,
            trade_value=quote.trade_value,
            issued_shares=quote.issued_shares,
            market_cap=(
                quote.close * quote.issued_shares
                if quote.close is not None and quote.issued_shares is not None
                else None
            ),
            source_name=source_name,
            source_url=quote.source_url,
        )
        for quote in selected
    )
    return len(selected)


def _replace_flows(
    session: Session,
    day: date,
    market: str,
    flows: list[InstitutionalFlow],
    companies: dict[str, Company],
) -> int:
    selected = [flow for flow in flows if flow.code in companies]
    if not selected:
        return 0
    market_codes = select(ResearchSecurity.code).where(ResearchSecurity.market == market)
    session.execute(
        delete(ResearchInstitutionalFlow).where(
            ResearchInstitutionalFlow.trade_date == day,
            ResearchInstitutionalFlow.stock_code.in_(market_codes),
        )
    )
    source_name = "臺灣證券交易所" if market == "上市" else "證券櫃檯買賣中心"
    session.add_all(
        ResearchInstitutionalFlow(
            stock_code=flow.code,
            trade_date=day,
            foreign_net_shares=flow.foreign_net_shares,
            trust_net_shares=flow.trust_net_shares,
            dealer_net_shares=flow.dealer_net_shares,
            source_name=source_name,
            source_url=flow.source_url,
        )
        for flow in selected
    )
    return len(selected)


def _upsert_index_points(session: Session, points: Iterable[IndexPoint]) -> int:
    count = 0
    for point in points:
        existing = session.scalar(
            select(ResearchIndexPoint).where(
                ResearchIndexPoint.index_code == point.index_code,
                ResearchIndexPoint.trade_date == point.trade_date,
            )
        )
        if existing:
            existing.value = point.value
            existing.source_url = point.source_url
            existing.fetched_at = _utcnow()
        else:
            session.add(
                ResearchIndexPoint(
                    index_code=point.index_code,
                    trade_date=point.trade_date,
                    value=point.value,
                    source_name="臺灣證券交易所",
                    source_url=point.source_url,
                )
            )
        count += 1
    return count


def _quote_frame(rows: list[tuple[ResearchDailyQuote, ResearchSecurity]], dates: list[date]):
    records = [
        {
            "date": quote.trade_date,
            "stock_code": quote.stock_code,
            "name": security.name,
            "market": security.market,
            "industry": security.industry or "未分類",
            "business": security.business,
            "close": quote.close,
            "raw_change": quote.raw_change,
            "turnover": quote.trade_value,
            "volume": quote.trade_volume,
            "market_cap": quote.market_cap,
        }
        for quote, security in rows
    ]
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "stock_code",
                "name",
                "market",
                "industry",
                "close",
                "raw_change",
                "turnover",
                "volume",
                "market_cap",
                "raw_return_decimal",
                "total_return_decimal",
            ]
        )
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["stock_code", "date"])
    previous = frame.groupby("stock_code")["close"].shift(1)
    frame["raw_return_decimal"] = frame["close"] / previous - 1
    reference = frame["close"] - frame["raw_change"]
    normal_reference = (reference - previous).abs() <= 1e-6
    frame["total_return_decimal"] = frame["raw_return_decimal"].where(normal_reference)
    frame["date"] = frame["date"].dt.date
    return frame


def _group_members(frame: pd.DataFrame, view: str) -> dict[str, list[str]]:
    if view == "industry":
        pairs = frame[["stock_code", "industry"]].drop_duplicates()
        return {
            str(industry): list(group["stock_code"].astype(str).drop_duplicates())
            for industry, group in pairs.groupby("industry", sort=True)
        }
    return {
        key: [code for code in formal_theme_codes(key) if code in set(frame["stock_code"])]
        for key in theme_definitions()
    }


def _group_summary(
    key: str,
    codes: list[str],
    frame: pd.DataFrame,
    dates: list[date],
    window: int,
    weight: str,
    benchmark_return: float | None,
) -> dict[str, Any]:
    selected = frame[frame["stock_code"].isin(codes)].copy()
    current_dates = dates[-window:]
    turnover, share, change = _turnover_values(selected, frame, dates, window)
    current = selected[selected["date"].isin(dates[-(window + 1) :])]
    total_return = _theme_return(current, window, weight, "total_return_decimal")
    raw_return = _theme_return(current, window, weight, "raw_return_decimal")
    expected = max(len(codes) * window, 1)
    usable = current[current["date"].isin(current_dates)]["total_return_decimal"].notna().sum()
    definition = theme_definitions().get(key, {}) if key in theme_definitions() else {}
    return {
        "key": key,
        "name": definition.get("name", key),
        "member_count": len(codes),
        "return_pct": _finite(total_return),
        "raw_return_pct": _finite(raw_return),
        "relative_strength_pp": _difference(total_return, benchmark_return),
        "turnover": _finite(turnover),
        "turnover_share_pct": _finite(share),
        "turnover_share_change_pp": _finite(change),
        "return_coverage_pct": float(round(usable / expected * 100, 1)),
    }


def _stock_summary(
    code: str,
    frame: pd.DataFrame,
    dates: list[date],
    window: int,
    benchmark_return: float | None,
    flow_amount: dict[str, float | None] | None,
    *,
    selected: pd.DataFrame | None = None,
    market_turnover: pd.Series | None = None,
) -> dict[str, Any]:
    selected = (selected if selected is not None else frame[frame["stock_code"] == code]).sort_values("date")
    if selected.empty:
        return {"code": code}
    latest = selected.iloc[-1]
    current = selected[selected["date"].isin(dates[-(window + 1) :])]
    total_return = _chain_stock_return(current, dates[-window:], "total_return_decimal")
    raw_return = _series_return(
        pd.Series(dict(zip(current["date"], current["close"], strict=False))),
        dates[-(window + 1) :],
    )
    turnover, share, change = _turnover_values(
        selected, frame, dates, window, market_turnover=market_turnover
    )
    tags = [str(tag.get("label")) for tag in stock_tags(code) if tag.get("label")]
    return {
        "code": code,
        "name": latest["name"],
        "market": latest["market"],
        "industry": latest["industry"],
        "business_summary": latest.get("business"),
        "tags": list(dict.fromkeys(tags)),
        "close": _finite(latest["close"]),
        "return_pct": _finite(total_return),
        "raw_return_pct": _finite(raw_return),
        "relative_strength_pp": _difference(total_return, benchmark_return),
        "turnover": _finite(turnover),
        "turnover_share_pct": _finite(share),
        "turnover_share_change_pp": _finite(change),
        "institutional_estimated_amount": (
            _finite(flow_amount.get("total")) if flow_amount else None
        ),
        "institutional_estimated_breakdown": flow_amount,
    }


def _turnover_values(
    selected: pd.DataFrame,
    market: pd.DataFrame,
    dates: list[date],
    window: int,
    *,
    market_turnover: pd.Series | None = None,
) -> tuple[float | None, float | None, float | None]:
    if len(dates) < window * 2:
        return None, None, None
    current_dates = dates[-window:]
    previous_dates = dates[-window * 2 : -window]
    current_stock = selected[selected["date"].isin(current_dates)]["turnover"]
    if market_turnover is None:
        market_turnover = market.groupby("date")["turnover"].sum(min_count=1)
    current_market = market_turnover.reindex(current_dates)
    previous_stock = selected[selected["date"].isin(previous_dates)]["turnover"]
    previous_market = market_turnover.reindex(previous_dates)
    if (
        len(current_market) != window
        or len(previous_market) != window
        or current_stock.isna().any()
        or previous_stock.isna().any()
        or current_market.isna().any()
        or previous_market.isna().any()
        or current_market.sum() <= 0
        or previous_market.sum() <= 0
    ):
        return None, None, None
    turnover = float(current_stock.sum())
    current_share = turnover / float(current_market.sum()) * 100
    previous_share = float(previous_stock.sum()) / float(previous_market.sum()) * 100
    return turnover, current_share, current_share - previous_share


def _theme_return(frame: pd.DataFrame, window: int, weight: str, column: str) -> float | None:
    if frame.empty:
        return None
    value = theme_total_return_percent(
        frame[["date", "stock_code", column, "market_cap"]].rename(
            columns={column: "total_return_decimal"}
        ),
        window,
        weighting=weight,  # type: ignore[arg-type]
    )
    return _finite(value)


def _chain_stock_return(frame: pd.DataFrame, dates: list[date], column: str) -> float | None:
    values = frame.set_index("date")[column].reindex(dates)
    if len(values) != len(dates) or values.isna().any():
        return None
    return float(((1 + values).prod() - 1) * 100)


def _series_return(series: pd.Series, dates: list[date]) -> float | None:
    if len(dates) < 2:
        return None
    values = pd.to_numeric(series.reindex(dates), errors="coerce")
    if values.isna().any() or values.iloc[0] <= 0:
        return None
    return float((values.iloc[-1] / values.iloc[0] - 1) * 100)


def _flow_amounts(
    flows: list[ResearchInstitutionalFlow],
    quotes: pd.DataFrame,
    dates: list[date],
) -> dict[str, dict[str, float | None]]:
    quote_lookup = {
        (str(row.stock_code), row.date): (
            float(row.turnover) / float(row.volume)
            if pd.notna(row.turnover) and pd.notna(row.volume) and row.volume > 0
            else None
        )
        for row in quotes.itertuples()
        if row.date in dates
    }
    buckets: dict[str, dict[str, list[float] | bool]] = {}
    for flow in flows:
        if flow.trade_date not in dates:
            continue
        average_price = quote_lookup.get((flow.stock_code, flow.trade_date))
        bucket = buckets.setdefault(
            flow.stock_code,
            {"foreign": [], "trust": [], "dealer": [], "has_missing": False},
        )
        if average_price is None:
            bucket["has_missing"] = True
            continue
        for key, shares in (
            ("foreign", flow.foreign_net_shares),
            ("trust", flow.trust_net_shares),
            ("dealer", flow.dealer_net_shares),
        ):
            if shares is None:
                bucket["has_missing"] = True
            else:
                values = bucket[key]
                assert isinstance(values, list)
                values.append(float(shares) * average_price)
    result: dict[str, dict[str, float | None]] = {}
    for code, bucket in buckets.items():
        breakdown: dict[str, float | None] = {}
        for key in ("foreign", "trust", "dealer"):
            values = bucket[key]
            assert isinstance(values, list)
            breakdown[key] = sum(values) if values else None
        parts = [value for value in breakdown.values() if value is not None]
        breakdown["total"] = sum(parts) if len(parts) == 3 else None
        result[code] = breakdown
    return result


def _validate_overview_options(window: int, weight: str, view: str) -> None:
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {sorted(WINDOWS)}")
    if weight not in WEIGHTS:
        raise ValueError(f"weight must be one of {sorted(WEIGHTS)}")
    if view not in VIEWS:
        raise ValueError(f"view must be one of {sorted(VIEWS)}")


def _empty_overview(window: int, weight: str, view: str) -> dict[str, Any]:
    return {
        "meta": {
            "as_of": None,
            "updated_at": None,
            "status": "unavailable",
            "window": window,
            "weight": weight,
            "view": view,
            "coverage_note": "尚未匯入官方盤後資料；缺值不會以 0 代替。",
            "return_method": "尚未取得",
            "sources": _overview_sources(),
        },
        "groups": [],
        "stocks": [],
    }


def _overview_sources() -> list[dict[str, str]]:
    return [
        {
            "name": "臺灣證券交易所－每日收盤行情",
            "url": official_research_market_provider.TWSE_QUOTE_URL,
        },
        {
            "name": "證券櫃檯買賣中心－上櫃股票行情",
            "url": official_research_market_provider.TPEX_QUOTE_URL,
        },
        {
            "name": "臺灣證券交易所－發行量加權股價報酬指數",
            "url": official_research_market_provider.TAIEX_TOTAL_RETURN_URL,
        },
        {
            "name": "TWSE／TPEx－三大法人買賣超股數",
            "url": official_research_market_provider.TWSE_FLOW_URL,
        },
    ]


def _latest_complete_quote_date(session: Session) -> date | None:
    return session.scalar(
        select(ResearchDailyQuote.trade_date)
        .join(ResearchSecurity, ResearchSecurity.code == ResearchDailyQuote.stock_code)
        .group_by(ResearchDailyQuote.trade_date)
        .having(func.count(func.distinct(ResearchSecurity.market)) == 2)
        .order_by(ResearchDailyQuote.trade_date.desc())
        .limit(1)
    )


def _difference(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
