import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.commands import handle_command
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.line_api import (
    bot_is_mentioned,
    conversation_id,
    reply,
    verify_signature,
    without_bot_mention,
)
from app.models import ShortLink
from app.providers.goodinfo import goodinfo_provider
from app.providers.market import market_catalog
from app.research_router import router as research_router
from app.research_service import refresh_research_day
from app.scheduler import start_scheduler, stop_scheduler
from app.scheduler import send_daily_digests
from app.storage import cleanup_published_charts

settings = get_settings()
settings.resolved_artifact_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if not settings.is_vercel:
        await market_catalog.refresh()
        start_scheduler()
    yield
    if not settings.is_vercel:
        stop_scheduler()
        await goodinfo_provider.close()


app = FastAPI(title="Taiwan Stock Research", version="0.2.0", lifespan=lifespan)
app.mount(
    "/artifacts",
    StaticFiles(directory=settings.resolved_artifact_dir),
    name="artifacts",
)
app.mount(
    "/static/research",
    StaticFiles(directory=Path(__file__).resolve().parent / "static" / "research"),
    name="research-static",
)
app.include_router(research_router)


@app.get("/", response_class=RedirectResponse)
async def root() -> RedirectResponse:
    return RedirectResponse("/research", status_code=307)


@app.get("/health")
async def health() -> dict:
    goodinfo_backfill_scheduled = bool(
        settings.goodinfo_enabled
        and settings.goodinfo_backfill_enabled
        and settings.goodinfo_backfill_scheduled
        and settings.enable_scheduler
        and not settings.is_vercel
    )
    ready = bool(
        settings.line_channel_secret
        and settings.line_channel_access_token
        and (not settings.is_vercel or settings.has_persistent_database)
        and (not settings.is_vercel or settings.blob_read_write_token)
        and (not settings.is_vercel or settings.cron_secret)
    )
    return {
        "status": "ok" if ready or not settings.is_vercel else "degraded",
        "ready": ready,
        "platform": "vercel" if settings.is_vercel else "local",
        "line_configured": bool(
            settings.line_channel_secret and settings.line_channel_access_token
        ),
        "persistent_database": settings.has_persistent_database,
        "blob_configured": bool(settings.blob_read_write_token),
        "cron_configured": bool(settings.cron_secret),
        "goodinfo_enabled": settings.goodinfo_enabled,
        "goodinfo_browser_enabled": settings.goodinfo_browser_enabled,
        "goodinfo_backfill_scheduled": goodinfo_backfill_scheduled,
        "goodinfo_backfill_manual_only": not goodinfo_backfill_scheduled,
        "goodinfo_backfill_max_pages": settings.goodinfo_backfill_max_pages,
        "goodinfo_backfill_interval_minutes": settings.goodinfo_backfill_interval_minutes,
        "goodinfo_pilot_stock_code": settings.goodinfo_pilot_stock_code,
        "goodinfo_rollout_enabled": settings.goodinfo_rollout_enabled,
        "scheduler_enabled": settings.enable_scheduler and not settings.is_vercel,
        "dashboard_configured": bool(
            settings.dashboard_password and settings.dashboard_session_secret
        ),
    }


@app.get("/n/{slug}", response_class=RedirectResponse)
def open_news_link(slug: str) -> RedirectResponse:
    with SessionLocal() as session:
        link = session.get(ShortLink, slug)
    if not link:
        raise HTTPException(status_code=404, detail="Short link not found")
    return RedirectResponse(link.url, status_code=302)


@app.get("/api/cron/daily-digest")
async def daily_digest_cron(request: Request) -> dict:
    authorization = request.headers.get("authorization", "")
    expected = f"Bearer {settings.cron_secret}"
    if not settings.cron_secret or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")
    digest_stats = await send_daily_digests()
    deleted_charts = await cleanup_published_charts()
    return {"ok": True, **digest_stats, "deleted_charts": deleted_charts}


@app.get("/api/cron/market-refresh")
async def market_refresh_cron(request: Request) -> dict:
    authorization = request.headers.get("authorization", "")
    expected = f"Bearer {settings.cron_secret}"
    if not settings.cron_secret or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")
    result = await refresh_research_day()
    return {"ok": result["status"] in {"success", "partial"}, **result}


@app.post("/webhook")
async def webhook(request: Request) -> JSONResponse:
    raw_body = await request.body()
    signature = request.headers.get("x-line-signature", "")
    if not signature or not verify_signature(raw_body, signature):
        raise HTTPException(status_code=400, detail="Invalid LINE signature")
    payload = await request.json()
    for event in payload.get("events", []):
        message = event.get("message", {})
        if event.get("type") != "message" or message.get("type") != "text":
            continue
        source = event.get("source", {})
        if source.get("type") in {"group", "room"} and not bot_is_mentioned(message):
            continue
        line_conversation_id = conversation_id(source)
        reply_token = event.get("replyToken")
        if not line_conversation_id or not reply_token:
            continue
        try:
            with SessionLocal() as session:
                result = await handle_command(
                    session, line_conversation_id, without_bot_mention(message)
                )
            if result.messages:
                await reply(reply_token, result.messages)
        except Exception:
            logger.exception("Webhook command failed")
            try:
                await reply(
                    reply_token,
                    [{"type": "text", "text": "服務暫時發生錯誤，請稍後再試。"}],
                )
            except Exception:
                logger.exception("Could not send error reply")
    return JSONResponse({"ok": True})
