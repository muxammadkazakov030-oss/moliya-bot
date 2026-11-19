import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import genai


def log(msg: str):
    print(msg, flush=True)


# --- 1. Render HTTP сервер (порт очиш ва ушлаб туриш) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Moliya bot is running")

    def log_message(self, format, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    log(f"HTTP сервер {port}-портда ишга тушди.")
    server.serve_forever()


threading.Thread(target=run_web_server, daemon=True).start()


# --- 2. Калитлар ва созламалар ---
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


# --- 3. Gemini клиент ва текширилган хабарлар ---
client = genai.Client(api_key=GEMINI_API_KEY)
processed_urls = set()


def fetch_news() -> list:
    if not NEWS_API_KEY:
        log("XATO: NEWS_API_KEY топилмади.")
        return []

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": '(gold OR XAU OR forex OR EUR/USD OR "EUR USD" OR Fed OR inflation OR CPI)',
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 5,
        "apiKey": NEWS_API_KEY,
    }
    try:
        log("NewsAPI'дан янгиликлар сўралмоқда...")
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        articles = data.get("articles", [])
        log(f"NewsAPI'дан {len(articles)} та мақола келди.")
        return articles
    except Exception as e:
        log(f"Yangilik olishda xato: {e}")
        return []


def analyze_news(title: str, description: str) -> str:
    if not GEMINI_API_KEY:
        log("XATO: GEMINI_API_KEY топилмади.")
        return ""

    prompt = (
        "Sen moliyaviy tahlilchisan. Quyidagi yangilikning XAU/USD (oltin) va "
        "EUR/USD bozorlariga ehtimoliy ta'sirini o'zbek tilida qisqa va aniq tahlil qil.\n"
        "Javobda:\n"
        "1) Qisqa xulosa\n"
        "2) XAU/USD ta'siri (Bullish / Bearish / Neytral)\n"
        "3) EUR/USD ta'siri (Bullish / Bearish / Neytral)\n"
        "4) Muhim ehtiyot eslatmasi (bu moliyaviy maslahat emas)\n\n"
        f"Sarlavha: {title}\n"
        f"Tavsif: {description or ''}"
    )
    try:
        log(f"Gemini таҳлил қилмоқда: {title[:60]}...")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = (response.text or "").strip()
        log("Gemini таҳлили тайёр бўлди.")
        return text
    except Exception as e:
        log(f"Gemini tahlilida xato: {e}")
        return ""


def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("XATO: TELEGRAM_TOKEN ёки TELEGRAM_CHAT_ID топилмади.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }
    try:
        log("Telegram'га хабар юборилмоқда...")
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        log("Telegram'га хабар муваффақиятли юборилди.")
        return True
    except Exception as e:
        log(f"Telegram yuborishda xato: {e}")
        return False


def main_loop():
    log("Moliya bot muvaffaqiyatli ishga tushdi!")
    send_telegram_message(
        "🤖 Savdo tahlil agenti ishga tushdi va XAU/USD hamda EUR/USD yangiliklarini kuzatmoqda!"
    )

    while True:
        try:
            log("--- Янги текширув цикли бошланди ---")
            articles = fetch_news()
            new_count = 0

            for article in articles:
                article_url = article.get("url")
                if not article_url or article_url in processed_urls:
                    continue

                processed_urls.add(article_url)
                title = article.get("title") or "Sarlavhasiz yangilik"
                description = article.get("description") or ""

                analysis = analyze_news(title, description)
                if analysis:
                    message = (
                        f"📊 Yangi moliyaviy tahlil (XAU/USD & EUR/USD)\n\n"
                        f"📰 {title}\n\n"
                        f"{analysis}\n\n"
                        f"🔗 Manba: {article_url}"
                    )
                    if send_telegram_message(message):
                        new_count += 1
                    time.sleep(2)

            if new_count == 0:
                log("Yangi moliyaviy xabar yoki yuborilgan tahlil topilmadi.")
            else:
                log(f"Jami {new_count} ta yangi tahlil Telegram'ga yuborildi.")

        except Exception as e:
            log(f"Asosiy siklda xato: {e}")

        log("300 сония (5 дақиқа) кутилмоқда...")
        time.sleep(300)


if __name__ == "__main__":
    main_loop()
