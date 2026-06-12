import os
import re
import requests
import time
import random
import logging
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, HTTPException
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
    """
    Shorten URL using CleanURI API.
    Falls back to original URL on any error to ensure message delivery.
    """
    try:
        api_url = "https://cleanuri.com/api/v1/shorten"
        response = requests.post(api_url, data={"url": url}, timeout=5)
        
        if response.status_code == 200:
            result_json = response.json()
            short_url = result_json.get("result_url")
            if short_url and short_url.startswith("http"):
                return short_url
        
        logger.warning(f"CleanURI shortening failed for {url}. Status: {response.status_code}")
    except Exception as e:
        logger.error(f"URL shortening request failed: {e}")
    
    return url  # Fallback to original URL

def fetch_stock_news(stock_id: str):
    """Scrape stock announcements from Goodinfo for a given stock ID, filtered by last 3 days."""
    tw_tz = timezone(timedelta(hours=8))
    now_tw = datetime.now(tw_tz)
    # Query for the last 7 days to ensure we have enough overlap for the 3-day filter
    start_dt = (now_tw - timedelta(days=7)).strftime("%Y/%m/%d")
    end_dt = now_tw.strftime("%Y/%m/%d")
    
    url = f"https://goodinfo.tw/tw/StockAnnounceList.asp?START_DT={start_dt}&END_DT={end_dt}&STOCK_ID={stock_id}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://goodinfo.tw/tw/index.asp"
    }
    
    news_items = []
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = "utf-8"
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch Goodinfo: {response.status_code}")
            return []
            
        soup = BeautifulSoup(response.text, "lxml")
        table = soup.find("table", {"id": "tblAnnounceList"})
        
        if not table:
            logger.warning(f"Announcement table not found for {stock_id}")
            return []
            
        rows = table.find_all("tr")
        three_days_ago = now_tw - timedelta(days=3)
        
        # Skip header row
        for row in rows[1:]:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue
                
            date_str = cols[0].text.strip() # YYYY/MM/DD
            time_str = cols[1].text.strip() # HH:MM:SS
            title = cols[3].text.strip()
            link_tag = cols[3].find("a")
            
            if not link_tag:
                continue
                
            link = "https://goodinfo.tw/tw/" + link_tag["href"]
            
            # Parse full datetime for filtering
            try:
                full_dt_str = f"{date_str} {time_str}"
                dt_obj = datetime.strptime(full_dt_str, "%Y/%m/%d %H:%M:%S").replace(tzinfo=tw_tz)
                
                # Filter: only last 3 days
                if dt_obj < three_days_ago:
                    continue
                    
                formatted_time = dt_obj.strftime("%Y/%m/%d %H:%M")
                short_link = shorten_url(link)
                news_items.append(f"📌 {title}\n⏰ {formatted_time} (台)\n🔗 {short_link}")
                
                if len(news_items) >= 3:
                    break
            except Exception as e:
                logger.error(f"Error parsing date {date_str} {time_str}: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Scraping Goodinfo failed for {stock_id}: {e}")
        
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
                # Query synchronously as it is a direct reply
                full_news_content = build_news_message(stocks)
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
async def daily_news_cron():
    """
    Triggered by Vercel Cron.
    Synchronously fetches and sends news to ensure Vercel doesn't freeze the task.
    """
    if not LINE_USER_ID:
        return {"status": "error", "message": "LINE_USER_ID not set"}

    key = get_user_stock_key(LINE_USER_ID)
    stocks = redis.smembers(key)

    if not stocks:
        return {"status": "success", "message": "No stocks to notify"}

    # Process synchronously on Vercel to avoid suspension
    try:
        send_news_notifications(LINE_USER_ID, stocks)
        return {"status": "success", "message": f"Successfully sent news for {len(stocks)} stocks"}
    except Exception as e:
        logger.error(f"Cron execution failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def build_news_message(stocks: list):
    """Helper to construct the news message string."""
    full_message = ""
    for i, stock in enumerate(stocks):
        if i > 0:
            full_message += "\n" + "─" * 15 + "\n"
            # Anti-429: Add a small delay between requests to Goodinfo
            time.sleep(random.uniform(1.5, 3.0))
        
        news = fetch_stock_news(stock)
        if news:
            full_message += f"\n📈 【{stock}】\n" + "\n\n".join(news) + "\n"
        else:
            full_message += f"\n📈 【{stock}】\n暫無 3 天內最新重大訊息。\n"
    
    if len(full_message) > 5000:
        full_message = full_message[:4997] + "..."
    return full_message

def send_news_notifications(user_id: str, stocks: list):
    header = "📢 早上好！這是您的持股今日重大訊息摘要：\n"
    content = build_news_message(stocks)
    full_message = header + content
    
    line_bot_api.push_message(
        user_id,
        TextSendMessage(text=full_message)
    )
