import pandas as pd

from app.charts import _label_indices, gross_margin_chart
from app.providers.finmind import finmind_provider
from app.providers.goodinfo import goodinfo_provider
from app.providers.market import market_catalog


def test_label_indices_keeps_every_point_for_short_series():
    assert _label_indices(4, 10) == [0, 1, 2, 3]


def test_label_indices_samples_long_series_and_keeps_endpoints():
    indices = _label_indices(100, 10)

    assert len(indices) == 10
    assert indices[0] == 0
    assert indices[-1] == 99


async def test_gross_margin_chart_prefers_goodinfo(monkeypatch, tmp_path):
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-03-31", "2025-06-30"]),
            "gross_margin": [58.8, 59.1],
        }
    )

    async def goodinfo(_):
        return frame

    async def finmind(_):
        raise AssertionError("FinMind should only be a fallback")

    async def company(_):
        return None

    monkeypatch.setattr(goodinfo_provider, "gross_margin_history", goodinfo)
    monkeypatch.setattr(finmind_provider, "gross_margin_history", finmind)
    monkeypatch.setattr(market_catalog, "get", company)
    monkeypatch.setattr("app.charts._new_path", lambda _: tmp_path / "gross.png")

    _, source = await gross_margin_chart("2330")

    assert source == "Goodinfo"
