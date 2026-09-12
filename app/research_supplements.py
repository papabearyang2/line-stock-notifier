"""Persisted, rate-limited Goodinfo supplements for the research page."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import ResearchGoodinfoSnapshot


GOODINFO_SECTIONS = (
    "monthly_revenue",
    "eps",
    "cash_flow",
    "pe",
    "dividends",
    "product_mix",
)
GOODINFO_IMPORT_PAGES = (
    "monthly_revenue",
    "cash_flow",
    "dividends",
    "pe",
    "product_mix",
)


def goodinfo_source_url(stock_code: str, section: str) -> str:
    paths = {
        "monthly_revenue": f"/ShowSaleMonChart.asp?STOCK_ID={stock_code}",
        "eps": f"/StockCashFlow.asp?STOCK_ID={stock_code}",
        "cash_flow": f"/StockCashFlow.asp?STOCK_ID={stock_code}",
        "pe": f"/ShowK_ChartFlow.asp?RPT_CAT=PER&STOCK_ID={stock_code}",
        "dividends": f"/StockDividendPolicy.asp?STOCK_ID={stock_code}",
        "product_mix": f"/ShowSaleMonProdChart.asp?STOCK_ID={stock_code}",
    }
    return f"https://goodinfo.tw/tw{paths.get(section, '')}"


def read_goodinfo_snapshot(stock_code: str, section: str) -> dict[str, Any] | None:
    """Read one persisted supplement without contacting Goodinfo."""
    init_db()
    try:
        with SessionLocal() as session:
            row = session.scalar(
                select(ResearchGoodinfoSnapshot).where(
                    ResearchGoodinfoSnapshot.stock_code == str(stock_code),
                    ResearchGoodinfoSnapshot.section == section,
                )
            )
            if row is None:
                return None
            try:
                payload = json.loads(row.payload_json or "{}")
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            checked_at = row.checked_at.replace(tzinfo=timezone.utc).isoformat()
            result = {
                **payload,
                "status": row.status,
                "source": row.source_name,
                "source_url": row.source_url,
                "last_checked": checked_at,
                "last_verified": checked_at,
            }
            if row.message and not result.get("reason"):
                result["reason"] = row.message
            return result
    except SQLAlchemyError:
        return None


def save_goodinfo_snapshot(
    stock_code: str,
    section: str,
    payload: dict[str, Any],
    *,
    checked_at: datetime | None = None,
) -> None:
    """Upsert a supplement, keeping the most recent period first in its JSON payload."""
    if section not in GOODINFO_SECTIONS:
        raise ValueError(f"unknown Goodinfo section: {section}")
    init_db()
    checked = (checked_at or datetime.now(timezone.utc)).replace(tzinfo=None)
    data = _recent_first_payload(payload)
    status = str(data.get("status") or ("available" if data.get("items") else "unavailable"))
    source_name = str(data.get("source") or "Goodinfo（補充）")
    source_url = str(data.get("source_url") or goodinfo_source_url(stock_code, section))
    message = str(data.get("reason") or data.get("message") or "")
    with SessionLocal() as session:
        row = session.scalar(
            select(ResearchGoodinfoSnapshot).where(
                ResearchGoodinfoSnapshot.stock_code == str(stock_code),
                ResearchGoodinfoSnapshot.section == section,
            )
        )
        if row is None:
            row = ResearchGoodinfoSnapshot(
                stock_code=str(stock_code),
                section=section,
                status=status,
                payload_json=json.dumps(data, ensure_ascii=False),
                source_name=source_name,
                source_url=source_url,
                message=message,
                checked_at=checked,
            )
            session.add(row)
        else:
            row.status = status
            row.payload_json = json.dumps(data, ensure_ascii=False)
            row.source_name = source_name
            row.source_url = source_url
            row.message = message
            row.checked_at = checked
        session.commit()


def goodinfo_snapshot_is_fresh(snapshot: dict[str, Any] | None) -> bool:
    """Return whether a stored result is inside the configured Goodinfo TTL."""
    if not snapshot:
        return False
    value = snapshot.get("last_checked")
    if not value:
        return False
    try:
        checked = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked <= timedelta(hours=get_settings().goodinfo_cache_hours)


def import_goodinfo_html(
    stock_code: str,
    page: str,
    html: str,
    *,
    years: int = 5,
) -> dict[str, Any]:
    """Parse a browser-exported Goodinfo page and save only verified, non-empty data."""
    if page not in GOODINFO_IMPORT_PAGES:
        raise ValueError(f"unknown Goodinfo import page: {page}")
    if years < 1:
        raise ValueError("years must be positive")

    from bs4 import BeautifulSoup

    from app.providers.goodinfo import GoodinfoProvider

    soup = BeautifulSoup(html, "html.parser")
    page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    if "Goodinfo" not in page_text or not re.search(rf"(?<!\d){re.escape(stock_code)}(?!\d)", page_text):
        raise ValueError("匯入檔不是指定股票的 Goodinfo 頁面")

    fingerprint = hashlib.sha256(html.encode("utf-8")).hexdigest()
    common = {
        "source": "Goodinfo（瀏覽器匯出）",
        "import_method": "browser_exported_html",
        "content_sha256": fingerprint,
    }
    payloads: dict[str, dict[str, Any]] = {}

    if page == "monthly_revenue":
        items = GoodinfoProvider._parse_monthly_revenue_history(soup, years * 12)
        payloads["monthly_revenue"] = _import_payload(
            stock_code, "monthly_revenue", items, common
        )
    elif page == "cash_flow":
        rows = GoodinfoProvider._parse_cash_flow_history(soup, years)
        eps = [
            {
                "period": row["period"],
                "single_quarter_eps": None,
                "ttm_eps": row["eps"],
                "ttm_status": "source_reported_annual",
                "type": "Goodinfo 年度公開值",
            }
            for row in rows
            if row.get("eps") is not None
        ]
        cash_flow = [
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
        payloads["eps"] = _import_payload(stock_code, "eps", eps, common)
        payloads["cash_flow"] = _import_payload(stock_code, "cash_flow", cash_flow, common)
    elif page == "dividends":
        items = GoodinfoProvider._parse_dividend_policy(soup, years)
        payloads["dividends"] = _import_payload(stock_code, "dividends", items, common)
    elif page == "pe":
        frame = GoodinfoProvider._parse_valuation_table(soup)
        items = [
            {"date": row.date.date().isoformat(), "pe": float(row.pe)}
            for row in frame.itertuples(index=False)
        ]
        payloads["pe"] = _import_payload(stock_code, "pe", items, common)
    else:
        parsed = GoodinfoProvider._parse_product_mix_summary(soup)
        if parsed.get("reason") == "Goodinfo 未提供產品／業務營收拆分":
            raise ValueError("Goodinfo 產品營收表格式無法辨識")
        payloads["product_mix"] = {
            **common,
            **parsed,
            "source_url": goodinfo_source_url(stock_code, "product_mix"),
        }

    for section, payload in payloads.items():
        save_goodinfo_snapshot(stock_code, section, payload)
    return {
        "status": "success",
        "stock_code": str(stock_code),
        "page": page,
        "sections": {
            section: {
                "status": payload["status"],
                "items": len(payload.get("items") or []),
            }
            for section, payload in payloads.items()
        },
        "content_sha256": fingerprint,
    }


def _import_payload(
    stock_code: str,
    section: str,
    items: list[dict[str, Any]],
    common: dict[str, Any],
) -> dict[str, Any]:
    if not items:
        raise ValueError(f"Goodinfo {section} 匯出檔沒有可辨識資料")
    return {
        **common,
        "status": "available",
        "items": items,
        "source_url": goodinfo_source_url(stock_code, section),
    }


async def backfill_goodinfo_stock(
    stock_code: str,
    *,
    years: int = 5,
    force: bool = False,
    sections: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Fill only missing/stale sections, one stock at a time.

    The Goodinfo provider owns the shared lock, HTML cache, and minimum interval.
    This function deliberately runs requests serially; persisted rows are written
    after each source group so a later run can continue from the recent data first.
    """
    settings = get_settings()
    requested = tuple(sections or GOODINFO_SECTIONS)
    invalid = set(requested) - set(GOODINFO_SECTIONS)
    if invalid:
        raise ValueError(f"unknown Goodinfo sections: {sorted(invalid)}")
    if years < 1:
        raise ValueError("years must be positive")
    if not settings.goodinfo_enabled:
        return {"status": "disabled", "stock_code": str(stock_code), "sections": {}}

    pending: list[str] = []
    skipped: list[str] = []
    for section in requested:
        snapshot = read_goodinfo_snapshot(stock_code, section)
        if not force and goodinfo_snapshot_is_fresh(snapshot):
            skipped.append(section)
        else:
            pending.append(section)

    # Import lazily so callers that only render stored data never initialize the
    # network adapter as part of their import path.
    from app.providers.goodinfo import goodinfo_provider

    result: dict[str, Any] = {
        "status": "success",
        "stock_code": str(stock_code),
        "years": years,
        "order": "recent_first",
        "skipped": skipped,
        "sections": {},
    }
    financial_pending = set(pending) & {"eps", "cash_flow", "monthly_revenue", "pe"}
    if financial_pending:
        try:
            financial = await goodinfo_provider.financial_fallback(
                str(stock_code), financial_pending, years=years
            )
        except Exception as exc:  # preserve an auditable unavailable result
            financial = {}
            error = f"Goodinfo 補庫失敗：{type(exc).__name__}"
        else:
            error = ""
        for section in sorted(financial_pending, key=_recent_section_order):
            items = list(financial.get(section) or [])
            payload = {
                "status": "available" if items else "unavailable",
                "items": items,
                "source": "Goodinfo（補充）",
                "source_url": goodinfo_source_url(str(stock_code), section),
                "reason": error or (
                    "Goodinfo 未取得可解析資料（可能要求驗證或拒絕自動存取）"
                    if not items
                    else ""
                ),
            }
            save_goodinfo_snapshot(str(stock_code), section, payload)
            result["sections"][section] = payload["status"]

    if "dividends" in pending:
        try:
            items = await goodinfo_provider.dividend_fallback(str(stock_code), years=years)
            error = ""
        except Exception as exc:
            items, error = [], f"Goodinfo 補庫失敗：{type(exc).__name__}"
        payload = {
            "status": "available" if items else "unavailable",
            "items": list(items),
            "source": "Goodinfo（補充）",
            "source_url": goodinfo_source_url(str(stock_code), "dividends"),
            "reason": error or (
                "Goodinfo 未取得可解析資料（可能要求驗證或拒絕自動存取）"
                if not items
                else ""
            ),
        }
        save_goodinfo_snapshot(str(stock_code), "dividends", payload)
        result["sections"]["dividends"] = payload["status"]

    if "product_mix" in pending:
        try:
            payload = await goodinfo_provider.product_mix_summary(str(stock_code))
        except Exception as exc:
            payload = {
                "status": "unavailable",
                "source": "Goodinfo（補充）",
                "source_url": goodinfo_source_url(str(stock_code), "product_mix"),
                "reason": f"Goodinfo 補庫失敗：{type(exc).__name__}",
            }
        save_goodinfo_snapshot(str(stock_code), "product_mix", payload)
        result["sections"]["product_mix"] = payload.get("status", "unavailable")

    if any(status == "unavailable" for status in result["sections"].values()):
        result["status"] = "partial"
    return result


def _recent_section_order(section: str) -> int:
    return {"monthly_revenue": 0, "eps": 1, "cash_flow": 2, "pe": 3}.get(section, 9)


def _recent_first_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    items = result.get("items")
    if isinstance(items, list) and all(isinstance(item, dict) for item in items):
        result["items"] = sorted(items, key=_item_period, reverse=True)
    return result


def _item_period(item: dict[str, Any]) -> str:
    for key in ("period", "month", "date", "year", "announcement_date"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""
