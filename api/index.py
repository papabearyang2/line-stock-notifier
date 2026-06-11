import os
import re
import requests
import feedparser
import logging
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import redis
from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI()

# Configuration
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_USER_ID = os.getenv("LINE_USER_ID")
REDIS_URL = os.getenv("REDIS_URL") or "redis://redis:6379"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Initialize standard Redis client (TCP/TLS)
try:
    logger.info(f"Connecting to Redis: {REDIS_URL.split('@')[-1]}")
    redis = redis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:
    logger.error(f"Failed to connect to Redis: {e}")
    redis = None

def get_user_stock_key(user_id: str):
    return f"user:{user_id}:stocks"

def shorten_url(url: str):
    """Shorten URL using TinyURL API."""
    try:
        api_url = f"http://tinyurl.com/api-create.php?url={url}"
        response = requests.get(api_url, timeout=5)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        print(f"URL shortening failed: {e}")
    return url  # Fallback to original URL if failed

def fetch_stock_news(stock_id: str):
    """Fetch news from Google News RSS for a given stock ID, filtered by last 3 days."""
    url = f"https://news.google.com/rss/search?q={stock_id}+stock&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    feed = feedparser.parse(url)
    news_items = []
    
    # Time settings
    now_utc = datetime.now(timezone.utc)
    three_days_ago = now_utc - timedelta(days=3)
    tw_tz = timezone(timedelta(hours=8))  # Taiwan Time GMT+8

    for entry in feed.entries:
        try:
            # Parse publication date to UTC datetime
            # published_parsed is a time.struct_time in UTC
            dt_utc = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            
            # Filter: only last 3 days
            if dt_utc < three_days_ago:
                continue
                
            # Convert to Taiwan time for display
            dt_tw = dt_utc.astimezone(tw_tz)
            formatted_time = dt_tw.strftime("%Y/%m/%d %H:%M")
            
            short_link = shorten_url(entry.link)
            news_items.append(f"📌 {entry.title}\n⏰ {formatted_time} (台)\n🔗 {short_link}")
            
            if len(news_items) >= 3:  # Limit to 3 news per stock
                break
        except Exception as e:
            logger.error(f"Error parsing news entry: {e}")
            continue
            
    return news_items

@app.get("/")
async def root():
    return {"message": "Line Stock Notifier is running"}

@app.post("/api/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    body_str = body.decode("utf-8")
    
    # Log the incoming request for debugging
    logger.info(f"Incoming Webhook Body: {body_str}")
    logger.info(f"Signature: {signature}")
    
    try:
        handler.handle(body_str, signature)
    except InvalidSignatureError:
        logger.error("Invalid Signature Error")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"Webhook Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        text = event.message.text.strip()
        user_id = event.source.user_id
        key = get_user_stock_key(user_id)
        
        logger.info(f"Handling message from {user_id}: {text}")

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
        elif text == "新聞":
            stocks = redis.smembers(key)
            if not stocks:
                reply = "目前清單中沒有持股，請先新增持股（例如：新增 2330）。"
            else:
                reply = "🔍 正在為您查詢最新新聞摘要...\n"
                full_news_content = ""
                for i, stock in enumerate(stocks):
                    if i > 0:
                        full_news_content += "\n" + "─" * 15 + "\n"
                    
                    news = fetch_stock_news(stock)
                    if news:
                        full_news_content += f"\n📈 【{stock}】\n" + "\n\n".join(news) + "\n"
                    else:
                        full_news_content += f"\n📈 【{stock}】\n暫無最新相關新聞。\n"
                
                if len(full_news_content) > 4900:
                    full_news_content = full_news_content[:4897] + "..."
                reply = full_news_content
        else:
            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )
    except Exception as e:
        logger.error(f"Error in handle_message: {str(e)}", exc_info=True)

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
