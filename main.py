import os
import time
import threading
from datetime import datetime, timezone, timedelta
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import genai


def log(msg: str):
    print(msg, flush=True)


# --- 1. Render HTTP сервер (порт очиш ва уйқудан сақлаш) ---
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


# --- 2. Муҳит ўзгарувчилари ва созламалар ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

MODEL_NAME = "gemini-3.8-flash"
UZ_TZ = timezone(timedelta(hours=5))  # Ўзбекистон вақти (UTC+5)
MAX_DAILY_GEMINI_CALLS = 18           # Free Tier 20 талик лимитдан ошмаслик учун

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
processed_urls = set()

daily_state = {
    "date": None,
    "calls": 0,
}

# Муҳим макроиқтисодий ва глобал молиявий бозор калит сўзлари (GBP/USD ва USD/CAD қўшилган)
MARKET_IMPACT_KEYWORDS = (
    "cpi", "inflation", "fomc", "fed", "federal reserve", "powell", "interest rate",
    "rate cut", "rate hike", "nfp", "non-farm", "nonfarm", "payroll", "unemployment",
    "jobless", "gdp", "pmi", "retail sales", "pce", "boe", "bank of england", "bailey",
    "boc", "bank of canada", "macklem", "boj", "ueda", "rbi", "pboc",
    "central bank", "reserves", "import duty", "tariff", "sanctions",
    "treasury", "yield", "bond", "xau", "gold", "bullion",
    "gbp/usd", "gbpusd", "pound", "sterling", "usd/jpy", "usdjpy", "yen",
    "usd/cad", "usdcad", "loonie", "canadian dollar", "nasdaq", "us100",
    "s&p 500", "wall street", "tech stocks", "dxy", "dollar", "greenback",
    "crude oil", "wti", "brent", "opec", "forex"
)

# Фақат маҳаллий заргарлик дўконлари ва шаҳарларнинг чакана тилла нархларини блоклаш
LOCAL_RETAIL_NOISE_KEYWORDS = (
    "24k", "22k", "18k", "retail rates", "city retail", "jewellery", "jewelry",
    "visakhapatnam", "andhra pradesh", "mumbai", "delhi", "chennai",
    "kolkata", "hyderabad", "bengaluru", "pune", "ahmedabad", "hallmark", "wedding"
)

# Йирик давлатларнинг глобал бозорга таъсир қилувчи макроиқтисодий истисно сўзлари
MACRO_OVERRIDE_KEYWORDS = (
    "central bank", "rbi", "reserve bank of india", "pboc", "fed", "boe", "boc", "boj",
    "interest rate", "rate cut", "rate hike", "inflation", "cpi", "gdp",
    "import duty", "customs duty", "gold reserves", "sovereign", "tariff", "opec"
)

# MT5 спот котировкаларига максимал мос 6 та актив (EUR/USD ўрнига GBP/USD ва USD/CAD)
MARKET_SYMBOLS = {
    "XAU/USD (Oltin)": (["XAUUSD=X", "GC=F"], 2),
    "GBP/USD": (["GBPUSD=X"], 5),
    "USD/JPY": (["JPY=X"], 3),
    "USD/CAD": (["CAD=X"], 5),
    "Nasdaq (US100)": (["NQ=F", "^NDX"], 2),
    "WTI Oil": (["CL=F", "USO"], 2),
}


def get_uz_now() -> datetime:
    return datetime.now(UZ_TZ)


def get_schedule_info(now_uz: datetime):
    minutes = now_uz.hour * 60 + now_uz.minute

    # 00:30 дан 12:00 гача -> Тунги/тонги сокин режим (лимит тежалади)
    if 30 <= minutes < 720:
        return False, 1800, "Тунги/тонги сокин режим (12:00 гача Gemini лимити тежалади)"

    # 16:00 - 20:30 ва 21:30 - 00:30 -> АҚШ / Канада / High-Impact юқори фаоллик соатлари
    if (960 <= minutes <= 1230) or (minutes >= 1290) or (minutes < 30):
        return True, 600, "Юқори фаоллик (АҚШ / Канада / High-Impact соати — ҳар 10 дақиқада)"

    # 12:00 - 16:00 ва 20:30 - 21:30 -> Лондон / Европа сессияси фаол режими
    return True, 900, "Фаол режим (Лондон/бозор соатлари — ҳар 15 дақиқада)"


def can_call_gemini(now_uz: datetime) -> bool:
    today_str = now_uz.strftime("%Y-%m-%d")
    if daily_state["date"] != today_str:
        daily_state["date"] = today_str
        daily_state["calls"] = 0
    return daily_state["calls"] < MAX_DAILY_GEMINI_CALLS


def register_gemini_call(now_uz: datetime):
    today_str = now_uz.strftime("%Y-%m-%d")
    if daily_state["date"] != today_str:
        daily_state["date"] = today_str
        daily_state["calls"] = 0
    daily_state["calls"] += 1


def is_market_relevant_article(title: str, description: str) -> bool:
    combined = f"{title} {description}".lower()

    # Йирик давлатлар ва марказий банкларнинг глобал макро хабарлари истисно тариқасида ўтади
    has_macro_override = any(macro in combined for macro in MACRO_OVERRIDE_KEYWORDS)

    # Локал заргарлик дўконлари чакана нархлари блокланади
    if not has_macro_override and any(noise in combined for noise in LOCAL_RETAIL_NOISE_KEYWORDS):
        return False

    return any(kw in combined for kw in MARKET_IMPACT_KEYWORDS)


def fetch_single_symbol_price(symbol: str):
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    meta = data["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    if price is None:
        raise ValueError(f"Narx topilmadi: {symbol}")
    return float(price)


def fetch_live_prices() -> str:
    """
    MT5 спот котировкаларига яқин реал бозор нархларини олади;
    асосий тикер жавоб бермаса, захира тикерни синаб кўради.
    """
    lines = []

    for label, (candidates, decimals) in MARKET_SYMBOLS.items():
        fetched_price = None
        used_symbol = None

        for sym in candidates:
            try:
                val = fetch_single_symbol_price(sym)
                if val > 0:
                    fetched_price = round(val, decimals)
                    used_symbol = sym
                    break
            except Exception as e:
                log(f"Нарх олишда хато ({label} / {sym}): {e}")

        if fetched_price is not None:
            lines.append(f"- {label} [{used_symbol}]: {fetched_price}")
        else:
            lines.append(f"- {label}: Noma'lum")

    return "\n".join(lines)


def fetch_financial_news():
    if not NEWS_API_KEY:
        log("NEWS_API_KEY топилмади.")
        return []

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": (
            '("gold price" OR XAU OR "GBP/USD" OR "USD/JPY" OR "USD/CAD" OR Nasdaq OR DXY OR "crude oil" OR WTI) '
            'OR (Fed OR FOMC OR CPI OR inflation OR NFP OR "interest rate" OR BoE OR BoC OR BoJ OR RBI OR PBOC OR "treasury yields")'
        ),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 8,
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


def analyze_news(title: str, description: str, live_prices_text: str) -> str:
    if not client:
        log("GEMINI_API_KEY sozlanmagan.")
        return ""

    prompt = (
        "Sen professional fundamental va texnik treyder-tahlilchisisisan.\n"
        "Quyida yangilik va bozorning JORIY REAL NARXLARI (MT5 spot ko'rsatkichlariga yaqin) berilgan. "
        "Eski xotirangdagi narxlarni UMUMAN ishlatma — faqat quyida berilgan JORIY REAL NARXLARGA asoslanib hisobla!\n\n"
        f"JORIY BOZOR NARXLARI:\n{live_prices_text}\n\n"
        f"YANGILIK SARLAVHASI: {title or 'Noma’lum'}\n"
        f"YANGILIK TAVSIFI: {description or 'Tavsif mavjud emas'}\n\n"
        "Tahlilni O'ZBEK TILIDA qisqa, aniq va tushunarli strukturada ber:\n"
        "1. Yangilikning qisqa mazmuni (1-2 gap; ta'sir darajasi: High-Impact, O'rtacha yoki Past).\n"
        "2. Har bir para bo'yicha ALOHIDA izoh va savdo mo'ljali (XAU/USD, GBP/USD, USD/JPY, USD/CAD, Nasdaq, WTI Oil):\n"
        "   - Agar yangilik paraga ANIQ fundamental ta'sir qilsa:\n"
        "     * Para nomi (Joriy narx: ...)\n"
        "     * Ta'siri va izoh: (1-2 gapda aniq sababini yoz)\n"
        "     * Yo'nalish: Buy yoki Sell\n"
        "     * Entry: (faqat yuqoridagi joriy narxga mos zona)\n"
        "     * SL: (joriy narx va texnik mantiqqa mos Stop Loss)\n"
        "     * TP: (joriy narx va texnik mantiqqa mos Take Profit)\n"
        "   - Agar yangilik paraga bevosita ta'sir qilmasa:\n"
        "     * Para nomi (Joriy narx: ...)\n"
        "     * Ta'siri va izoh: (qisqa 1 gap sabab)\n"
        "     * Yo'nalish: Kuzatuv (bu holatda Entry, SL, TP yozma!)\n"
        "   (Eslatma: agar biror paraning joriy narxi 'Noma'lum' bo'lsa, u para uchun raqamli narx yozma.)\n"
        "3. Oxirida majburiy eslatma: 'Ushbu tahlil moliyaviy maslahat emas, faqat tahliliy/o'quv mo'ljali hisoblanadi.'"
    )

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
            )
            return (response.text or "").strip()
        except Exception as e:
            err_str = str(e)
            log(f"Gemini tahlilida xato (urinish {attempt}/{max_attempts}): {err_str}")
            is_retryable = ("503" in err_str) or ("UNAVAILABLE" in err_str) or ("500" in err_str)
            if is_retryable and attempt < max_attempts:
                wait_sec = attempt * 3
                log(f"Vaqtinchalik yuklama sabab {wait_sec} soniyadan so'ng qayta uriniladi...")
                time.sleep(wait_sec)
            else:
                break

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

    log(f"Moliya bot ishga tushdi. Model: {MODEL_NAME} | Vaqt mintaqasi: UTC+5")

    while True:
        now_uz = get_uz_now()
        is_active, sleep_seconds, mode_label = get_schedule_info(now_uz)
        log(f"[{now_uz.strftime('%H:%M')} UZ] {mode_label}")

        if not is_active:
            log(f"12:00 гача кутилмоқда ({sleep_seconds} сония)...")
            time.sleep(sleep_seconds)
            continue

        if not can_call_gemini(now_uz):
            log(f"Кунлик Gemini лимити ({MAX_DAILY_GEMINI_CALLS} та сўров) тўлди. Эртанги кунгача кутилмоқда...")
            time.sleep(1800)
            continue

        new_count = 0
        try:
            articles = fetch_financial_news()
            for article in articles:
                if not can_call_gemini(get_uz_now()):
                    log("Кунлик сўров лимитига етилди, қолган мақолалар тўхтатилди.")
                    break

                if new_count >= 1:
                    break

                article_url = article.get("url")
                if not article_url or article_url in processed_urls:
                    continue

                processed_urls.add(article_url)
                title = article.get("title") or "Sarlavhasiz yangilik"
                description = article.get("description") or ""

                if not is_market_relevant_article(title, description):
                    log(f"Ўтказиб юборилди (локал/бозорга таъсирсиз): {title[:65]}...")
                    continue

                log(f"Реал нархлар олинмоқда ва таҳлил қилинмоқда: {title[:70]}...")
                live_prices_text = fetch_live_prices()
                log(f"Олинган жорий нархлар:\n{live_prices_text}")

                register_gemini_call(get_uz_now())
                analysis = analyze_news(title, description, live_prices_text)

                if analysis:
                    message = (
                        f"📊 Fundamental Tahlil ({get_uz_now().strftime('%H:%M')} UZ)\n\n"
                        f"📰 {title}\n\n"
                        f"{analysis}\n\n"
                        f"🔗 Manba: {article_url}"
                    )
                    if send_telegram_message(message):
                        new_count += 1
                    time.sleep(2)

            if new_count == 0:
                log("Ҳозирча янги глобал бозор хабари топилмади.")
            else:
                log(f"Жами {new_count} та таҳлил юборилди. Бугунги сўровлар: {daily_state['calls']}/{MAX_DAILY_GEMINI_CALLS}")

        except Exception as e:
            log(f"Асосий циклда хато: {e}")

        log(f"Навбатдаги текширувгача {sleep_seconds} сония кутилмоқда...")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main_loop()
