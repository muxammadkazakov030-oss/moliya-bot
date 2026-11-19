import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import genai


# --- 1. Render HTTP сервер (порт очиш ва "Live" ҳолати учун) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Moliya bot is running!")

    def log_message(self, format, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


threading.Thread(target=run_web_server, daemon=True).start()


# --- 2. Калитлар ва созламалар ---
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "1043ae8441ae40cb935")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6LbZpd0ZwBZBjv...")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "9270595335:AAFrPS1w1aE...")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5955129098")


# --- 3. Gemini клиент ва текширилган хабарлар ---
client = genai.Client(api_key=GEMINI_API_KEY)
processed_urls = set()


def fetch_news() -> list:
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "finance OR gold OR forex OR market",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 5,
        "apiKey": NEWS_API_KEY,
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("articles", [])
    except Exception as e:
        print(f"Yangilik olishda xato: {e}")
        return []


def analyze_news(title: str, description: str) -> str:
    prompt = (
        "Quyidagi moliyaviy yangilikni tahlil qilib, o'zbek tilida qisqa va aniq "
        "xulosa bering (qaysi bozor/aktivga ta'sir qilishi va asosiy mazmuni):\n"
        f"Sarlavha: {title}\n"
        f"Tavsif: {description or ''}"
    )
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return (response.text or "").strip()
    except Exception as e:
        print(f"Gemini tahlilida xato: {e}")
        return ""


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"Telegram yuborishda xato: {e}")


def main_loop():
    print("Moliya bot muvaffaqiyatli ishga tushdi!")
    while True:
        try:
            articles = fetch_news()
            new_count = 0
            for article in articles:
                article_url = article.get("url")
                if not article_url or article_url in processed_urls:
                    continue

                processed_urls.add(article_url)
                title = article.get("title") or "Yangilik"
                description = article.get("description") or ""

                analysis = analyze_news(title, description)
                if analysis:
                    message = f"📊 Yangilik: {title}\n\n{analysis}\n\n🔗 {article_url}"
                    send_telegram_message(message)
                    new_count += 1
                    time.sleep(2)

            if new_count == 0:
                print("Yangi moliyaviy xabarlar topilmadi, kutilmoqda...")
        except Exception as e:
            print(f"Asosiy siklda xato: {e}")

        time.sleep(300)


if __name__ == "__main__":
    main_loop()
