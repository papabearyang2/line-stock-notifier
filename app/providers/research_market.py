"""Official end-of-day batch sources used by the research dashboard."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx


class ResearchSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class DailyQuote:
    code: str
    name: str
    market: str
    close: float | None
    raw_change: float | None
    trade_volume: int | None
    trade_value: int | None
    issued_shares: int | None
    source_url: str


@dataclass(frozen=True)
class InstitutionalFlow:
    code: str
    foreign_net_shares: int | None
    trust_net_shares: int | None
    dealer_net_shares: int | None
    source_url: str


@dataclass(frozen=True)
class IndexPoint:
    trade_date: date
    value: float
    index_code: str
    source_url: str


class OfficialResearchMarketProvider:
    TWSE_QUOTE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
    TPEX_QUOTE_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
    TWSE_CAPITAL_URL = "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
    TWSE_FLOW_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
    TPEX_FLOW_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
    TAIEX_TOTAL_RETURN_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MFI94U"
    TAIEX_PRICE_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST"

    async def twse_quotes(self, day: date) -> list[DailyQuote]:
        payload, source_url = await self._get_json(
            self.TWSE_QUOTE_URL,
            {"date": day.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
        )
        fields, rows = _find_table(payload, "證券代號", "成交金額", "收盤價")
        capitals = await self.twse_issued_shares(day)
        result: list[DailyQuote] = []
        for row in _records(fields, rows):
            code = _text(row.get("證券代號"))
            if not code:
                continue
            change = _number(row.get("漲跌價差"))
            sign = _text(row.get("漲跌(+/-)"))
            if change is not None and ("green" in sign.casefold() or sign.strip() == "-"):
                change = -abs(change)
            result.append(
                DailyQuote(
                    code=code,
                    name=_text(row.get("證券名稱")),
                    market="上市",
                    close=_number(row.get("收盤價")),
                    raw_change=change,
                    trade_volume=_integer(row.get("成交股數")),
                    trade_value=_integer(row.get("成交金額")),
                    issued_shares=capitals.get(code),
                    source_url=source_url,
                )
            )
        return result

    async def tpex_quotes(self, day: date) -> list[DailyQuote]:
        payload, source_url = await self._get_json(
            self.TPEX_QUOTE_URL,
            {"date": day.strftime("%Y/%m/%d"), "id": "", "response": "json"},
        )
        fields, rows = _find_table(payload, "代號", "成交金額(元)", "收盤")
        result: list[DailyQuote] = []
        for row in _records(fields, rows):
            code = _text(row.get("代號"))
            if not code:
                continue
            result.append(
                DailyQuote(
                    code=code,
                    name=_text(row.get("名稱")),
                    market="上櫃",
                    close=_number(row.get("收盤")),
                    raw_change=_number(row.get("漲跌")),
                    trade_volume=_integer(row.get("成交股數")),
                    trade_value=_integer(row.get("成交金額(元)")),
                    issued_shares=_integer(row.get("發行股數")),
                    source_url=source_url,
                )
            )
        return result

    async def twse_issued_shares(self, day: date) -> dict[str, int]:
        payload, _ = await self._get_json(
            self.TWSE_CAPITAL_URL,
            {"date": day.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"},
        )
        fields, rows = _find_table(payload, "證券代號", "發行股數")
        return {
            code: shares
            for row in _records(fields, rows)
            if (code := _text(row.get("證券代號")))
            and (shares := _integer(row.get("發行股數"))) is not None
        }

    async def twse_flows(self, day: date) -> list[InstitutionalFlow]:
        payload, source_url = await self._get_json(
            self.TWSE_FLOW_URL,
            {"date": day.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"},
        )
        fields, rows = _find_table(payload, "證券代號", "投信買賣超股數")
        result: list[InstitutionalFlow] = []
        for row in _records(fields, rows):
            code = _text(row.get("證券代號"))
            if not code:
                continue
            result.append(
                InstitutionalFlow(
                    code=code,
                    foreign_net_shares=_integer(
                        row.get("外陸資買賣超股數(不含外資自營商)")
                    ),
                    trust_net_shares=_integer(row.get("投信買賣超股數")),
                    dealer_net_shares=_integer(row.get("自營商買賣超股數")),
                    source_url=source_url,
                )
            )
        return result

    async def tpex_flows(self, day: date) -> list[InstitutionalFlow]:
        payload, source_url = await self._get_json(
            self.TPEX_FLOW_URL,
            {
                "type": "Daily",
                "sect": "EW",
                "date": day.strftime("%Y/%m/%d"),
                "response": "json",
            },
        )
        fields, rows = _find_table(payload, "代號", "名稱")
        result: list[InstitutionalFlow] = []
        for values in rows:
            if not isinstance(values, list):
                continue
            row = dict(zip(fields, values, strict=False))
            code = _text(values[0] if values else row.get("代號"))
            if not code:
                continue
            # TPEx's current response repeats generic 買進／賣出／買賣超
            # field labels.  The documented column order is three triplets
            # for foreign, trust and dealer totals; use their net columns
            # positionally when the labels cannot distinguish them.
            positional = len(values) >= 23
            foreign = _integer(values[4]) if positional else _first_integer(
                row,
                "外資及陸資(不含外資自營商)-買賣超股數",
                "外資及陸資(不含外資自營商)買賣超股數",
                contains=("外資", "不含", "買賣超"),
            )
            trust = _integer(values[13]) if positional else _first_integer(
                row, "投信-買賣超股數", "投信買賣超股數", contains=("投信", "買賣超")
            )
            dealer = _integer(values[22]) if positional else _first_integer(
                row,
                "自營商-買賣超股數",
                "自營商買賣超股數",
                contains=("自營商", "合計", "買賣超"),
            )
            result.append(
                InstitutionalFlow(
                    code=code,
                    foreign_net_shares=foreign,
                    trust_net_shares=trust,
                    dealer_net_shares=dealer,
                    source_url=source_url,
                )
            )
        return result

    async def taiex_total_return(self, month: date) -> list[IndexPoint]:
        return await self._index_month(
            self.TAIEX_TOTAL_RETURN_URL,
            month,
            "TAIEX_TOTAL_RETURN",
            ("發行量加權股價報酬指數", "TAIEXTotalReturnIndex"),
        )

    async def taiex_price(self, month: date) -> list[IndexPoint]:
        return await self._index_month(
            self.TAIEX_PRICE_URL,
            month,
            "TAIEX_PRICE",
            ("收盤指數", "ClosingIndex"),
        )

    async def _index_month(
        self,
        url: str,
        month: date,
        index_code: str,
        value_names: tuple[str, ...],
    ) -> list[IndexPoint]:
        payload, source_url = await self._get_json(
            url,
            {"date": month.strftime("%Y%m%d"), "response": "json"},
        )
        fields, rows = _find_table(payload, "日期")
        result: list[IndexPoint] = []
        for row in _records(fields, rows):
            value = next((_number(row.get(name)) for name in value_names if name in row), None)
            parsed_date = _date(row.get("日期"))
            if parsed_date and value is not None:
                result.append(IndexPoint(parsed_date, value, index_code, source_url))
        return result

    async def _get_json(
        self, url: str, params: dict[str, str], attempts: int = 3
    ) -> tuple[dict[str, Any], str]:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    response = await client.get(
                        url,
                        params=params,
                        headers={"Accept": "application/json", "User-Agent": "StockResearch/1.0"},
                    )
                if response.status_code in {429, 500, 502, 503, 504, 520}:
                    raise httpx.HTTPStatusError(
                        f"temporary upstream status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("top-level response is not an object")
                status = str(payload.get("stat", "ok")).casefold()
                if status not in {"ok", ""}:
                    raise ValueError(f"upstream status: {payload.get('stat')}")
                return payload, str(response.url)
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(2**attempt)
        raise ResearchSourceError(f"官方資料來源暫時無法使用：{url}") from last_error


def _find_table(payload: dict[str, Any], *required: str) -> tuple[list[str], list[list[Any]]]:
    candidates = [payload, *payload.get("tables", [])]
    for table in candidates:
        fields = [re.sub(r"\s+", "", str(value)) for value in table.get("fields") or []]
        if fields and all(re.sub(r"\s+", "", name) in fields for name in required):
            return fields, table.get("data") or []
    raise ResearchSourceError(f"官方資料欄位已變更：{', '.join(required)}")


def _records(fields: list[str], rows: list[list[Any]]):
    for values in rows:
        if isinstance(values, list):
            yield dict(zip(fields, values, strict=False))


def _text(value: Any) -> str:
    return re.sub(r"<[^>]*>", "", str(value or "")).strip()


def _number(value: Any) -> float | None:
    text = _text(value).replace(",", "")
    if text in {"", "--", "---", "-", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _first_integer(
    row: dict[str, Any], *names: str, contains: tuple[str, ...] = ()
) -> int | None:
    for name in names:
        if name in row:
            return _integer(row[name])
    for key, value in row.items():
        if all(fragment in key for fragment in contains):
            return _integer(value)
    return None


def _date(value: Any) -> date | None:
    text = _text(value)
    parts = re.split(r"[-/]", text)
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(part) for part in parts)
        if year < 1911:
            year += 1911
        return date(year, month, day)
    except ValueError:
        return None


official_research_market_provider = OfficialResearchMarketProvider()
