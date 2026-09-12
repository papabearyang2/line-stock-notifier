import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from time import monotonic
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import SessionLocal
from app.models import GoodinfoCache
from app.providers.goodinfo_browser import goodinfo_browser

logger = logging.getLogger(__name__)


class GoodinfoProvider:
    """Conservative, opt-in HTML adapter.

    This adapter intentionally performs no bypass, CAPTCHA solving, proxy rotation,
    or retry storm. If Goodinfo denies automated access, callers receive no result
    and the bot falls back to official/curated sources.
    """

    BASE = "https://goodinfo.tw/tw"
    PRODUCT_MIX_PATH = "/ShowSaleMonProdChart.asp?STOCK_ID={stock_code}"
    CASH_FLOW_PATH = "/StockCashFlow.asp?STOCK_ID={stock_code}"
    MONTHLY_REVENUE_PATH = "/ShowSaleMonChart.asp?STOCK_ID={stock_code}"
    DIVIDEND_PATH = "/StockDividendPolicy.asp?STOCK_ID={stock_code}"
    VALUATION_PATH = "/ShowK_ChartFlow.asp?RPT_CAT=PER&STOCK_ID={stock_code}"

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def research_summary(self, stock_code: str) -> str | None:
        result = await self.product_mix_summary(stock_code)
        return result.get("summary")

    async def product_mix_summary(self, stock_code: str) -> dict[str, Any]:
        settings = get_settings()
        source_url = f"{self.BASE}{self.PRODUCT_MIX_PATH.format(stock_code=stock_code)}"
        if not settings.goodinfo_enabled:
            return {
                "status": "unavailable",
                "source": "Goodinfo（補充）",
                "source_url": source_url,
                "reason": "Goodinfo 補充來源尚未啟用",
            }
        soup = await self._fetch_soup(self.PRODUCT_MIX_PATH.format(stock_code=stock_code))
        if soup is None:
            return {
                "status": "unavailable",
                "source": "Goodinfo（補充）",
                "source_url": source_url,
                "reason": "Goodinfo 暫時無法取得（可能要求驗證或拒絕自動存取）",
            }
        parsed = self._parse_product_mix_summary(soup)
        return {
            **parsed,
            "source": "Goodinfo（補充）",
            "source_url": source_url,
        }

    @staticmethod
    def _parse_product_mix_summary(soup: BeautifulSoup) -> dict[str, Any]:
        candidates = []
        for table in soup.find_all("table"):
            table_text = re.sub(r"\s+", " ", table.get_text(" ", strip=True))
            if (
                ("產品/業務" in table_text and "佔比" in table_text)
                or any(
                    keyword in table_text
                    for keyword in ("主要業務", "產品比重", "營收比重")
                )
            ):
                candidates.append(table_text)
        if not candidates:
            return {
                "status": "unavailable",
                "reason": "Goodinfo 未提供產品／業務營收拆分",
            }
        # Nested layout tables often repeat the whole page. Prefer the smallest
        # matching table because it is normally the actual product-mix table.
        summary = min(candidates, key=len)[:900]
        if "無申報資料" in summary and not re.search(r"\d+(?:\.\d+)?\s*%", summary):
            return {
                "status": "unavailable",
                "reason": "Goodinfo 顯示公司未申報產品／業務營收拆分",
            }
        return {
            "status": "available",
            "summary": summary,
        }

    async def financial_fallback(
        self, stock_code: str, sections: set[str], years: int = 5
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch only missing sections, serially, through the shared limiter/cache."""
        settings = get_settings()
        if not settings.goodinfo_enabled or not sections:
            return {}
        result: dict[str, list[dict[str, Any]]] = {}
        if {"eps", "cash_flow"} & sections:
            soup = await self._fetch_soup(self.CASH_FLOW_PATH.format(stock_code=stock_code))
            if soup is not None:
                rows = self._parse_cash_flow_history(soup, years)
                if "eps" in sections:
                    result["eps"] = [
                        {
                            "period": row["period"],
                            "single_quarter_eps": None,
                            "ttm_eps": row["eps"],
                            "ttm_status": "source_reported_annual",
                            "type": "Goodinfo 年度／累季公開值",
                        }
                        for row in rows
                        if row.get("eps") is not None
                    ]
                if "cash_flow" in sections:
                    result["cash_flow"] = [
                        {
                            "period": row["period"],
                            "operating": row.get("operating"),
                            "investing": row.get("investing"),
                            "financing": row.get("financing"),
                            "basis": "reported_annual",
                            "single_quarter": None,
                        }
                        for row in rows
                        if any(row.get(key) is not None for key in ("operating", "investing", "financing"))
                    ]
        if "monthly_revenue" in sections:
            soup = await self._fetch_soup(
                self.MONTHLY_REVENUE_PATH.format(stock_code=stock_code)
            )
            if soup is not None:
                result["monthly_revenue"] = self._parse_monthly_revenue_history(
                    soup, years * 12
                )
        if "pe" in sections:
            try:
                frame = await self.valuation_history(stock_code, years=years)
            except Exception:
                frame = None
            if frame is not None:
                result["pe"] = [
                    {"date": row.date.date().isoformat(), "pe": float(row.pe)}
                    for row in frame.itertuples(index=False)
                ]
        return result

    async def dividend_fallback(self, stock_code: str, years: int = 5) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.goodinfo_enabled:
            return []
        soup = await self._fetch_soup(self.DIVIDEND_PATH.format(stock_code=stock_code))
        if soup is None:
            return []
        return self._parse_dividend_policy(soup, years)

    async def gross_margin_history(
        self, stock_code: str, years: int = 5
    ) -> pd.DataFrame | None:
        soup = await self._fetch_soup(
            f"/StockFinDetail.asp?RPT_CAT=XX_M_QUAR&STOCK_ID={stock_code}"
        )
        if soup is None:
            return None
        frame = self._parse_gross_margin_table(soup)
        if frame.empty:
            return None
        cutoff = pd.Timestamp(date.today() - timedelta(days=365 * years))
        filtered = frame.loc[frame["date"] >= cutoff]
        return filtered if not filtered.empty else frame.tail(years * 4)

    async def valuation_history(
        self, stock_code: str, years: int = 5
    ) -> pd.DataFrame | None:
        soup = await self._fetch_soup(
            self.VALUATION_PATH.format(stock_code=stock_code)
        )
        if soup is None:
            return None
        frame = self._parse_valuation_table(soup)
        if frame.empty:
            return None
        cutoff = pd.Timestamp(date.today() - timedelta(days=365 * years))
        filtered = frame.loc[frame["date"] >= cutoff]
        return filtered if not filtered.empty else frame.tail(years * 12)

    async def _fetch_soup(self, path: str) -> BeautifulSoup | None:
        settings = get_settings()
        if not settings.goodinfo_enabled:
            return None
        cached = self._read_cache(path)
        if cached:
            return BeautifulSoup(cached, "html.parser")
        async with self._lock:
            cached = self._read_cache(path)
            if cached:
                return BeautifulSoup(cached, "html.parser")
            stale = self._read_cache(path, allow_stale=True)
            try:
                from app.goodinfo_backfill import goodinfo_request_interval

                request_interval = goodinfo_request_interval(
                    settings.goodinfo_min_interval_seconds
                )
            except Exception:
                request_interval = settings.goodinfo_min_interval_seconds
            wait_for = request_interval - (
                monotonic() - self._last_request
            )
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            try:
                html = await self._get(path)
            except (httpx.HTTPError, ValueError):
                if stale:
                    self._write_cache(path, stale)
                    return BeautifulSoup(stale, "html.parser")
                return None
            finally:
                self._last_request = monotonic()
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        if self._is_challenge(html, text):
            if stale:
                self._write_cache(path, stale)
                return BeautifulSoup(stale, "html.parser")
            return None
        self._write_cache(path, html)
        return soup

    async def probe(self, path: str, minimum_interval_seconds: float) -> dict[str, Any]:
        """Make one uncached calibration request without bypassing access controls."""
        async with self._lock:
            wait_for = minimum_interval_seconds - (monotonic() - self._last_request)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            started = monotonic()
            try:
                html = await self._get(path)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    status = "rate_limited"
                elif exc.response.status_code in {401, 403}:
                    status = "access_refused"
                else:
                    status = "http_error"
                return {
                    "status": status,
                    "http_status": exc.response.status_code,
                    "latency_seconds": round(monotonic() - started, 3),
                }
            except (httpx.HTTPError, ValueError) as exc:
                browser_status = str(exc)
                return {
                    "status": browser_status
                    if browser_status
                    in {
                        "access_refused",
                        "browser_error",
                        "browser_start_failed",
                        "challenge_timeout",
                    }
                    else "network_error",
                    "error": type(exc).__name__,
                    "latency_seconds": round(monotonic() - started, 3),
                }
            finally:
                self._last_request = monotonic()
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return {
            "status": "challenge" if self._is_challenge(html, text) else "accepted",
            "latency_seconds": round(monotonic() - started, 3),
            "bytes": len(html.encode("utf-8")),
        }

    @staticmethod
    def _is_challenge(html: str, text: str) -> bool:
        return (
            "驗證碼" in text
            or "Access Denied" in text
            or "Just a moment" in text
            or "正在執行安全驗證" in text
            or "請稍候" in text
            or "Enable JavaScript and cookies" in text
            or (len(html) < 10_000 and "CLIENT_KEY" in html)
        )

    @staticmethod
    def _read_cache(path: str, allow_stale: bool = False) -> str | None:
        settings = get_settings()
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=settings.goodinfo_cache_hours
        )
        try:
            with SessionLocal() as session:
                cached = session.get(GoodinfoCache, path)
                if cached and (allow_stale or cached.fetched_at >= cutoff):
                    return cached.html
        except SQLAlchemyError:
            logger.warning("Could not read Goodinfo cache", exc_info=True)
        return None

    @staticmethod
    def _write_cache(path: str, html: str) -> None:
        try:
            with SessionLocal() as session:
                session.merge(
                    GoodinfoCache(
                        path=path,
                        html=html,
                        fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    )
                )
                session.commit()
        except SQLAlchemyError:
            logger.warning("Could not write Goodinfo cache", exc_info=True)

    @classmethod
    def _parse_gross_margin_table(cls, soup: BeautifulSoup) -> pd.DataFrame:
        for table in soup.find_all("table"):
            table_text = re.sub(r"\s+", " ", table.get_text(" ", strip=True))
            if "營業毛利率" not in table_text:
                continue
            periods = list(
                dict.fromkeys(re.findall(r"\b(?:19|20)?\d{2}Q[1-4]\b", table_text))
            )
            for row in table.find_all("tr"):
                cells = [
                    re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
                    for cell in row.find_all(["th", "td"])
                ]
                if not cells or not any("營業毛利率" in cell for cell in cells):
                    continue
                values = [
                    cls._number(cell)
                    for cell in cells
                    if re.fullmatch(r"[-+]?\d[\d,]*(?:\.\d+)?%?", cell)
                ]
                numeric = [value for value in values if value is not None]
                if periods and numeric:
                    return cls._quarter_frame(periods[: len(numeric)], numeric)

            segment = table_text.split("營業毛利率", 1)[1]
            segment = segment.split("營業利益率", 1)[0]
            numeric = [
                float(value.replace(",", ""))
                for value in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", segment)
            ]
            if periods and numeric:
                return cls._quarter_frame(periods[: len(numeric)], numeric[-len(periods) :])
        return pd.DataFrame(columns=["date", "gross_margin"])

    @classmethod
    def _parse_valuation_table(cls, soup: BeautifulSoup) -> pd.DataFrame:
        records: list[dict] = []
        for table in soup.find_all("table"):
            table_text = re.sub(r"\s+", " ", table.get_text(" ", strip=True))
            if "目前" not in table_text or "PER" not in table_text or "月份" not in table_text:
                continue
            for row in table.find_all("tr"):
                cells = [
                    re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
                    for cell in row.find_all(["th", "td"])
                ]
                if len(cells) < 6:
                    continue
                period_match = re.fullmatch(r"(\d{2})M(\d{2})", cells[0])
                if not period_match:
                    continue
                close = cls._number(cells[1])
                pe = cls._number(cells[5])
                if close is None or pe is None or close <= 0 or pe <= 0:
                    continue
                year = 2000 + int(period_match.group(1))
                month = int(period_match.group(2))
                records.append(
                    {
                        "date": pd.Timestamp(year=year, month=month, day=1)
                        + pd.offsets.MonthEnd(0),
                        "close": close,
                        "pe": pe,
                    }
                )
        if not records:
            return pd.DataFrame(columns=["date", "close", "pe"])
        return (
            pd.DataFrame(records)
            .drop_duplicates(subset=["date"], keep="first")
            .sort_values("date")
            .reset_index(drop=True)
        )

    @classmethod
    def _parse_cash_flow_history(cls, soup: BeautifulSoup, years: int) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for table in soup.find_all("table"):
            table_text = re.sub(r"\s+", " ", table.get_text(" ", strip=True))
            # Goodinfo's live table may split the two characters into
            # separate header cells, yielding "營業 活動" after extraction.
            compact_text = table_text.replace(" ", "")
            if "營業活動" not in compact_text or "EPS" not in compact_text:
                continue
            for row in table.find_all("tr"):
                cells = [
                    re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
                    for cell in row.find_all(["th", "td"])
                ]
                if len(cells) < 19 or not re.fullmatch(r"(?:19|20)\d{2}", cells[0]):
                    continue
                values = [cls._number(cell) for cell in cells[1:]]
                if len(values) < 18:
                    continue
                records.append(
                    {
                        "period": cells[0],
                        "operating": values[8],
                        "investing": values[9],
                        "financing": values[10],
                        "eps": values[17],
                    }
                )
        return records[-years:]

    @classmethod
    def _parse_monthly_revenue_history(
        cls, soup: BeautifulSoup, limit: int
    ) -> list[dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for table in soup.find_all("table"):
            table_text = re.sub(r"\s+", " ", table.get_text(" ", strip=True))
            if "營收" not in table_text or "月別" not in table_text:
                continue
            for row in table.find_all("tr"):
                cells = [
                    re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
                    for cell in row.find_all(["th", "td"])
                ]
                if not cells or not re.fullmatch(r"(?:19|20)?\d{2}/\d{2}", cells[0]):
                    continue
                period = cells[0]
                if len(period) == 5:
                    period = f"20{period}"
                numbers = [cls._number(cell) for cell in cells[1:]]
                if len(numbers) < 9 or numbers[6] is None:
                    continue
                records[period.replace("/", "-")] = {
                    "month": period.replace("/", "-"),
                    "revenue": numbers[6] * 100_000_000,
                    "yoy_pct": numbers[8],
                    "mom_pct": numbers[7],
                    "unit": "source_reported_億元_converted_to_NTD",
                }
        return [records[key] for key in sorted(records)[-limit:]]

    @classmethod
    def _parse_dividend_policy(cls, soup: BeautifulSoup, years: int) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for table in soup.find_all("table"):
            table_text = re.sub(r"\s+", " ", table.get_text(" ", strip=True))
            if "股東股利" not in table_text or "現金股利" not in table_text:
                continue
            for row in table.find_all("tr"):
                cells = [
                    re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
                    for cell in row.find_all(["th", "td"])
                ]
                if len(cells) < 9 or not re.fullmatch(r"(?:19|20)\d{2}", cells[0]):
                    continue
                cash_total = cls._number(cells[4])
                stock_total = cls._number(cells[7])
                if cash_total is None and stock_total is None:
                    continue
                records.append(
                    {
                        "year": int(cells[0]),
                        "cash_dividend_per_share": cash_total,
                        "stock_dividend_per_share": stock_total,
                        "cash_earnings_distribution": cls._number(cells[2]),
                        "cash_statutory_surplus": cls._number(cells[3]),
                        "stock_earnings_distribution": cls._number(cells[5]),
                        "stock_statutory_surplus": cls._number(cells[6]),
                        "pre_ex_close": None,
                        "official_reference_price": None,
                        "historical_yield_pct": None,
                        "fill": {
                            "status": "unavailable",
                            "reason": "Goodinfo 年度股利頁未提供官方逐日填權息驗證",
                        },
                    }
                )
        return sorted(records, key=lambda item: item["year"], reverse=True)[:years]

    @staticmethod
    def _quarter_frame(periods: list[str], values: list[float]) -> pd.DataFrame:
        records = []
        for period, value in zip(periods, values, strict=False):
            normalized = period if len(period) == 6 else f"20{period}"
            try:
                timestamp = pd.Period(normalized, freq="Q").end_time.normalize()
            except ValueError:
                continue
            records.append({"date": timestamp, "gross_margin": value})
        return (
            pd.DataFrame(records, columns=["date", "gross_margin"])
            .sort_values("date")
            .reset_index(drop=True)
        )

    @staticmethod
    def _number(value: str) -> float | None:
        cleaned = (
            value.replace(",", "")
            .replace("%", "")
            .replace("(年化)", "")
            .replace("年化", "")
            .strip()
        )
        if cleaned in {"", "-", "--", "N/A", "無資料", "無申報資料"}:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    async def _get(self, path: str) -> str:
        if get_settings().goodinfo_browser_enabled:
            return await goodinfo_browser.fetch(f"{self.BASE}{path}")
        return await self._get_http(path)

    @staticmethod
    async def _get_http(path: str) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PersonalStockResearchBot/1.0)",
            "Accept-Language": "zh-TW,zh;q=0.9",
        }
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(f"{self.BASE}{path}", headers=headers)
            response.raise_for_status()
            return response.text

    @staticmethod
    async def close() -> None:
        await goodinfo_browser.close()


goodinfo_provider = GoodinfoProvider()
