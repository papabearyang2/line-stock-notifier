import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import goodinfo_backfill
from app.models import ResearchGoodinfoSnapshot
from app.providers.goodinfo import goodinfo_provider


def _snapshot(section, status, checked_at, payload=None, message=""):
    return ResearchGoodinfoSnapshot(
        stock_code="6274",
        section=section,
        status=status,
        payload_json=json.dumps(payload or {}),
        source_name="Goodinfo",
        source_url="https://goodinfo.tw/",
        message=message,
        checked_at=checked_at,
    )


def test_audit_groups_page_gaps_and_excludes_confirmed_source_absence(monkeypatch):
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    snapshots = {
        ("6274", "pe"): _snapshot("pe", "unavailable", now.replace(tzinfo=None)),
        ("6274", "eps"): _snapshot(
            "eps",
            "available",
            (now - timedelta(days=2)).replace(tzinfo=None),
            {"items": [{"period": "2025", "ttm_eps": 12}]},
        ),
        ("6274", "dividends"): _snapshot(
            "dividends",
            "available",
            (now - timedelta(days=40)).replace(tzinfo=None),
            {"items": [{"year": 2025}]},
        ),
        ("6274", "product_mix"): _snapshot(
            "product_mix",
            "unavailable",
            now.replace(tzinfo=None),
            message="Goodinfo 顯示公司未申報產品／業務營收拆分",
        ),
    }
    monkeypatch.setattr(
        goodinfo_backfill,
        "get_settings",
        lambda: SimpleNamespace(
            goodinfo_cache_hours=24,
            goodinfo_backfill_max_pages=2,
            goodinfo_min_interval_seconds=8,
            goodinfo_backfill_interval_minutes=15,
        ),
    )
    monkeypatch.setattr(goodinfo_backfill, "_load_snapshots", lambda codes: snapshots)
    monkeypatch.setattr(goodinfo_backfill, "_load_retry_states", lambda codes: {})
    monkeypatch.setattr(goodinfo_backfill, "goodinfo_request_interval", float)

    result = goodinfo_backfill.audit_goodinfo_gaps(
        stock_codes=["6274"], now=now, max_pages_per_run=2
    )

    assert result["gap_pages"] == 4
    assert result["due_pages"] == 3
    assert result["deferred_pages"] == 1
    assert result["source_absent_sections"] == 1
    assert result["estimated_scheduled_runs"] == 2
    cash_flow = next(page for page in result["pages"] if page["page"] == "cash_flow")
    assert cash_flow["sections"] == ["cash_flow"]


async def test_cycle_stops_after_first_automated_access_failure(monkeypatch):
    audit = {
        "scope": "research",
        "stock_count": 2,
        "gap_pages": 2,
        "due_pages": 2,
        "deferred_pages": 0,
        "source_absent_sections": 0,
        "minimum_request_seconds": 16,
        "estimated_request_minutes": 0.3,
        "max_pages_per_run": 6,
        "estimated_scheduled_runs": 1,
        "pages": [
            {
                "stock_code": "6274",
                "page": "monthly_revenue",
                "sections": ["monthly_revenue"],
                "due": True,
                "reasons": {"monthly_revenue": "missing"},
                "source_url": "https://goodinfo.tw/6274",
            },
            {
                "stock_code": "2383",
                "page": "monthly_revenue",
                "sections": ["monthly_revenue"],
                "due": True,
                "reasons": {"monthly_revenue": "missing"},
                "source_url": "https://goodinfo.tw/2383",
            },
        ],
    }
    failed = _snapshot(
        "monthly_revenue",
        "unavailable",
        datetime(2026, 9, 2),
        message="Goodinfo 未取得可解析資料（可能要求驗證或拒絕自動存取）",
    )
    calls = []
    monkeypatch.setattr(
        goodinfo_backfill,
        "get_settings",
        lambda: SimpleNamespace(
            goodinfo_enabled=True,
            goodinfo_backfill_scope="research",
            goodinfo_backfill_max_pages=6,
        ),
    )
    monkeypatch.setattr(goodinfo_backfill, "audit_goodinfo_gaps", lambda **kwargs: audit)
    monkeypatch.setattr(
        goodinfo_backfill,
        "_load_snapshots",
        lambda codes: {("6274", "monthly_revenue"): failed},
    )
    monkeypatch.setattr(goodinfo_backfill, "_save_run_log", lambda result: None)
    monkeypatch.setattr(
        goodinfo_backfill,
        "_record_retry_failure",
        lambda code, page: {"next_retry_at": "2026-09-02T01:00:00+00:00"},
    )

    async def backfill(code, **kwargs):
        calls.append(code)
        return {
            "status": "partial",
            "sections": {"monthly_revenue": "unavailable"},
        }

    monkeypatch.setattr(goodinfo_backfill, "backfill_goodinfo_stock", backfill)

    result = await goodinfo_backfill.run_goodinfo_backfill_cycle()

    assert calls == ["6274"]
    assert result["attempted_pages"] == 1
    assert result["source_blocked"] is True
    assert result["remaining_due_pages"] == 2


async def test_calibration_uses_1_5x_after_five_accepted_pages(monkeypatch):
    monkeypatch.setattr(
        goodinfo_backfill,
        "audit_goodinfo_gaps",
        lambda **kwargs: {"gap_pages": 0},
    )
    monkeypatch.setattr(
        goodinfo_backfill,
        "_read_state",
        lambda key: {"candidate_index": 4},
    )
    written = {}
    monkeypatch.setattr(
        goodinfo_backfill,
        "_write_state",
        lambda key, payload: written.update({key: payload}),
    )
    calls = []

    async def probe(path, interval):
        calls.append((path, interval))
        return {"status": "accepted", "latency_seconds": 0.1, "bytes": 100}

    monkeypatch.setattr(goodinfo_provider, "probe", probe)

    result = await goodinfo_backfill.run_goodinfo_rate_calibration("6274")

    assert len(calls) == 5
    assert {interval for _, interval in calls} == {3.0}
    assert result["status"] == "calibrated"
    assert result["safe_interval_seconds"] == 4.5
    assert written["calibration:6274"]["safe_interval_seconds"] == 4.5


def test_retry_state_defers_then_becomes_due(monkeypatch):
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    snapshots = {
        ("6274", "pe"): _snapshot("pe", "unavailable", now.replace(tzinfo=None))
    }
    settings = SimpleNamespace(
        goodinfo_cache_hours=24,
        goodinfo_backfill_max_pages=30,
        goodinfo_min_interval_seconds=8,
        goodinfo_backfill_interval_minutes=15,
    )
    monkeypatch.setattr(goodinfo_backfill, "get_settings", lambda: settings)
    monkeypatch.setattr(goodinfo_backfill, "_load_snapshots", lambda codes: snapshots)
    monkeypatch.setattr(goodinfo_backfill, "goodinfo_request_interval", float)
    retry = {("6274", "pe"): {"next_retry_at": (now + timedelta(minutes=15)).isoformat()}}
    monkeypatch.setattr(goodinfo_backfill, "_load_retry_states", lambda codes: retry)

    deferred = goodinfo_backfill.audit_goodinfo_gaps(stock_codes=["6274"], now=now)
    due = goodinfo_backfill.audit_goodinfo_gaps(
        stock_codes=["6274"], now=now + timedelta(minutes=16)
    )

    assert deferred["due_pages"] == 4
    assert due["due_pages"] == 5
