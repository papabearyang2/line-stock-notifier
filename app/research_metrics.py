from collections.abc import Iterable
from typing import Literal

import pandas as pd


WINDOWS = frozenset({1, 5, 20, 60})
RETURN_VALUE_COLUMNS = frozenset({"total_return_index", "adjusted_price"})


def total_return_percent(
    frame: pd.DataFrame,
    window: int,
    *,
    value_column: str = "total_return_index",
    date_column: str = "date",
) -> float:
    """Return the last ``window`` trading-day total return, in percent."""
    _validate_window(window)
    _validate_return_column(value_column)
    values = _dated_series(frame, date_column, value_column).tail(window + 1)
    return _return_percent(values, window)


def relative_strength_pp(
    stock: pd.DataFrame,
    market: pd.DataFrame,
    window: int,
    *,
    stock_value_column: str = "total_return_index",
    market_value_column: str = "total_return_index",
    date_column: str = "date",
) -> float:
    """Return stock total return minus market total return, in percentage points."""
    _validate_window(window)
    _validate_return_column(stock_value_column)
    _validate_return_column(market_value_column)
    stock_values = _dated_series(stock, date_column, stock_value_column).rename("stock")
    market_values = _dated_series(market, date_column, market_value_column).rename("market")
    aligned = pd.concat([stock_values, market_values], axis=1).sort_index().tail(window + 1)
    stock_return = _return_percent(aligned["stock"], window)
    market_return = _return_percent(aligned["market"], window)
    return stock_return - market_return


def turnover_focus(
    frame: pd.DataFrame,
    window: int,
    *,
    stock_turnover_column: str = "stock_turnover",
    market_turnover_column: str = "market_turnover",
    date_column: str = "date",
) -> dict[str, float]:
    """Compare stock turnover share with the immediately preceding equal window.

    ``market_turnover`` must be the fixed whole-market denominator before any UI
    filters. Missing values invalidate the affected period rather than becoming zero.
    """
    _validate_window(window)
    ordered = _dated_frame(
        frame,
        date_column,
        [stock_turnover_column, market_turnover_column],
    ).tail(window * 2)
    current = ordered.tail(window)
    previous = ordered.iloc[-window * 2 : -window]
    current_share = _turnover_share(current, stock_turnover_column, market_turnover_column, window)
    previous_share = _turnover_share(
        previous,
        stock_turnover_column,
        market_turnover_column,
        window,
    )
    return {
        "turnover_share_percent": current_share,
        "previous_turnover_share_percent": previous_share,
        "turnover_share_change_pp": current_share - previous_share,
    }


def theme_daily_returns(
    frame: pd.DataFrame,
    *,
    weighting: Literal["market_cap", "equal"] = "market_cap",
    date_column: str = "date",
    stock_column: str = "stock_code",
    return_column: str = "total_return_decimal",
    market_cap_column: str = "market_cap",
) -> pd.Series:
    """Aggregate constituent daily total returns without using future weights.

    Market-cap weighting uses each constituent's cap on the immediately previous
    trading date. A constituent with a missing return or required cap is excluded
    for that day and the remaining weights are re-normalized. An empty daily
    cross-section remains missing.
    """
    if weighting not in {"market_cap", "equal"}:
        raise ValueError("weighting must be 'market_cap' or 'equal'")
    columns = [stock_column, return_column]
    if weighting == "market_cap":
        columns.append(market_cap_column)
    ordered = _dated_frame(frame, date_column, columns)
    if ordered.duplicated([date_column, stock_column]).any():
        raise ValueError("each date and stock_code pair must be unique")

    ordered[stock_column] = ordered[stock_column].astype(str)
    ordered[return_column] = pd.to_numeric(ordered[return_column], errors="coerce")
    if (ordered[return_column].dropna() < -1).any():
        raise ValueError("total_return_decimal cannot be below -1")
    dates = pd.Index(ordered[date_column].drop_duplicates().sort_values(), name=date_column)

    if weighting == "equal":
        result = ordered.groupby(date_column, sort=True)[return_column].mean().reindex(dates)
    else:
        ordered[market_cap_column] = pd.to_numeric(ordered[market_cap_column], errors="coerce")
        caps = ordered.pivot(index=date_column, columns=stock_column, values=market_cap_column)
        previous_caps = (
            caps.reindex(dates)
            .shift(1)
            .rename_axis(index=date_column, columns=stock_column)
            .reset_index()
            .melt(
                id_vars=date_column,
                var_name=stock_column,
                value_name="_previous_market_cap",
            )
        )
        weighted = ordered.merge(
            previous_caps,
            on=[date_column, stock_column],
            how="left",
            validate="one_to_one",
        )
        eligible = weighted[
            weighted[return_column].notna()
            & weighted["_previous_market_cap"].notna()
            & (weighted["_previous_market_cap"] > 0)
        ].copy()
        numerator = (
            eligible[return_column] * eligible["_previous_market_cap"]
        ).groupby(eligible[date_column]).sum(min_count=1)
        denominator = eligible.groupby(date_column)["_previous_market_cap"].sum(min_count=1)
        result = (numerator / denominator).reindex(dates)

    result.name = f"{weighting}_daily_total_return_decimal"
    return result


def theme_total_return_percent(
    frame: pd.DataFrame,
    window: int,
    *,
    weighting: Literal["market_cap", "equal"] = "market_cap",
    date_column: str = "date",
    stock_column: str = "stock_code",
    return_column: str = "total_return_decimal",
    market_cap_column: str = "market_cap",
) -> float:
    """Chain-link the last ``window`` theme daily returns, in percent."""
    _validate_window(window)
    daily = theme_daily_returns(
        frame,
        weighting=weighting,
        date_column=date_column,
        stock_column=stock_column,
        return_column=return_column,
        market_cap_column=market_cap_column,
    ).tail(window)
    if len(daily) != window or daily.isna().any():
        return float("nan")
    return float(((1 + daily).prod() - 1) * 100)


def unique_stock_codes_for_tags(
    frame: pd.DataFrame,
    tags: Iterable[str],
    *,
    require_all: bool = False,
    stock_column: str = "stock_code",
    tag_column: str = "tag",
) -> list[str]:
    """Return stable, de-duplicated stock codes matching any or all selected tags."""
    _require_columns(frame, [stock_column, tag_column])
    wanted = {str(tag) for tag in tags}
    codes = frame[stock_column].dropna().astype(str)
    if not wanted:
        return list(dict.fromkeys(codes))

    matched = frame[frame[tag_column].astype(str).isin(wanted)]
    if require_all:
        matched_counts = matched.groupby(stock_column)[tag_column].nunique()
        eligible = set(matched_counts[matched_counts == len(wanted)].index.astype(str))
        codes = codes[codes.isin(eligible)]
    else:
        codes = matched[stock_column].dropna().astype(str)
    return list(dict.fromkeys(codes))


def _validate_window(window: int) -> None:
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {sorted(WINDOWS)}")


def _validate_return_column(value_column: str) -> None:
    if value_column not in RETURN_VALUE_COLUMNS:
        raise ValueError("value_column must be 'total_return_index' or 'adjusted_price'")


def _require_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = set(columns).difference(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {', '.join(sorted(missing))}")


def _dated_frame(frame: pd.DataFrame, date_column: str, columns: list[str]) -> pd.DataFrame:
    _require_columns(frame, [date_column, *columns])
    result = frame[[date_column, *columns]].copy()
    result[date_column] = pd.to_datetime(result[date_column], errors="coerce")
    if result[date_column].isna().any():
        raise ValueError(f"{date_column} contains an invalid date")
    return result.sort_values(date_column).reset_index(drop=True)


def _dated_series(frame: pd.DataFrame, date_column: str, value_column: str) -> pd.Series:
    result = _dated_frame(frame, date_column, [value_column])
    if result[date_column].duplicated().any():
        raise ValueError(f"{date_column} must be unique")
    return pd.Series(
        pd.to_numeric(result[value_column], errors="coerce").to_numpy(),
        index=pd.Index(result[date_column], name=date_column),
        name=value_column,
    )


def _return_percent(values: pd.Series, window: int) -> float:
    if len(values) != window + 1 or values.isna().any() or values.iloc[0] <= 0:
        return float("nan")
    return float((values.iloc[-1] / values.iloc[0] - 1) * 100)


def _turnover_share(
    period: pd.DataFrame,
    stock_column: str,
    market_column: str,
    window: int,
) -> float:
    if len(period) != window:
        return float("nan")
    stock = pd.to_numeric(period[stock_column], errors="coerce")
    market = pd.to_numeric(period[market_column], errors="coerce")
    if stock.isna().any() or market.isna().any() or (stock < 0).any() or (market <= 0).any():
        return float("nan")
    return float(stock.sum() / market.sum() * 100)
