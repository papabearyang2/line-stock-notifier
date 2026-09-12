import math

import pandas as pd
import pytest

from app.research_metrics import (
    relative_strength_pp,
    theme_daily_returns,
    theme_total_return_percent,
    total_return_percent,
    turnover_focus,
    unique_stock_codes_for_tags,
)


def test_total_return_requires_a_total_return_input_and_reports_percent():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=6),
            "total_return_index": [100, 101, 102, 103, 104, 110],
            "adjusted_price": [50, 50.5, 51, 51.5, 52, 55],
            "close": [50, 50.5, 51, 51.5, 52, 55],
        }
    )

    assert total_return_percent(frame, 5) == pytest.approx(10)
    assert total_return_percent(frame, 5, value_column="adjusted_price") == pytest.approx(10)
    with pytest.raises(ValueError, match="total_return_index.*adjusted_price"):
        total_return_percent(frame, 5, value_column="close")


def test_relative_strength_is_percentage_point_difference_and_missing_propagates():
    dates = pd.date_range("2026-01-01", periods=2)
    stock = pd.DataFrame({"date": dates, "total_return_index": [100, 110]})
    market = pd.DataFrame({"date": dates, "total_return_index": [100, 104]})

    assert relative_strength_pp(stock, market, 1) == pytest.approx(6)

    stock.loc[1, "total_return_index"] = None
    assert math.isnan(relative_strength_pp(stock, market, 1))


def test_turnover_focus_uses_period_sums_and_never_converts_missing_to_zero():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=10),
            "stock_turnover": [10] * 5 + [30] * 5,
            "market_turnover": [100] * 10,
        }
    )
    result = turnover_focus(frame, 5)

    assert result == pytest.approx(
        {
            "turnover_share_percent": 30,
            "previous_turnover_share_percent": 10,
            "turnover_share_change_pp": 20,
        }
    )

    frame.loc[9, "stock_turnover"] = None
    result = turnover_focus(frame, 5)
    assert math.isnan(result["turnover_share_percent"])
    assert math.isnan(result["turnover_share_change_pp"])
    assert result["previous_turnover_share_percent"] == pytest.approx(10)


def test_market_cap_weighting_uses_previous_trading_day_cap_without_lookahead():
    frame = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"],
            "stock_code": ["A", "B", "A", "B"],
            "total_return_decimal": [0, 0, 0.10, 0],
            "market_cap": [90, 10, 1, 99],
        }
    )

    cap_weighted = theme_daily_returns(frame, weighting="market_cap")
    equal_weighted = theme_daily_returns(frame, weighting="equal")

    assert math.isnan(cap_weighted.loc[pd.Timestamp("2026-01-01")])
    assert cap_weighted.loc[pd.Timestamp("2026-01-02")] == pytest.approx(0.09)
    assert equal_weighted.loc[pd.Timestamp("2026-01-02")] == pytest.approx(0.05)


def test_theme_missing_constituents_are_excluded_not_zero_filled():
    frame = pd.DataFrame(
        {
            "date": [
                "2026-01-01",
                "2026-01-01",
                "2026-01-02",
                "2026-01-02",
                "2026-01-03",
                "2026-01-03",
            ],
            "stock_code": ["A", "B"] * 3,
            "total_return_decimal": [0, 0, None, 0.02, None, None],
            "market_cap": [90, 10, 90, 10, 90, 10],
        }
    )

    daily = theme_daily_returns(frame, weighting="market_cap")

    assert daily.loc[pd.Timestamp("2026-01-02")] == pytest.approx(0.02)
    assert math.isnan(daily.loc[pd.Timestamp("2026-01-03")])
    assert math.isnan(theme_total_return_percent(frame, 1, weighting="market_cap"))


def test_theme_return_chain_links_daily_returns():
    dates = pd.date_range("2026-01-01", periods=5)
    frame = pd.DataFrame(
        {
            "date": dates,
            "stock_code": ["A"] * 5,
            "total_return_decimal": [0.01] * 5,
        }
    )

    assert theme_total_return_percent(frame, 5, weighting="equal") == pytest.approx(
        ((1.01**5) - 1) * 100
    )


def test_multi_tag_filter_returns_each_stock_once():
    tags = pd.DataFrame(
        {
            "stock_code": ["6274", "6274", "2383", "6213", "6213"],
            "tag": ["CCL", "AI Server", "CCL", "CCL", "Switch"],
        }
    )

    assert unique_stock_codes_for_tags(tags, ["CCL", "Switch"]) == ["6274", "2383", "6213"]
    assert unique_stock_codes_for_tags(tags, ["CCL", "Switch"], require_all=True) == ["6213"]
