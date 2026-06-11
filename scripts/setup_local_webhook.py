import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

def update_line_webhook():
    print("等待 ngrok 啟動...")
    # Give ngrok a few seconds to start
    time.sleep(5)
    
    try:
        # Get ngrok public URL from local ngrok API
        response = requests.get("http://localhost:4040/api/tunnels")
        tunnels = response.json().get("tunnels", [])
        public_url = ""
        for tunnel in tunnels:
            if tunnel.get("proto") == "https":
                public_url = tunnel.get("public_url")
                break
        
        if not public_url:
            print("❌ 找不到 ngrok 公開網址")
            return

        webhook_url = f"{public_url}/api/webhook"
        print(f"🚀 取得 ngrok 網址: {webhook_url}")

        # Update LINE Webhook URL via LINE API
        headers = {
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        data = {"endpoint": webhook_url}
        
        line_response = requests.put(
            "https://api.line.me/v2/bot/channel/webhook/endpoint",
            headers=headers,
            json=data
        )
        
        if line_response.status_code == 200:
            print("✅ 成功更新 LINE Webhook URL！")
        else:
            print(f"❌ 更新失敗: {line_response.status_code}")
            print(line_response.text)

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")

if __name__ == "__main__":
    update_line_webhook()
