from datetime import date

from scripts import backfill_research


async def test_research_backfill_fetches_newest_trading_days_first(monkeypatch):
    calls = []

    async def trading_day(day):
        return day.weekday() < 5

    async def refresh_day(day, **_kwargs):
        calls.append(day)
        return {"status": "success", "quotes": 1, "flows": 1}

    async def refresh_indices(_start, _end):
        return {"status": "success", "indices": 1}

    monkeypatch.setattr(backfill_research, "is_trading_day", trading_day)
    monkeypatch.setattr(backfill_research, "refresh_research_day", refresh_day)
    monkeypatch.setattr(backfill_research, "refresh_research_indices", refresh_indices)

    await backfill_research.run(
        date(2026, 8, 28),
        date(2026, 9, 2),
        include_flows=True,
        pause=0,
    )

    assert calls == [
        date(2026, 9, 2),
        date(2026, 9, 1),
        date(2026, 8, 31),
        date(2026, 8, 28),
    ]
