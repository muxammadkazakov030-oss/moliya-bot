import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import genai


def log(msg: str):
    print(msg, flush=True)


# --- 1. Render HTTP сервер (порт очиш ва текшириш учун) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Moliya Bot is running!".encode("utf-8"))

    def log_message(self, format, *args):
        return


def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    log(f"HTTP сервер {port}-портда ишга тушди.")
    server.serve_forever()


def keep_alive_loop():
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "https://moliya-bot-z9f1.onrender.com").rstrip("/")
    time.sleep(30)
    while True:
        try:
            requests.get(render_url, timeout=15)
            log(f"Keep-alive сўров юборилди: {render_url}")
        except Exception as e:
            log(f"Keep-alive хатоси: {e}")
        time.sleep(240)


# --- 2. Муҳит ўзгарувчилари ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

MODEL_NAME = "gemini-3.8-flash"

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
processed_urls = set()


def fetch_financial_news():
    if not NEWS_API_KEY:
        log("NEWS_API_KEY топилмади.")
        return []

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "(gold OR forex OR inflation OR Fed OR interest rate OR stock OR oil OR Nasdaq) AND (market OR economy OR finance)",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 10,
        "apiKey": NEWS_API_KEY,
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        return data.get("articles", [])
    except Exception as e:
        log(f"Yangiliklarni olishda xato: {e}")
        return []


def analyze_news(title: str, description: str) -> str:
    if not client:
        log("GEMINI_API_KEY sozlanmagan.")
        return ""

    prompt = (
        "Sen moliyaviy bozorlar (XAU/USD, EUR/USD, Nasdaq, oltin, aksiyalar, makroiqtisodiyot) bo'yicha tahlilchisan.\n"
        "Quyidagi yangilikni qisqa, aniq va tushunarli qilib O'ZBEK TILIDA tahlil qil:\n"
        "1. Yangilikning qisqa mazmuni (1-2 gap).\n"
        "2. Bozorlarga ehtimoliy ta'siri (Oltin/XAU/USD, EUR/USD, Nasdaq/Aksiyalar: ijobiy, salbiy yoki neytral).\n"
        "3. Taxminiy/ehtimoliy savdo mo'ljali (agar yangilik bozor uchun yetarlicha aniq signal bersa):\n"
        "   - Aktiv (masalan: XAU/USD, EUR/USD yoki Nasdaq) va yo'nalish (Buy/Sell yoki Kuzatuv);\n"
        "   - Taxminiy Entry (faqat yangilik va umumiy bozor kontekstiga tayangan mo'ljal zona/daraja);\n"
        "   - Taxminiy SL (Stop Loss mo'ljali);\n"
        "   - Taxminiy TP (Take Profit mo'ljali).\n"
        "   Agar yangilik noaniq yoki zaif ta'sirli bo'lsa, aniq kirish nuqtasi majburiy emas — shunchaki 'Aniq signal darajasida emas, kuzatuv tavsiya etiladi' deb yoz.\n"
        "4. Ogohlantirish: albatta 'moliyaviy maslahat emas, faqat tahliliy/o'quv mo'ljali' deb qayd et.\n\n"
        f"Sarlavha: {title or 'Noma’lum'}\n"
        f"Tavsif: {description or 'Tavsif mavjud emas'}"
    )

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
        )
        return (response.text or "").strip()
    except Exception as e:
        log(f"Gemini tahlilida xato: {e}")
        return ""


def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("TELEGRAM_TOKEN yoki TELEGRAM_CHAT_ID sozlanmagan.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        return True
    except Exception as e:
        log(f"Telegramga yuborishda xato: {e}")
        return False


def main_loop():
    threading.Thread(target=run_http_server, daemon=True).start()
    threading.Thread(target=keep_alive_loop, daemon=True).start()

    log(f"Moliya bot ishga tushdi. Model: {MODEL_NAME}")

    while True:
        new_count = 0
        try:
            articles = fetch_financial_news()
            for article in articles:
                article_url = article.get("url")
                if not article_url or article_url in processed_urls:
                    continue

                processed_urls.add(article_url)
                title = article.get("title") or "Sarlavhasiz yangilik"
                description = article.get("description") or ""

                log(f"Gemini таҳлил қилмоқда: {title[:80]}...")
                analysis = analyze_news(title, description)

                if analysis:
                    message = (
                        f"📊 Yangi moliyaviy tahlil\n\n"
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
                log(f"Jami {new_count} ta yangi tahlil Telegramga yuborildi.")

        except Exception as e:
            log(f"Asosiy siklda xato: {e}")

        log("300 сония (5 дақиқа) кутилмоқда...")
        time.sleep(300)


if __name__ == "__main__":
    main_loop()
