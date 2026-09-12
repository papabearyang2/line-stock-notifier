import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.commands import build_news_digest
from app.config import get_settings
from app.db import SessionLocal
from app.line_api import push, text_message
from app.models import DeliveryLog, User
from app.providers.market import is_trading_day
from app.goodinfo_backfill import run_goodinfo_automation
from app.research_service import refresh_research_day
from app.repositories import list_codes

logger = logging.getLogger(__name__)
scheduler: AsyncIOScheduler | None = None


async def send_daily_digests() -> dict[str, int]:
    settings = get_settings()
    stats = {"sent": 0, "skipped": 0, "failed": 0}
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    if not await is_trading_day(today):
        return stats
    today_text = today.isoformat()
    with SessionLocal() as session:
        users = list(session.scalars(select(User).where(User.digest_enabled.is_(True))))
        for user in users:
            delivery_key = f"daily-news:{user.id}:{today_text}"
            exists = session.scalar(
                select(DeliveryLog.id).where(DeliveryLog.delivery_key == delivery_key)
            )
            if exists:
                stats["skipped"] += 1
                continue
            codes = list_codes(session, user)
            if not codes:
                stats["skipped"] += 1
                continue
            try:
                digest = await build_news_digest(session, codes, per_stock=2)
            except Exception:
                logger.exception("Daily digest build failed for user_id=%s", user.id)
                stats["failed"] += 1
                continue

            # Reserve the unique key before pushing. This keeps duplicate Vercel
            # Cron invocations from sending the same daily digest twice.
            delivery = DeliveryLog(
                user_id=user.id,
                delivery_key=delivery_key,
                article_count=0,
            )
            session.add(delivery)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                stats["skipped"] += 1
                continue

            try:
                await push(user.line_user_id, [text_message(digest)])
            except Exception:
                logger.exception("Daily digest push failed for user_id=%s", user.id)
                session.delete(delivery)
                session.commit()
                stats["failed"] += 1
                continue
            delivery.article_count = digest.count("\n• ")
            session.commit()
            stats["sent"] += 1
    return stats


def cleanup_artifacts() -> None:
    settings = get_settings()
    cutoff = datetime.now().timestamp() - 86400 * 7
    for path in Path(settings.resolved_artifact_dir).glob("*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            logger.warning("Could not remove artifact %s", path)


def start_scheduler() -> AsyncIOScheduler | None:
    global scheduler
    settings = get_settings()
    if not settings.enable_scheduler:
        return None
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(
        send_daily_digests,
        "cron",
        hour=settings.daily_digest_hour,
        minute=settings.daily_digest_minute,
        id="daily-digests",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        cleanup_artifacts,
        "cron",
        hour=3,
        minute=30,
        id="artifact-cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_research_day,
        "cron",
        hour=settings.research_refresh_hour,
        minute=settings.research_refresh_minute,
        id="research-market-refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if (
        settings.goodinfo_enabled
        and settings.goodinfo_backfill_enabled
        and settings.goodinfo_backfill_scheduled
    ):
        scheduler.add_job(
            run_goodinfo_automation,
            "interval",
            minutes=settings.goodinfo_backfill_interval_minutes,
            next_run_time=datetime.now(ZoneInfo(settings.timezone)) + timedelta(minutes=1),
            id="goodinfo-backfill",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    return scheduler


def stop_scheduler() -> None:
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
    scheduler = None
