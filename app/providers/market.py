from dataclasses import dataclass
from datetime import date
from time import monotonic

import httpx

INDUSTRY_NAMES = {
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "14": "建材營造",
    "15": "航運業",
    "16": "觀光餐旅",
    "17": "金融保險業",
    "18": "貿易百貨",
    "19": "綜合",
    "20": "其他",
    "21": "化學工業",
    "22": "生技醫療業",
    "23": "油電燃氣業",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
}

TWSE_HOLIDAY_URL = "https://www.twse.com.tw/holidaySchedule/holidaySchedule"
_closed_days: dict[int, set[date]] = {}


async def is_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    if day.year not in _closed_days:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    TWSE_HOLIDAY_URL,
                    params={"response": "json", "queryYear": day.year - 1911},
                )
                response.raise_for_status()
                rows = response.json().get("data", [])
        except (httpx.HTTPError, ValueError):
            return False
        _closed_days[day.year] = {
            date.fromisoformat(row[0])
            for row in rows
            if len(row) >= 2
            and "開始交易" not in row[1]
            and "最後交易" not in row[1]
        }
    return day not in _closed_days[day.year]


@dataclass(frozen=True)
class Company:
    code: str
    name: str
    industry: str = ""
    business: str = ""
    market: str = "上市"


class MarketCatalog:
    """Company metadata from official TWSE and TPEx OpenAPI endpoints."""

    TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
    FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

    def __init__(self) -> None:
        self._companies: dict[str, Company] = {}
        self._official_codes: set[str] = set()
        self._loaded_at = 0.0

    async def refresh(self, force: bool = False) -> None:
        if not force and self._companies and monotonic() - self._loaded_at < 86400:
            return
        companies: dict[str, Company] = {}
        official_codes: set[str] = set()
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            # FinMind supplies normalized market and industry names for both
            # listed and OTC securities. Official exchange data below enriches
            # this catalog with business descriptions.
            try:
                response = await client.get(
                    self.FINMIND_URL, params={"dataset": "TaiwanStockInfo"}
                )
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("data", []) if payload.get("status") == 200 else []
            except (httpx.HTTPError, ValueError):
                rows = []
            for row in rows:
                code = str(row.get("stock_id", "")).strip()
                name = str(row.get("stock_name", "")).strip()
                if code and name:
                    market_type = str(row.get("type", ""))
                    companies[code] = Company(
                        code=code,
                        name=name,
                        industry=str(row.get("industry_category", "")).strip(),
                        market="上櫃" if market_type.lower() in {"tpex", "otc"} else "上市",
                    )
            for market, url in (("上市", self.TWSE_URL), ("上櫃", self.TPEX_URL)):
                try:
                    response = await client.get(url, headers={"Accept": "application/json"})
                    response.raise_for_status()
                    rows = response.json()
                except (httpx.HTTPError, ValueError):
                    continue
                for row in rows:
                    code = self._value(row, "公司代號", "SecuritiesCompanyCode", "Code")
                    name = self._value(row, "公司簡稱", "公司名稱", "CompanyName")
                    if not code or not name:
                        continue
                    clean_code = code.strip()
                    official_codes.add(clean_code)
                    old = companies.get(clean_code)
                    exchange_industry = self._value(
                        row, "產業別", "SecuritiesIndustryCode"
                    ).strip()
                    normalized_industry = INDUSTRY_NAMES.get(exchange_industry)
                    companies[clean_code] = Company(
                        code=clean_code,
                        name=name.strip(),
                        industry=(
                            normalized_industry
                            or (old.industry if old and old.industry else exchange_industry)
                        ),
                        business=self._value(row, "主要經營業務", "BusinessScope"),
                        market=market,
                    )
        if companies:
            self._companies = companies
            self._official_codes = official_codes
            self._loaded_at = monotonic()

    async def get(self, code: str) -> Company | None:
        await self.refresh()
        return self._companies.get(code)

    async def get_many(self, codes: list[str]) -> dict[str, Company]:
        await self.refresh()
        return {
            code: self._companies[code]
            for code in dict.fromkeys(codes)
            if code in self._companies
        }

    async def all_companies(self, *, official_only: bool = False) -> dict[str, Company]:
        """Return a snapshot of the current official listed-company universe."""
        await self.refresh()
        if official_only and not self._official_codes:
            await self.refresh(force=True)
        if official_only:
            return {
                code: company
                for code, company in self._companies.items()
                if code in self._official_codes
            }
        return dict(self._companies)

    async def find_by_name(self, name: str) -> Company | None:
        await self.refresh()
        normalized = name.strip().casefold()
        if not normalized:
            return None
        return next(
            (
                company
                for company in self._companies.values()
                if company.name.strip().casefold() == normalized
            ),
            None,
        )

    async def peers(self, code: str, limit: int = 8) -> list[Company]:
        await self.refresh()
        target = self._companies.get(code)
        if not target or not target.industry:
            return []
        return [
            company
            for company in self._companies.values()
            if company.code != code and company.industry == target.industry
        ][:limit]

    @staticmethod
    def _value(row: dict, *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value is not None:
                return str(value)
        return ""


market_catalog = MarketCatalog()
