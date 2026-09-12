"""Audit and cautiously backfill missing Goodinfo research supplements."""

from __future__ import annotations

import json
import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import (
    GoodinfoAutomationState,
    ResearchGoodinfoSnapshot,
    ResearchRefreshLog,
    ResearchSecurity,
    WatchStock,
)
from app.research_catalog import load_research_catalog
from app.research_supplements import backfill_goodinfo_stock, goodinfo_source_url


GOODINFO_PAGE_SECTIONS = {
    "monthly_revenue": ("monthly_revenue",),
    "pe": ("pe",),
    "cash_flow": ("eps", "cash_flow"),
    "dividends": ("dividends",),
    "product_mix": ("product_mix",),
}
GOODINFO_REFRESH_HOURS = {
    "monthly_revenue": 24 * 7,
    "pe": 24 * 7,
    "eps": 24 * 30,
    "cash_flow": 24 * 30,
    "dividends": 24 * 30,
    "product_mix": 24 * 90,
}
GOODINFO_BROWSER_RETRY_SECONDS = (60, 5 * 60, 15 * 60, 60 * 60, 4 * 60 * 60)
GOODINFO_HTTP_RETRY_SECONDS = (15 * 60, 60 * 60, 4 * 60 * 60, 12 * 60 * 60, 24 * 60 * 60)
GOODINFO_CALIBRATION_INTERVALS = (10.0, 8.0, 6.0, 4.0, 3.0)


def goodinfo_stock_codes(scope: str = "research") -> list[str]:
    """Return the dashboard/watchlist universe, or every active four-digit stock."""
    if scope not in {"research", "market"}:
        raise ValueError("scope must be research or market")
    init_db()
    catalog = load_research_catalog().get("stocks", {}) or {}
    codes = {str(code) for code in catalog if _is_stock_code(str(code))}
    with SessionLocal() as session:
        codes.update(
            str(code)
            for code in session.scalars(select(WatchStock.stock_code).distinct())
            if _is_stock_code(str(code))
        )
        if scope == "market":
            codes.update(
                str(code)
                for code in session.scalars(
                    select(ResearchSecurity.code).where(ResearchSecurity.is_active.is_(True))
                )
                if _is_stock_code(str(code))
            )
    return sorted(codes)


def audit_goodinfo_gaps(
    *,
    scope: str = "research",
    stock_codes: Iterable[str] | None = None,
    now: datetime | None = None,
    max_pages_per_run: int | None = None,
) -> dict[str, Any]:
    """Find missing, failed and stale Goodinfo pages without making network requests."""
    settings = get_settings()
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    selected_codes = goodinfo_stock_codes(scope) if stock_codes is None else stock_codes
    codes = sorted({str(code) for code in selected_codes if _is_stock_code(str(code))})
    snapshots = _load_snapshots(codes)
    retry_states = _load_retry_states(codes)
    pages: list[dict[str, Any]] = []
    source_absent = 0

    for page, sections in GOODINFO_PAGE_SECTIONS.items():
        for code in codes:
            section_gaps = []
            for section in sections:
                gap = _section_gap(
                    snapshots.get((code, section)),
                    section,
                    checked_at,
                    settings.goodinfo_cache_hours,
                )
                if gap == "source_absent":
                    source_absent += 1
                elif gap:
                    section_gaps.append({"section": section, **gap})
            if not section_gaps:
                continue
            due = any(item["due"] for item in section_gaps)
            page_gap = {
                "stock_code": code,
                "page": page,
                "sections": [item["section"] for item in section_gaps],
                "due": due,
                "reasons": {item["section"]: item["reason"] for item in section_gaps},
                "source_url": goodinfo_source_url(code, page),
            }
            retry = retry_states.get((code, page), {})
            retry_at = _parse_timestamp(retry.get("next_retry_at"))
            if retry_at:
                page_gap["due"] = retry_at <= checked_at
                if retry_at > checked_at:
                    page_gap["retry_after"] = retry_at.isoformat()
            pages.append(page_gap)

    due_pages = [page for page in pages if page["due"]]
    page_limit = max_pages_per_run or settings.goodinfo_backfill_max_pages
    interval = goodinfo_request_interval(settings.goodinfo_min_interval_seconds)
    minimum_seconds = len(pages) * interval
    scheduled_runs = math.ceil(len(due_pages) / page_limit) if due_pages and page_limit else 0
    scheduled_wait_minutes = max(
        0.0,
        (scheduled_runs - 1)
        * (
            settings.goodinfo_backfill_interval_minutes
            - page_limit * interval / 60
        ),
    )
    return {
        "scope": scope,
        "stock_count": len(codes),
        "gap_pages": len(pages),
        "due_pages": len(due_pages),
        "deferred_pages": len(pages) - len(due_pages),
        "source_absent_sections": source_absent,
        "minimum_request_seconds": round(minimum_seconds, 1),
        "estimated_request_minutes": round(minimum_seconds / 60, 1),
        "estimated_wall_minutes": round(minimum_seconds / 60 + scheduled_wait_minutes, 1),
        "max_pages_per_run": page_limit,
        "estimated_scheduled_runs": scheduled_runs,
        "pages": pages,
    }


async def run_goodinfo_backfill_cycle(
    *,
    scope: str | None = None,
    years: int = 5,
    max_pages: int | None = None,
    stock_codes: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Run one bounded cycle and stop immediately if automated access is refused."""
    settings = get_settings()
    selected_scope = scope or settings.goodinfo_backfill_scope
    page_limit = max_pages or settings.goodinfo_backfill_max_pages
    audit = audit_goodinfo_gaps(
        scope=selected_scope,
        stock_codes=stock_codes,
        max_pages_per_run=page_limit,
    )
    result: dict[str, Any] = {
        **{key: value for key, value in audit.items() if key != "pages"},
        "status": "disabled" if not settings.goodinfo_enabled else "success",
        "attempted_pages": 0,
        "completed_pages": 0,
        "source_blocked": False,
        "results": [],
        "browser_export_queue": audit["pages"],
    }
    if not settings.goodinfo_enabled:
        _save_run_log(result)
        return result

    due_pages = [page for page in audit["pages"] if page["due"]][:page_limit]
    for page in due_pages:
        page_result = await backfill_goodinfo_stock(
            page["stock_code"],
            years=years,
            sections=page["sections"],
        )
        result["attempted_pages"] += 1
        result["results"].append(
            {
                "stock_code": page["stock_code"],
                "page": page["page"],
                "status": page_result["status"],
                "sections": page_result.get("sections", {}),
            }
        )
        refreshed = _load_snapshots([page["stock_code"]])
        unresolved = [
            refreshed.get((page["stock_code"], section))
            for section in page["sections"]
            if not _snapshot_is_complete(refreshed.get((page["stock_code"], section)))
        ]
        retryable = [snapshot for snapshot in unresolved if not _is_source_absent(snapshot)]
        if retryable:
            retry = _record_retry_failure(page["stock_code"], page["page"])
            result["status"] = "partial"
            result["source_blocked"] = True
            result["retry_after"] = retry["next_retry_at"]
            break
        else:
            _clear_retry_state(page["stock_code"], page["page"])
            result["completed_pages"] += 1

    result["remaining_due_pages"] = max(
        0, audit["due_pages"] - result["completed_pages"]
    )
    _save_run_log(result)
    return result


async def run_goodinfo_automation(
    *, scope: str | None = None, years: int = 5, max_pages: int | None = None
) -> dict[str, Any]:
    """Keep rollout on the pilot stock until access-rate calibration succeeds."""
    settings = get_settings()
    pilot = settings.goodinfo_pilot_stock_code
    pilot_audit = audit_goodinfo_gaps(stock_codes=[pilot])
    if pilot_audit["gap_pages"]:
        result = await run_goodinfo_backfill_cycle(
            years=years, max_pages=max_pages, stock_codes=[pilot]
        )
        result["phase"] = "pilot_backfill"
        return result

    calibration = await run_goodinfo_rate_calibration(pilot)
    if calibration["status"] != "calibrated" or not settings.goodinfo_rollout_enabled:
        result = {
            "status": "success" if calibration["status"] == "calibrated" else "partial",
            "phase": "pilot_calibration",
            "completed_pages": 0,
            "calibration": calibration,
        }
        _save_run_log(result)
        return result

    result = await run_goodinfo_backfill_cycle(
        scope=scope or settings.goodinfo_backfill_scope,
        years=years,
        max_pages=max_pages,
    )
    result["phase"] = "rollout"
    result["safe_interval_seconds"] = calibration["safe_interval_seconds"]
    return result


async def run_goodinfo_rate_calibration(stock_code: str = "6274") -> dict[str, Any]:
    """Probe five distinct pilot pages, from slow to faster request intervals."""
    audit = audit_goodinfo_gaps(stock_codes=[stock_code])
    if audit["gap_pages"]:
        return {"status": "waiting_for_pilot", "gap_pages": audit["gap_pages"]}

    state_key = f"calibration:{stock_code}"
    state = _read_state(state_key)
    now = datetime.now(timezone.utc)
    retry_at = _parse_timestamp(state.get("next_retry_at"))
    if retry_at and retry_at > now:
        return {**state, "status": "deferred"}
    if state.get("status") == "calibrated":
        return state

    from app.providers.goodinfo import GoodinfoProvider, goodinfo_provider

    candidate_index = min(
        int(state.get("candidate_index", 0)), len(GOODINFO_CALIBRATION_INTERVALS) - 1
    )
    candidate = GOODINFO_CALIBRATION_INTERVALS[candidate_index]
    paths = (
        GoodinfoProvider.MONTHLY_REVENUE_PATH.format(stock_code=stock_code),
        GoodinfoProvider.CASH_FLOW_PATH.format(stock_code=stock_code),
        GoodinfoProvider.VALUATION_PATH.format(stock_code=stock_code),
        GoodinfoProvider.DIVIDEND_PATH.format(stock_code=stock_code),
        GoodinfoProvider.PRODUCT_MIX_PATH.format(stock_code=stock_code),
    )
    results = []
    for path in paths:
        probe = await goodinfo_provider.probe(path, candidate)
        results.append({"path": path, **probe})
        if probe["status"] != "accepted":
            failures = int(state.get("failure_count", 0)) + 1
            if probe["status"] == "rate_limited" and state.get("last_success_interval"):
                safe_interval = round(float(state["last_success_interval"]) * 1.5, 2)
                state = {
                    **state,
                    "status": "calibrated",
                    "safe_interval_seconds": safe_interval,
                    "last_result": probe["status"],
                    "results": results,
                }
            else:
                state = {
                    **state,
                    "status": "blocked",
                    "candidate_index": candidate_index,
                    "candidate_interval_seconds": candidate,
                    "failure_count": failures,
                    "last_result": probe["status"],
                    "next_retry_at": (
                        now + timedelta(seconds=_retry_delay(stock_code, "calibration", failures))
                    ).isoformat(),
                    "results": results,
                }
            _write_state(state_key, state)
            return state

    if candidate_index == len(GOODINFO_CALIBRATION_INTERVALS) - 1:
        state = {
            "status": "calibrated",
            "candidate_index": candidate_index,
            "last_success_interval": candidate,
            "safe_interval_seconds": round(candidate * 1.5, 2),
            "failure_count": 0,
            "results": results,
        }
    else:
        state = {
            "status": "calibrating",
            "candidate_index": candidate_index + 1,
            "last_success_interval": candidate,
            "failure_count": 0,
            "results": results,
        }
    _write_state(state_key, state)
    return state


def goodinfo_request_interval(default: float) -> float:
    """Return the calibrated 1.5x safety interval, or the configured default."""
    settings = get_settings()
    pilot = getattr(settings, "goodinfo_pilot_stock_code", "6274")
    state = _read_state(f"calibration:{pilot}")
    if state.get("status") == "calibrated" and state.get("safe_interval_seconds"):
        return max(float(default), float(state["safe_interval_seconds"]))
    return float(default)


def _load_retry_states(codes: list[str]) -> dict[tuple[str, str], dict[str, Any]]:
    if not codes:
        return {}
    init_db()
    selected = set(codes)
    with SessionLocal() as session:
        rows = session.scalars(
            select(GoodinfoAutomationState).where(
                GoodinfoAutomationState.key.like("retry:%")
            )
        ).all()
    states = {}
    for row in rows:
        parts = row.key.split(":", 2)
        if len(parts) == 3 and parts[1] in selected:
            _, stock_code, page = parts
            states[(stock_code, page)] = _decode_state(row.payload_json)
    return states


def _read_state(key: str) -> dict[str, Any]:
    init_db()
    with SessionLocal() as session:
        row = session.get(GoodinfoAutomationState, key)
        return _decode_state(row.payload_json) if row else {}


def _write_state(key: str, payload: dict[str, Any]) -> None:
    init_db()
    with SessionLocal() as session:
        session.merge(
            GoodinfoAutomationState(
                key=key,
                payload_json=json.dumps(payload, ensure_ascii=False),
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        session.commit()


def _record_retry_failure(stock_code: str, page: str) -> dict[str, Any]:
    key = f"retry:{stock_code}:{page}"
    state = _read_state(key)
    failures = int(state.get("failure_count", 0)) + 1
    now = datetime.now(timezone.utc)
    state = {
        "status": "retry_wait",
        "failure_count": failures,
        "last_attempt_at": now.isoformat(),
        "next_retry_at": (
            now + timedelta(seconds=_retry_delay(stock_code, page, failures))
        ).isoformat(),
    }
    _write_state(key, state)
    return state


def _clear_retry_state(stock_code: str, page: str) -> None:
    init_db()
    with SessionLocal() as session:
        row = session.get(GoodinfoAutomationState, f"retry:{stock_code}:{page}")
        if row:
            session.delete(row)
            session.commit()


def _retry_delay(stock_code: str, page: str, failures: int) -> int:
    schedule = (
        GOODINFO_BROWSER_RETRY_SECONDS
        if get_settings().goodinfo_browser_enabled
        else GOODINFO_HTTP_RETRY_SECONDS
    )
    base = schedule[min(max(failures, 1) - 1, len(schedule) - 1)]
    digest = hashlib.sha256(f"{stock_code}:{page}:{failures}".encode()).digest()
    jitter = 0.9 + digest[0] / 2550
    return round(base * jitter)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _decode_state(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_snapshots(codes: list[str]) -> dict[tuple[str, str], ResearchGoodinfoSnapshot]:
    if not codes:
        return {}
    init_db()
    with SessionLocal() as session:
        rows = session.scalars(
            select(ResearchGoodinfoSnapshot).where(
                ResearchGoodinfoSnapshot.stock_code.in_(codes)
            )
        ).all()
        return {(row.stock_code, row.section): row for row in rows}


def _section_gap(
    snapshot: ResearchGoodinfoSnapshot | None,
    section: str,
    now: datetime,
    retry_hours: int,
) -> dict[str, Any] | str | None:
    if snapshot is None:
        return {"reason": "missing", "due": True}
    if _is_source_absent(snapshot):
        return "source_absent"
    checked = snapshot.checked_at.replace(tzinfo=timezone.utc)
    age = now - checked
    if not _snapshot_is_complete(snapshot):
        return {
            "reason": "unavailable",
            "due": age >= timedelta(hours=retry_hours),
        }
    if age >= timedelta(hours=GOODINFO_REFRESH_HOURS[section]):
        return {"reason": "stale", "due": True}
    return None


def _snapshot_is_complete(snapshot: ResearchGoodinfoSnapshot | None) -> bool:
    if snapshot is None or snapshot.status != "available":
        return False
    try:
        payload = json.loads(snapshot.payload_json or "{}")
    except json.JSONDecodeError:
        return False
    if snapshot.section == "product_mix":
        return bool(payload.get("summary"))
    return bool(payload.get("items"))


def _is_source_absent(snapshot: ResearchGoodinfoSnapshot | None) -> bool:
    return bool(snapshot and "顯示公司未申報" in (snapshot.message or ""))


def _save_run_log(result: dict[str, Any]) -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    local_day = datetime.now(ZoneInfo(settings.timezone)).date()
    summary = {key: value for key, value in result.items() if key != "browser_export_queue"}
    queue = result.get("browser_export_queue", [])
    summary["browser_export_queue"] = queue[:100]
    summary["browser_export_queue_truncated"] = max(0, len(queue) - 100)
    with SessionLocal() as session:
        session.add(
            ResearchRefreshLog(
                scope="goodinfo_backfill",
                status=str(result["status"]),
                requested_date=local_day,
                row_count=int(result.get("completed_pages", 0)),
                message=json.dumps(summary, ensure_ascii=False),
                started_at=now,
                completed_at=now,
            )
        )
        session.commit()


def _is_stock_code(value: str) -> bool:
    return len(value) == 4 and value.isdigit()
