from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    ResearchDailyQuote,
    ResearchIndexPoint,
    ResearchInstitutionalFlow,
    ResearchRefreshLog,
    ResearchSecurity,
)
from app import research_service
from app.providers.research_market import DailyQuote
from app.providers.market import Company


def _temporary_database(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(research_service, "SessionLocal", sessions)
    monkeypatch.setattr(research_service, "init_db", lambda: None)
    return sessions


def test_overview_uses_fixed_market_denominator_and_estimates_institutional_cash(monkeypatch):
    sessions = _temporary_database(monkeypatch)
    dates = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31)]
    with sessions() as session:
        session.add_all(
            [
                ResearchSecurity(
                    code="1111",
                    name="甲公司",
                    market="上市",
                    industry="測試業",
                    source_name="TWSE",
                    source_url="https://example.test/securities",
                ),
                ResearchSecurity(
                    code="2222",
                    name="乙公司",
                    market="上櫃",
                    industry="其他業",
                    source_name="TPEx",
                    source_url="https://example.test/securities",
                ),
            ]
        )
        closes = {"1111": [100, 110, 121], "2222": [100, 100, 100]}
        values = {"1111": [10_000, 20_000, 40_000], "2222": [90_000, 80_000, 60_000]}
        for code in closes:
            for index, day in enumerate(dates):
                previous = closes[code][index - 1] if index else closes[code][index]
                session.add(
                    ResearchDailyQuote(
                        stock_code=code,
                        trade_date=day,
                        close=closes[code][index],
                        raw_change=closes[code][index] - previous,
                        trade_volume=1_000,
                        trade_value=values[code][index],
                        issued_shares=1_000,
                        market_cap=closes[code][index] * 1_000,
                        source_name="exchange",
                        source_url="https://example.test/quote",
                    )
                )
        session.add_all(
            ResearchIndexPoint(
                index_code="TAIEX_TOTAL_RETURN",
                trade_date=day,
                value=value,
                source_name="TWSE",
                source_url="https://example.test/index",
            )
            for day, value in zip(dates, [100, 102, 104], strict=True)
        )
        session.add(
            ResearchInstitutionalFlow(
                stock_code="1111",
                trade_date=dates[-1],
                foreign_net_shares=2,
                trust_net_shares=1,
                dealer_net_shares=-1,
                source_name="TWSE",
                source_url="https://example.test/flow",
            )
        )
        session.commit()

    payload = research_service.overview(window=1)
    stock = next(row for row in payload["stocks"] if row["code"] == "1111")

    assert stock["return_pct"] == pytest.approx(10)
    assert stock["relative_strength_pp"] == pytest.approx(10 - (104 / 102 - 1) * 100)
    assert stock["turnover_share_pct"] == pytest.approx(40)
    assert stock["turnover_share_change_pp"] == pytest.approx(20)
    assert stock["institutional_estimated_breakdown"] == pytest.approx(
        {"foreign": 80, "trust": 40, "dealer": -40, "total": 80}
    )


def test_replace_quotes_loads_existing_securities_once(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    companies = {
        code: Company(code, f"公司{code}", market="上市")
        for code in ("1111", "2222", "3333")
    }
    quotes = [
        DailyQuote(
            code,
            f"公司{code}",
            "上市",
            100.0,
            1.0,
            1_000,
            100_000,
            10_000,
            "https://example.test/quotes",
        )
        for code in companies
    ]
    security_selects = []

    @event.listens_for(engine, "before_cursor_execute")
    def count_security_selects(_conn, _cursor, statement, _params, _context, _executemany):
        if "research_securities" in statement.lower() and statement.lstrip().lower().startswith("select"):
            security_selects.append(statement)

    with sessions() as session:
        session.add(
            ResearchSecurity(
                code="1111",
                name="舊公司名稱",
                market="上市",
                source_name="old",
                source_url="https://example.test/old",
            )
        )
        session.commit()
        count = research_service._replace_quotes(
            session, date(2026, 9, 2), "上市", quotes, companies
        )
        session.commit()

    assert count == len(quotes)
    assert len(security_selects) == 1


def test_corporate_action_reference_gap_stays_unavailable(monkeypatch):
    sessions = _temporary_database(monkeypatch)
    dates = [date(2026, 8, 28), date(2026, 8, 31)]
    with sessions() as session:
        session.add(
            ResearchSecurity(
                code="1111",
                name="甲公司",
                market="上市",
                industry="測試業",
                source_name="TWSE",
                source_url="https://example.test/securities",
            )
        )
        session.add(
            ResearchSecurity(
                code="2222",
                name="乙公司",
                market="上櫃",
                industry="其他業",
                source_name="TPEx",
                source_url="https://example.test/securities",
            )
        )
        session.add_all(
            [
                ResearchDailyQuote(
                    stock_code="1111",
                    trade_date=dates[0],
                    close=100,
                    raw_change=0,
                    trade_volume=100,
                    trade_value=10_000,
                    market_cap=100_000,
                    source_name="TWSE",
                    source_url="https://example.test/quote",
                ),
                ResearchDailyQuote(
                    stock_code="1111",
                    trade_date=dates[1],
                    close=96,
                    raw_change=1,
                    trade_volume=100,
                    trade_value=9_600,
                    market_cap=96_000,
                    source_name="TWSE",
                    source_url="https://example.test/quote",
                ),
            ]
        )
        session.add_all(
            ResearchDailyQuote(
                stock_code="2222",
                trade_date=day,
                close=50,
                raw_change=0,
                trade_volume=100,
                trade_value=5_000,
                market_cap=50_000,
                source_name="TPEx",
                source_url="https://example.test/quote",
            )
            for day in dates
        )
        session.add_all(
            ResearchIndexPoint(
                index_code="TAIEX_TOTAL_RETURN",
                trade_date=day,
                value=value,
                source_name="TWSE",
                source_url="https://example.test/index",
            )
            for day, value in zip(dates, [100, 101], strict=True)
        )
        session.commit()

    stock = next(
        item for item in research_service.overview(window=1)["stocks"] if item["code"] == "1111"
    )

    assert stock["raw_return_pct"] == pytest.approx(-4)
    assert stock["return_pct"] is None
    assert stock["relative_strength_pp"] is None


def test_empty_database_reports_unavailable_without_fake_zero(monkeypatch):
    _temporary_database(monkeypatch)

    payload = research_service.overview(window=20)

    assert payload["meta"]["status"] == "unavailable"
    assert payload["meta"]["as_of"] is None
    assert payload["groups"] == []
    assert payload["stocks"] == []


def test_latest_refresh_status_uses_latest_available_data_after_later_failed_attempt(monkeypatch):
    sessions = _temporary_database(monkeypatch)
    data_day = date(2026, 9, 2)
    failed_day = date(2026, 7, 10)
    with sessions() as session:
        session.add_all(
            [
                ResearchSecurity(
                    code="1111",
                    name="甲公司",
                    market="上市",
                    industry="測試業",
                    source_name="exchange",
                    source_url="https://example.test/security",
                ),
                ResearchSecurity(
                    code="2222",
                    name="乙公司",
                    market="上櫃",
                    industry="其他業",
                    source_name="exchange",
                    source_url="https://example.test/security",
                ),
            ]
        )
        session.add_all(
            ResearchDailyQuote(
                stock_code=code,
                trade_date=data_day,
                close=100,
                raw_change=0,
                trade_volume=100,
                trade_value=10_000,
                market_cap=100_000,
                source_name="exchange",
                source_url="https://example.test/quote",
            )
            for code in ("1111", "2222")
        )
        session.add_all(
            [
                ResearchRefreshLog(
                    scope="daily",
                    status="success",
                    requested_date=data_day,
                    data_date=data_day,
                    row_count=3_833,
                    started_at=datetime(2026, 9, 2, 17, 0),
                    completed_at=datetime(2026, 9, 2, 17, 20),
                ),
                ResearchRefreshLog(
                    scope="daily",
                    status="failed",
                    requested_date=failed_day,
                    row_count=44,
                    message="上市行情：官方資料來源暫時無法使用",
                    started_at=datetime(2026, 9, 3, 0, 30),
                    completed_at=datetime(2026, 9, 3, 0, 31),
                ),
            ]
        )
        session.commit()

    payload = research_service.latest_refresh_status()

    assert payload["status"] == "success"
    assert payload["data_date"] == data_day.isoformat()
    assert payload["row_count"] == 3_833
    assert payload["message"] is None


def test_overview_excludes_historical_partial_market_days(monkeypatch):
    sessions = _temporary_database(monkeypatch)
    dates = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31)]
    with sessions() as session:
        session.add_all(
            [
                ResearchSecurity(
                    code="1111",
                    name="甲公司",
                    market="上市",
                    industry="測試業",
                    source_name="exchange",
                    source_url="https://example.test/security",
                ),
                ResearchSecurity(
                    code="2222",
                    name="乙公司",
                    market="上櫃",
                    industry="其他業",
                    source_name="exchange",
                    source_url="https://example.test/security",
                ),
            ]
        )
        for code, market, closes in (
            ("1111", "上市", [100, 500, 110]),
            ("2222", "上櫃", [50, None, 50]),
        ):
            for day, close in zip(dates, closes, strict=True):
                if close is None:
                    continue
                session.add(
                    ResearchDailyQuote(
                        stock_code=code,
                        trade_date=day,
                        close=close,
                        raw_change=0,
                        trade_volume=100,
                        trade_value=close * 100,
                        market_cap=close * 1_000,
                        source_name=market,
                        source_url="https://example.test/quote",
                    )
                )
        session.add_all(
            ResearchIndexPoint(
                index_code="TAIEX_TOTAL_RETURN",
                trade_date=day,
                value=value,
                source_name="TWSE",
                source_url="https://example.test/index",
            )
            for day, value in zip(dates, [100, 101, 102], strict=True)
        )
        session.commit()

    stock = next(item for item in research_service.overview(window=1)["stocks"] if item["code"] == "1111")
    assert stock["raw_return_pct"] == pytest.approx(10)
