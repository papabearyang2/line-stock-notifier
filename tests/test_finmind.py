from datetime import date

import httpx
import pytest

from app.providers.finmind import FinMindError, FinMindProvider


@pytest.mark.asyncio
async def test_dataset_wraps_network_errors(monkeypatch):
    async def fail(*args, **kwargs):
        request = httpx.Request("GET", FinMindProvider.URL)
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fail)

    with pytest.raises(FinMindError, match="連線或回應格式錯誤"):
        await FinMindProvider().dataset("TaiwanStockPrice", "2330", date(2026, 1, 1))
