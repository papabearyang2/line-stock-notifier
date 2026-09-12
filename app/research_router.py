"""Private web and JSON routes for the research dashboard."""

from __future__ import annotations

import asyncio
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from app.config import get_settings
from app.providers.company_research import company_research_provider
from app.research_auth import create_session_token, verify_dashboard_password, verify_session_token
from app.research_service import latest_refresh_status, overview, stock_snapshot
from app.research_supplements import read_goodinfo_snapshot


router = APIRouter()
settings = get_settings()
STATIC_DIR = Path(__file__).resolve().parent / "static" / "research"
SESSION_COOKIE = "stock_research_session"


def _require_session(request: Request) -> None:
    if not settings.dashboard_password or not settings.dashboard_session_secret:
        raise HTTPException(status_code=503, detail="私人研究網站尚未設定登入密碼")
    if not verify_session_token(request.cookies.get(SESSION_COOKIE), settings.dashboard_session_secret):
        raise HTTPException(status_code=401, detail="請先登入")


@router.get("/research/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/research") -> Response:
    if verify_session_token(request.cookies.get(SESSION_COOKIE), settings.dashboard_session_secret):
        return RedirectResponse(_safe_next(next), status_code=303)
    configured = bool(settings.dashboard_password and settings.dashboard_session_secret)
    warning = "" if configured else "網站管理者尚未設定私人登入環境變數。"
    return HTMLResponse(_login_html(_safe_next(next), warning), status_code=200 if configured else 503)


@router.post("/research/login")
async def login(request: Request) -> Response:
    if not settings.dashboard_password or not settings.dashboard_session_secret:
        return HTMLResponse(_login_html("/research", "網站管理者尚未設定私人登入環境變數。"), status_code=503)
    body = (await request.body()).decode("utf-8", errors="replace")
    form = parse_qs(body, keep_blank_values=True)
    password = (form.get("password") or [""])[0]
    next_path = _safe_next((form.get("next") or ["/research"])[0])
    if not verify_dashboard_password(password, settings.dashboard_password):
        return HTMLResponse(_login_html(next_path, "密碼不正確。"), status_code=401)
    ttl = settings.dashboard_session_hours * 60 * 60
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(settings.dashboard_session_secret, ttl_seconds=ttl),
        max_age=ttl,
        httponly=True,
        secure=settings.is_vercel or settings.public_base_url.startswith("https://"),
        samesite="lax",
        path="/",
    )
    return response


@router.post("/research/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/research/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/research")
@router.get("/research/stocks/{stock_code}")
async def research_page(request: Request, stock_code: str | None = None) -> Response:
    if not verify_session_token(request.cookies.get(SESSION_COOKIE), settings.dashboard_session_secret):
        destination = f"/research/stocks/{stock_code}" if stock_code else "/research"
        return RedirectResponse(f"/research/login?next={destination}", status_code=303)
    return FileResponse(
        STATIC_DIR / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/research/status")
async def research_status(request: Request) -> dict:
    _require_session(request)
    return latest_refresh_status()


@router.get("/api/research/overview")
async def research_overview(
    request: Request,
    window: int = Query(default=20),
    weight: str = Query(default="market_cap"),
    view: str = Query(default="industry"),
    group: str | None = Query(default=None, max_length=80),
) -> dict:
    _require_session(request)
    try:
        return await asyncio.to_thread(
            overview,
            window=window,
            weight=weight,
            view=view,
            group=group,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/research/stocks/{stock_code}")
async def research_stock(
    request: Request,
    stock_code: str,
    window: int = Query(default=20),
    news_days: int = Query(default=30),
) -> dict:
    _require_session(request)
    if not stock_code.isdigit() or len(stock_code) != 4:
        raise HTTPException(status_code=404, detail="找不到個股")
    if news_days not in {7, 30, 90}:
        raise HTTPException(status_code=422, detail="news_days must be 7, 30 or 90")
    try:
        snapshot = await asyncio.to_thread(stock_snapshot, stock_code, window=window)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="找不到個股或尚無行情資料")
    product_mix = snapshot.get("product_mix")
    if isinstance(product_mix, dict) and not product_mix.get("items"):
        goodinfo_mix = read_goodinfo_snapshot(stock_code, "product_mix")
        if goodinfo_mix is None:
            goodinfo_mix = {}
        if goodinfo_mix:
            product_mix = {**product_mix, "goodinfo": goodinfo_mix}
            source = {
                "name": goodinfo_mix.get("source", "Goodinfo（補充）"),
                "url": goodinfo_mix.get("source_url"),
                "last_verified": goodinfo_mix.get("last_checked"),
                "type": "secondary_html",
                "status": goodinfo_mix.get("status"),
                "note": goodinfo_mix.get("reason"),
            }
            product_mix["sources"] = [*product_mix.get("sources", []), source]
            if goodinfo_mix.get("status") == "available":
                product_mix["status"] = "partial"
            snapshot["product_mix"] = product_mix
    financials, dividends, news = await asyncio.gather(
        company_research_provider.financials(stock_code, years=5),
        company_research_provider.dividends(stock_code, years=5),
        company_research_provider.news(stock_code, days=news_days),
    )
    return {**snapshot, "financials": financials, "dividends": dividends, "news": news}


def _safe_next(value: str) -> str:
    return value if value.startswith("/research") and not value.startswith("//") else "/research"


def _login_html(next_path: str, warning: str) -> str:
    warning_html = (
        f'<p class="warning" role="alert">{escape(warning)}</p>' if warning else ""
    )
    escaped_next = escape(next_path, quote=True)
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>私人台股研究</title><style>
:root {{ color-scheme: dark; font-family: system-ui, sans-serif; }}
* {{ box-sizing: border-box; }} body {{ margin: 0; min-height: 100vh; display: grid;
place-items: center; background: #09111f; color: #ecf3ff; padding: 24px; }}
main {{ width: min(420px, 100%); background: #111d30; border: 1px solid #2a3b55;
border-radius: 18px; padding: 30px; box-shadow: 0 24px 80px #0008; }}
.eyebrow {{ color: #75c7ff; font-weight: 700; letter-spacing: .08em; }}
h1 {{ margin: 8px 0; font-size: 1.8rem; }} p {{ color: #aebed3; line-height: 1.6; }}
label {{ display: block; font-weight: 700; margin: 24px 0 8px; }}
input,button {{ width: 100%; min-height: 48px; border-radius: 10px; font: inherit; }}
input {{ border: 1px solid #50647f; background: #09111f; color: white; padding: 0 14px; }}
button {{ border: 0; margin-top: 14px; background: #36a3ff; color: #04101e; font-weight: 800;
cursor: pointer; }} button:focus-visible,input:focus-visible {{ outline: 3px solid #ffd166; outline-offset: 3px; }}
.warning {{ color: #ffd5a5; background: #392718; padding: 10px 12px; border-radius: 8px; }}
</style></head><body><main><div class="eyebrow">PRIVATE RESEARCH</div>
<h1>台股交易重心觀測</h1><p>此頁只供擁有者登入。資料僅供研究，不構成投資建議。</p>
{warning_html}<form method="post" action="/research/login">
<input type="hidden" name="next" value="{escaped_next}"><label for="password">密碼</label>
<input id="password" name="password" type="password" required autofocus autocomplete="current-password">
<button type="submit">登入</button></form></main></body></html>"""
