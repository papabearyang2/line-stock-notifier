from datetime import date, timedelta

import httpx
import pandas as pd

from app.config import get_settings


class FinMindError(RuntimeError):
    pass


class FinMindProvider:
    URL = "https://api.finmindtrade.com/api/v4/data"

    async def dataset(
        self, dataset: str, stock_code: str, start_date: date, end_date: date | None = None
    ) -> pd.DataFrame:
        settings = get_settings()
        params = {
            "dataset": dataset,
            "data_id": stock_code,
            "start_date": start_date.isoformat(),
        }
        if end_date:
            params["end_date"] = end_date.isoformat()
        if settings.finmind_token:
            params["token"] = settings.finmind_token
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(self.URL, params=params)
                response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FinMindError("FinMind 連線或回應格式錯誤") from exc
        if int(payload.get("status", 0)) != 200:
            raise FinMindError(str(payload.get("msg") or "FinMind request failed"))
        return pd.DataFrame(payload.get("data", []))

    async def valuation_history(self, stock_code: str, years: int = 5) -> pd.DataFrame:
        start = date.today() - timedelta(days=365 * years)
        pe, price = await self._parallel_datasets(stock_code, start)
        if pe.empty or price.empty:
            raise FinMindError("查無本益比或股價歷史資料")
        pe_col = self._first_column(pe, "PER", "pe_ratio")
        close_col = self._first_column(price, "close")
        if not pe_col or not close_col:
            raise FinMindError("資料欄位格式已變更")
        left = pe[["date", pe_col]].rename(columns={pe_col: "pe"})
        right = price[["date", close_col]].rename(columns={close_col: "close"})
        frame = left.merge(right, on="date", how="inner")
        frame["date"] = pd.to_datetime(frame["date"])
        frame["pe"] = pd.to_numeric(frame["pe"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        return frame.dropna().query("pe > 0 and close > 0").sort_values("date")

    async def gross_margin_history(self, stock_code: str, years: int = 5) -> pd.DataFrame:
        start = date.today() - timedelta(days=365 * years)
        frame = await self.dataset("TaiwanStockFinancialStatements", stock_code, start)
        if frame.empty or "date" not in frame.columns:
            raise FinMindError("查無財務報表資料")
        type_col = self._first_column(frame, "type", "account")
        value_col = self._first_column(frame, "value", "amount")
        if not type_col or not value_col:
            raise FinMindError("資料欄位格式已變更")
        aliases = {
            "revenue": {
                "Revenue",
                "OperatingRevenue",
                "營業收入合計",
                "營業收入",
            },
            "gross_profit": {
                "GrossProfit",
                "GrossProfitLossFromOperations",
                "營業毛利（毛損）",
                "營業毛利（毛損）淨額",
            },
        }
        selected = frame[
            frame[type_col].isin(aliases["revenue"] | aliases["gross_profit"])
        ].copy()
        selected["metric"] = selected[type_col].map(
            lambda value: "revenue" if value in aliases["revenue"] else "gross_profit"
        )
        selected[value_col] = pd.to_numeric(selected[value_col], errors="coerce")
        pivot = selected.pivot_table(
            index="date", columns="metric", values=value_col, aggfunc="last"
        )
        if not {"revenue", "gross_profit"}.issubset(pivot.columns):
            raise FinMindError("找不到營收或毛利欄位")
        pivot = pivot.reset_index()
        pivot["date"] = pd.to_datetime(pivot["date"])
        pivot["gross_margin"] = pivot["gross_profit"] / pivot["revenue"] * 100
        return pivot.replace([float("inf"), float("-inf")], pd.NA).dropna().sort_values("date")

    async def _parallel_datasets(
        self, stock_code: str, start: date
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        import asyncio

        return await asyncio.gather(
            self.dataset("TaiwanStockPER", stock_code, start),
            self.dataset("TaiwanStockPrice", stock_code, start),
        )

    @staticmethod
    def _first_column(frame: pd.DataFrame, *candidates: str) -> str | None:
        return next((name for name in candidates if name in frame.columns), None)


finmind_provider = FinMindProvider()
