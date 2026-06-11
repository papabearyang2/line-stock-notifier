import os
import re
import feedparser
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from upstash_redis import Redis
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Configuration
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.getenv("LINE_USER_ID")
KV_URL = os.getenv("KV_REST_API_URL")
KV_TOKEN = os.getenv("KV_REST_API_TOKEN")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
redis = Redis(url=KV_URL, token=KV_TOKEN)

def get_user_stock_key(user_id: str):
    return f"user:{user_id}:stocks"

def fetch_stock_news(stock_id: str):
    """Fetch news from Google News RSS for a given stock ID."""
    url = f"https://news.google.com/rss/search?q={stock_id}+stock&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    feed = feedparser.parse(url)
    news_items = []
    for entry in feed.entries[:3]:  # Get top 3 news
        news_items.append(f"📌 {entry.title}\n🔗 {entry.link}")
    return news_items

@app.get("/")
async def root():
    return {"message": "Line Stock Notifier is running"}

@app.post("/api/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    key = get_user_stock_key(user_id)

    if text.startswith("新增") or text.startswith("+"):
        stock_id = re.sub(r"^(新增|\+)\s*", "", text)
        if stock_id:
            redis.sadd(key, stock_id)
            reply = f"✅ 已新增持股：{stock_id}"
        else:
            reply = "請輸入正確格式，例如：新增 2330"
    elif text.startswith("刪除") or text.startswith("-"):
        stock_id = re.sub(r"^(刪除|\-)\s*", "", text)
        if stock_id:
            redis.srem(key, stock_id)
            reply = f"❌ 已刪除持股：{stock_id}"
        else:
            reply = "請輸入正確格式，例如：刪除 2330"
    elif text in ["清單", "我的持股", "查詢"]:
        stocks = redis.smembers(key)
        if stocks:
            reply = "📊 您的目前持股清單：\n" + "\n".join(stocks)
        else:
            reply = "目前清單中沒有持股。"
    else:
        # Ignore other messages or provide help
        return

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

@app.get("/api/cron")
async def daily_news_cron(background_tasks: BackgroundTasks):
    """
    Triggered by Vercel Cron.
    Fetches news for all stocks of the target user and sends to Line.
    """
    if not LINE_USER_ID:
        return {"status": "error", "message": "LINE_USER_ID not set"}

    key = get_user_stock_key(LINE_USER_ID)
    stocks = redis.smembers(key)

    if not stocks:
        return {"status": "success", "message": "No stocks to notify"}

    background_tasks.add_task(send_news_notifications, LINE_USER_ID, stocks)
    return {"status": "success", "message": f"Processing news for {len(stocks)} stocks"}

def send_news_notifications(user_id: str, stocks: list):
    full_message = "📢 早上好！這是您的持股今日新聞摘要：\n"
    
    for stock in stocks:
        news = fetch_stock_news(stock)
        if news:
            full_message += f"\n📈 【{stock}】\n" + "\n\n".join(news) + "\n"
        else:
            full_message += f"\n📈 【{stock}】\n暫無最新相關新聞。\n"
    
    # Line message has a 5000 character limit, but news list might be long.
    # Split if necessary, but here we assume it fits or simple truncation.
    if len(full_message) > 5000:
        full_message = full_message[:4997] + "..."

    line_bot_api.push_message(
        user_id,
        TextSendMessage(text=full_message)
    )
