from types import SimpleNamespace

import pytest

from app import research_supplements
from app.providers.goodinfo import goodinfo_provider


async def test_backfill_saves_missing_sections_newest_first(monkeypatch):
    saved = {}
    calls = []

    monkeypatch.setattr(
        research_supplements,
        "get_settings",
        lambda: SimpleNamespace(goodinfo_enabled=True, goodinfo_cache_hours=24),
    )
    monkeypatch.setattr(research_supplements, "read_goodinfo_snapshot", lambda code, section: None)
    monkeypatch.setattr(
        research_supplements,
        "save_goodinfo_snapshot",
        lambda code, section, payload: saved.__setitem__(
            section, research_supplements._recent_first_payload(payload)
        ),
    )

    async def financial(code, sections, years):
        calls.append("financial")
        return {
            "eps": [
                {"period": "2024", "ttm_eps": 9},
                {"period": "2025", "ttm_eps": 12},
            ],
            "monthly_revenue": [
                {"month": "2025-01", "revenue": 10},
                {"month": "2025-02", "revenue": 12},
            ],
        }

    async def product_mix(code):
        calls.append("product_mix")
        return {"status": "unavailable", "reason": "未申報"}

    monkeypatch.setattr(goodinfo_provider, "financial_fallback", financial)
    monkeypatch.setattr(goodinfo_provider, "product_mix_summary", product_mix)

    result = await research_supplements.backfill_goodinfo_stock(
        "6274", years=5, sections=["eps", "monthly_revenue", "product_mix"]
    )

    assert result["status"] == "partial"
    assert calls == ["financial", "product_mix"]
    assert [row["period"] for row in saved["eps"]["items"]] == ["2025", "2024"]
    assert [row["month"] for row in saved["monthly_revenue"]["items"]] == ["2025-02", "2025-01"]
    assert saved["product_mix"]["status"] == "unavailable"


def test_import_goodinfo_html_saves_verified_monthly_revenue(monkeypatch):
    saved = {}
    html = """
    <html><body><h1>Goodinfo 6274 台燿</h1>
    <table>
      <tr><th>月別</th><th>單月營收</th><th>營收</th></tr>
      <tr><td>2026/07</td><td>1710</td><td>1125</td><td>1840</td><td>1060</td>
        <td>-555</td><td>-33.04</td><td>59.08</td><td>+20.7</td><td>+121.5</td>
        <td>302.6</td></tr>
    </table></body></html>
    """
    monkeypatch.setattr(
        research_supplements,
        "save_goodinfo_snapshot",
        lambda code, section, payload: saved.__setitem__(section, payload),
    )

    result = research_supplements.import_goodinfo_html(
        "6274", "monthly_revenue", html, years=1
    )

    assert result["sections"]["monthly_revenue"] == {"status": "available", "items": 1}
    assert saved["monthly_revenue"]["items"][0]["revenue"] == 5_908_000_000
    assert saved["monthly_revenue"]["source"] == "Goodinfo（瀏覽器匯出）"


def test_import_goodinfo_html_rejects_wrong_stock(monkeypatch):
    monkeypatch.setattr(
        research_supplements,
        "save_goodinfo_snapshot",
        lambda *args: pytest.fail("invalid file must not be saved"),
    )

    with pytest.raises(ValueError, match="指定股票"):
        research_supplements.import_goodinfo_html(
            "6274", "monthly_revenue", "<h1>Goodinfo 2382 廣達</h1>"
        )
