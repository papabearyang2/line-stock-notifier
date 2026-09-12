import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.charts import gross_margin_chart, pe_river_chart
from app.config import get_settings
from app.line_api import image_message, text_message
from app.profiles import industry_research, profit_sources
from app.providers.finmind import FinMindError
from app.providers.market import Company, market_catalog
from app.providers.news import Article, news_provider
from app.repositories import (
    add_codes,
    get_or_create_user,
    list_codes,
    remove_codes,
    save_short_links,
)
from app.storage import publish_chart

CODE_RE = re.compile(r"(?<![\d/])(\d{4,6})(?![\d/])")
NAME_ARGUMENT_COMMANDS = {
    "新增",
    "加入",
    "add",
    "刪除",
    "移除",
    "delete",
    "remove",
    "新聞",
    "news",
    "毛利率",
    "毛利",
    "gross",
    "本益比",
    "河流圖",
    "pe",
    "產業",
    "同業",
    "上下游",
    "供應鏈",
    "獲利",
    "獲利來源",
    "營收來源",
    "產品",
}
REPLY_KEYWORDS = NAME_ARGUMENT_COMMANDS | {
    "help",
    "說明",
    "功能",
    "指令",
    "開始",
    "清單",
    "觀察清單",
    "watchlist",
    "訂閱摘要",
    "訂閱",
    "subscribe",
    "取消摘要",
    "取消訂閱",
    "unsubscribe",
}

HELP = """台股研究 Bot 指令

清單
新增 2330 2454
新增 台積電 聯發科
刪除 2454
新聞（清單內全部，近三天）
新聞 2330
新聞 台積電
毛利率 2330
本益比 2330
產業 2330
獲利 2330
訂閱摘要
取消摘要

資料僅供研究，不構成投資建議。"""


@dataclass
class CommandResult:
    messages: list[dict]


def extract_codes(text: str) -> list[str]:
    return list(dict.fromkeys(CODE_RE.findall(text)))


async def handle_command(session: Session, conversation_id: str, raw_text: str) -> CommandResult:
    text = re.sub(r"\s+", " ", raw_text.strip())
    keyword = text.split(" ", 1)[0].lower()
    if keyword not in REPLY_KEYWORDS:
        return CommandResult([])

    codes = extract_codes(text)
    if not codes and " " in text and keyword in NAME_ARGUMENT_COMMANDS:
        codes = await _resolve_company_names(text)
    user = get_or_create_user(session, conversation_id)

    if keyword in {"help", "說明", "功能", "指令", "開始"}:
        return _text(HELP)
    if keyword in {"清單", "觀察清單", "watchlist"}:
        current = list_codes(session, user)
        current_labels = await _stock_labels(current)
        return _text(
            "此聊天室觀察清單："
            + ("、".join(current_labels) if current_labels else "目前是空的")
        )
    if keyword in {"新增", "加入", "add"}:
        if not codes:
            return _text("請輸入股票代號或名稱，例如：新增 2330 台積電")
        added, skipped = add_codes(session, user, codes)
        added_labels = await _stock_labels(added)
        skipped_labels = await _stock_labels(skipped)
        lines = [f"已新增：{'、'.join(added_labels)}" if added else "沒有新增股票"]
        if skipped:
            lines.append(f"已在清單：{'、'.join(skipped_labels)}")
        return _text("\n".join(lines))
    if keyword in {"刪除", "移除", "delete", "remove"}:
        if not codes:
            return _text("請輸入股票代號或名稱，例如：刪除 2454 聯發科")
        removed = remove_codes(session, user, codes)
        removed_labels = await _stock_labels(removed)
        return _text(
            f"已移除：{'、'.join(removed_labels)}" if removed else "指定股票不在清單內"
        )
    if keyword in {"新聞", "news"}:
        targets = codes or list_codes(session, user)
        if not targets:
            return _text("此聊天室的觀察清單是空的；請先用「新增 2330」加入股票。")
        return _text(await build_news_digest(session, targets))
    if keyword in {"毛利率", "毛利", "gross"}:
        return await _chart_command(codes, gross_margin_chart, "毛利率")
    if keyword in {"本益比", "河流圖", "pe"}:
        return await _chart_command(codes, pe_river_chart, "本益比河流圖")
    if keyword in {"產業", "同業", "上下游", "供應鏈"}:
        if not codes:
            return _text("請輸入股票代號，例如：產業 2330")
        return _text(await industry_research(codes[0]))
    if keyword in {"獲利", "獲利來源", "營收來源", "產品"}:
        if not codes:
            return _text("請輸入股票代號，例如：獲利 2330")
        return _text(await profit_sources(codes[0]))
    if keyword in {"訂閱摘要", "訂閱", "subscribe"}:
        user.digest_enabled = True
        session.commit()
        settings = get_settings()
        return _text(
            f"已開啟此聊天室的每日新聞摘要，預計每天 {settings.daily_digest_hour:02d}:"
            f"{settings.daily_digest_minute:02d} 推送。"
        )
    if keyword in {"取消摘要", "取消訂閱", "unsubscribe"}:
        user.digest_enabled = False
        session.commit()
        return _text("已關閉此聊天室的每日新聞摘要。")
    return CommandResult([])


async def build_news_digest(
    session: Session, codes: list[str], per_stock: int = 3
) -> str:
    settings = get_settings()
    targets = codes[:20]
    results = await asyncio.gather(
        *(
            news_provider.get_stock_news(
                code, days=settings.news_lookback_days, limit=per_stock
            )
            for code in targets
        ),
        return_exceptions=True,
    )
    companies = await market_catalog.get_many(targets)
    articles = [
        article
        for result in results
        if not isinstance(result, Exception)
        for article in result
    ]
    short_links = save_short_links(session, [article.url for article in articles])
    lines = [f"近 {settings.news_lookback_days} 天新聞整理"]
    for code, result in zip(targets, results, strict=True):
        lines.append(f"\n【{_stock_label(code, companies.get(code))}】")
        if isinstance(result, Exception):
            lines.append("暫時無法取得新聞")
            continue
        articles: list[Article] = result
        if not articles:
            lines.append("沒有找到相關新聞")
            continue
        for article in articles:
            published = _format_published_at(article.published_at, settings.timezone)
            source = f"｜{article.source}" if article.source else ""
            url = f"{settings.public_base_url}/n/{short_links[article.url]}"
            lines.append(f"• {published}{source}\n{article.title}\n{url}")
    if len(codes) > len(targets):
        lines.append(f"\n本次先處理前 {len(targets)} 檔，避免訊息超過 LINE 長度限制。")
    return "\n".join(lines)[:5000]


def _format_published_at(published_at: datetime | None, timezone_name: str) -> str:
    if published_at is None:
        return "日期未知"
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_timezone = ZoneInfo("Asia/Taipei")
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=local_timezone)
    return published_at.astimezone(local_timezone).strftime("%Y-%m-%d %H:%M")


async def _resolve_company_names(text: str) -> list[str]:
    if " " not in text:
        return []
    names = re.split(r"[\s,，、]+", text.split(" ", 1)[1].strip())
    resolved: list[str] = []
    for name in names:
        if not name:
            continue
        company = await market_catalog.find_by_name(name)
        if company and company.code not in resolved:
            resolved.append(company.code)
    return resolved


def _stock_label(code: str, company: Company | None) -> str:
    return f"{code} {company.name}" if company else code


async def _stock_labels(codes: list[str]) -> list[str]:
    companies = await market_catalog.get_many(codes)
    return [_stock_label(code, companies.get(code)) for code in codes]


async def _chart_command(codes: list[str], chart_fn, label: str) -> CommandResult:
    if not codes:
        return _text(f"請輸入股票代號，例如：{label} 2330")
    try:
        path, source = await chart_fn(codes[0])
    except (FinMindError, ValueError) as exc:
        return _text(f"{label}產生失敗：{exc}")
    except Exception:
        return _text(f"{label}目前無法取得，請稍後再試。")
    try:
        url = await publish_chart(path)
    except Exception as exc:
        return _text(f"{label}圖片發布失敗：{exc}")
    company = await market_catalog.get(codes[0])
    return CommandResult(
        [
            text_message(
                f"{_stock_label(codes[0], company)} {label}\n資料來源：{source}"
            ),
            image_message(url),
        ]
    )


def _text(value: str) -> CommandResult:
    return CommandResult([text_message(value)])
