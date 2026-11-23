import os
import re
import time
import threading
import xml.etree.ElementTree as ET
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
        self.wfile.write("Moliya Bot (Fundamental + Technical) is running!".encode("utf-8"))

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

MODEL_NAME = "gemini-2.5-flash"
UZ_TZ = timezone(timedelta(hours=5))  # Ўзбекистон вақти (UTC+5)
MAX_DAILY_GEMINI_CALLS = 18           # Free Tier лимитдан ошмаслик учун
FXSTREET_RSS_URL = "https://www.fxstreet.com/rss/news"

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
processed_urls = set()

daily_state = {
    "date": None,
    "calls": 0,
}

# Муҳим макроиқтисодий ва глобал молиявий бозор калит сўзлари
MARKET_IMPACT_KEYWORDS = (
    "cpi", "inflation", "fomc", "fed", "federal reserve", "powell", "interest rate",
    "rate cut", "rate hike", "nfp", "non-farm", "nonfarm", "payroll", "unemployment",
    "jobless", "gdp", "pmi", "business activity", "manufacturing", "services",
    "retail sales", "pce", "boe", "bank of england", "bailey",
    "boc", "bank of canada", "macklem", "boj", "ueda", "rbi", "pboc",
    "central bank", "reserves", "import duty", "tariff", "sanctions",
    "treasury", "yield", "bond", "xau", "gold", "bullion",
    "gbp/usd", "gbpusd", "pound", "sterling", "uk", "britain",
    "usd/jpy", "usdjpy", "yen", "japan",
    "usd/cad", "usdcad", "loonie", "canadian", "canada",
    "nasdaq", "us100", "s&p 500", "wall street", "tech stocks",
    "dxy", "dollar", "greenback",
    "crude oil", "wti", "brent", "opec", "forex", "eur/usd", "eurozone"
)

# Фақат маҳаллий заргарлик дўконлари чакана нархлари ва крипто шовқинини блоклаш
LOCAL_RETAIL_NOISE_KEYWORDS = (
    "24k", "22k", "18k", "retail rates", "city retail", "jewellery", "jewelry",
    "visakhapatnam", "andhra pradesh", "mumbai", "delhi", "chennai",
    "kolkata", "hyderabad", "bengaluru", "pune", "ahmedabad", "hallmark", "wedding",
    "bitcoin", "ethereum", "solana", "ripple", "xrp", "dogecoin", "crypto"
)

# Йирик давлатларнинг глобал бозорга таъсир қилувчи макроиқтисодий истисно сўзлари
MACRO_OVERRIDE_KEYWORDS = (
    "central bank", "rbi", "reserve bank of india", "pboc", "fed", "boe", "boc", "boj",
    "interest rate", "rate cut", "rate hike", "inflation", "cpi", "gdp", "pmi",
    "import duty", "customs duty", "gold reserves", "sovereign", "tariff", "opec", "dxy"
)

# MT5 спот котировкаларига максимал мос 6 та актив
MARKET_SYMBOLS = {
    "XAU/USD": (["XAUUSD=X", "GC=F"], 2),
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


def clean_html_tags(raw_text: str) -> str:
    if not raw_text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", raw_text)
    return re.sub(r"\s+", " ", cleaned).strip()


def is_market_relevant_article(title: str, description: str) -> bool:
    combined = f"{title} {description}".lower()
    has_macro_override = any(macro in combined for macro in MACRO_OVERRIDE_KEYWORDS)

    if not has_macro_override and any(noise in combined for noise in LOCAL_RETAIL_NOISE_KEYWORDS):
        return False

    return any(kw in combined for kw in MARKET_IMPACT_KEYWORDS)


# --- 3. Техник индикаторларни ҳисоблаш (EMA-20, RSI-14, Support/Resistance) ---
def compute_ema(prices: list, period: int = 20) -> float:
    if len(prices) < period:
        return sum(prices) / len(prices)
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def compute_rsi(prices: list, period: int = 14) -> float:
    if len(prices) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def fetch_technical_snapshot(symbol: str, decimals: int) -> dict:
    """
    Yahoo Finance'дан 1 соатлик (5 кунлик) свечаларни олиб,
    жорий нарх, EMA-20, RSI-14 ва кунлик Support/Resistance даражаларини ҳисоблайди.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1h&range=5d"
    resp = requests.get(url, headers=headers, timeout=12)
    resp.raise_for_status()
    data = resp.json()

    result = data["chart"]["result"][0]
    meta = result["meta"]
    quote = result["indicators"]["quote"][0]

    closes = [float(c) for c in quote.get("close", []) if c is not None]
    highs = [float(h) for h in quote.get("high", []) if h is not None]
    lows = [float(l) for l in quote.get("low", []) if l is not None]

    current_price = meta.get("regularMarketPrice")
    if current_price is None and closes:
        current_price = closes[-1]
    if current_price is None or float(current_price) <= 0:
        raise ValueError(f"Нарх топилмади: {symbol}")

    current_price = round(float(current_price), decimals)
    ema20 = round(compute_ema(closes[-40:], 20), decimals) if closes else current_price
    rsi14 = round(compute_rsi(closes[-30:], 14), 1) if closes else 50.0

    # Охирги 24 соатлик (24 та 1H свеча) Support ва Resistance
    recent_high = round(max(highs[-24:]), decimals) if highs else current_price
    recent_low = round(min(lows[-24:]), decimals) if lows else current_price

    trend = "Юқорига (Bullish)" if current_price > ema20 else "Пастга (Bearish)"
    if rsi14 >= 70:
        rsi_state = "Overbought (Ҳаддан ташқари сотиб олинган)"
    elif rsi14 <= 30:
        rsi_state = "Oversold (Ҳаддан ташқари сотилган)"
    else:
        rsi_state = "Нейтрал"

    return {
        "symbol": symbol,
        "price": current_price,
        "ema20": ema20,
        "rsi14": rsi14,
        "rsi_state": rsi_state,
        "trend": trend,
        "resistance_24h": recent_high,
        "support_24h": recent_low,
    }


def fetch_all_market_data() -> str:
    """
    Барча 6 та актив учун жонли нарх ва H1 техник кўрсаткичларни йиғади.
    """
    blocks = []
    for label, (candidates, decimals) in MARKET_SYMBOLS.items():
        snap = None
        for sym in candidates:
            try:
                snap = fetch_technical_snapshot(sym, decimals)
                break
            except Exception as e:
                log(f"Техник маълумот олишда хато ({label} / {sym}): {e}")

        if snap:
            blocks.append(
                f"• {label} [{snap['symbol']}]:\n"
                f"  - Жорий нарх: {snap['price']}\n"
                f"  - H1 Тренд (EMA-20: {snap['ema20']}): {snap['trend']}\n"
                f"  - H1 RSI(14): {snap['rsi14']} ({snap['rsi_state']})\n"
                f"  - 24H Support: {snap['support_24h']} | 24H Resistance: {snap['resistance_24h']}"
            )
        else:
            blocks.append(f"• {label}: Маълумот вақтинча номаълум")

    return "\n".join(blocks)


# --- 4. Янгиликларни олиш (FXStreet Live RSS + NewsAPI) ---
def fetch_fxstreet_rss():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    }
    articles = []
    try:
        resp = requests.get(FXSTREET_RSS_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item")[:10]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = clean_html_tags(item.findtext("description") or "")
            if title and link:
                articles.append({
                    "title": title,
                    "description": desc,
                    "url": link,
                    "source_tag": "FXStreet Live",
                })
        log(f"FXStreet RSS'дан {len(articles)} та мақола олинди.")
    except Exception as e:
        log(f"FXStreet RSS олишда хато: {e}")
    return articles


def fetch_newsapi_articles():
    if not NEWS_API_KEY:
        return []

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": (
            '("gold price" OR XAU OR "GBP/USD" OR "USD/JPY" OR "USD/CAD" OR Nasdaq OR DXY OR "crude oil" OR WTI) '
            'OR (Fed OR FOMC OR CPI OR inflation OR NFP OR PMI OR "business activity" OR "interest rate" '
            'OR BoE OR BoC OR BoJ OR RBI OR PBOC OR "treasury yields")'
        ),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 6,
        "apiKey": NEWS_API_KEY,
    }

    articles = []
    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        for art in data.get("articles", []):
            if art.get("url") and art.get("title"):
                articles.append({
                    "title": art.get("title", "").strip(),
                    "description": clean_html_tags(art.get("description") or ""),
                    "url": art.get("url", "").strip(),
                    "source_tag": "Global Macro",
                })
        log(f"NewsAPI'дан {len(articles)} та мақола олинди.")
    except Exception as e:
        log(f"NewsAPI олишда хато: {e}")
    return articles


def fetch_combined_news():
    return fetch_fxstreet_rss() + fetch_newsapi_articles()


# --- 5. Gemini орқали Фундаментал + Техник таҳлил ---
def analyze_news(title: str, description: str, market_data_text: str) -> str:
    if not client:
        log("GEMINI_API_KEY sozlanmagan.")
        return ""

    prompt = (
        "Sen yuqori aniqlikdagi professional fundamental va texnik treyder-tahlilchisisisan.\n"
        "Quyida so'nggi iqtisodiy yangilik hamda 6 ta aktivning JORIY REAL NARXLARI va H1 TEXNIK INDIKATORLARI (EMA-20, RSI-14, 24H Support/Resistance) berilgan.\n"
        "Eski xotirangdagi narxlarni UMUMAN ishlatma — faqat quyidagi real ma'lumotlarga tayan!\n\n"
        f"BOZORNING JORIY TEXNIK XARITASI:\n{market_data_text}\n\n"
        f"YANGILIK SARLAVHASI: {title}\n"
        f"YANGILIK TAVSIFI: {description or 'Tavsif mavjud emas'}\n\n"
        "VAZIFA: Fundamental yangilik va texnik indikatorlar (EMA-20, RSI-14, Support/Resistance) bir-birini tasdiqlagan holatlar uchun O'ZBEK TILIDA aniq, ravshan va qisqa savdo signallarini tuz.\n\n"
        "CHIQISH FORMATI (faqat shu strukturada yoz):\n"
        "📌 **Yangilik va ta'siri:** (1-2 gapda mohiyati va ta'sir kuchi: High-Impact / O'rtacha / Past)\n\n"
        "🎯 **ANIQ SAVDO SIGNALLARI:**\n"
        "(Faqat ushbu yangilik va texnik indikatorlar bo'yicha aniq 'BUY' yoki 'SELL' ehtimoli yuqori bo'lgan paralar uchun yoz! Agar biror paraga yangilikning aloqasi bo'lmasa yoki texnik holat qarama-qarshi bo'lsa, u parani umuman yozma!)\n\n"
        "Har bir mos para uchun:\n"
        "🔹 **[PARA NOMI]** — **BUY** (yoki **SELL**)\n"
        "• Joriy narx: ...\n"
        "• Sabab (Fundament + Texnika): (Masalan: DXY kuchsizlanmoqda, narx H1 EMA-20 dan yuqorida va RSI 58 — o'sish kuchi bor)\n"
        "• Entry (Kirish): ...\n"
        "• Stop Loss (SL): ... (Support/Resistance ortiga mantiqiy qo'y)\n"
        "• Take Profit 1 (TP1): ...\n"
        "• Take Profit 2 (TP2): ...\n\n"
        "(Agar 6 ta paradan HECH BIRIDA aniq fundamental + texnik moslik bo'lmasa, faqatgina 'NO_SIGNAL' deb javob ber — boshqa hech narsa yozma!)\n\n"
        "⚠️ Oxirida qisqa eslatma: _Ushbu signal tahliliy ma'lumot bo'lib, risk-menejment (SL) bilan foydalanish tavsiya etiladi._"
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
            is_retryable = ("503" in err_str) or ("UNAVAILABLE" in err_str) or ("500" in err_str) or ("429" in err_str)
            if is_retryable and attempt < max_attempts:
                wait_sec = attempt * 4
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
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
        if response.status_code != 200:
            # Markdown хатоси бўлса, оддий текст сифатида қайта юбориш
            payload.pop("parse_mode", None)
            response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        return True
    except Exception as e:
        log(f"Telegramga yuborishda xato: {e}")
        return False


def send_startup_summary():
    """
    Бот ишга тушиши билан Telegram'га жонли техник ҳолат ва тайёрлик хабарини юборади.
    """
    now_str = get_uz_now().strftime("%H:%M")
    try:
        market_snapshot = fetch_all_market_data()
        msg = (
            f"✅ *Moliya Bot Yangilandi va Ishga Tushdi* ({now_str} UZ)\n\n"
            f"📡 *Manbalar:* FXStreet Live RSS + Global Macro\n"
            f"📐 *Texnik tahlil:* H1 EMA-20, RSI(14), 24H Support/Resistance\n\n"
            f"📊 *Joriy Bozor Xaritasi:*\n{market_snapshot}\n\n"
            f"🔔 Aniq fundamental + texnik signal shakllanishi bilan darhol xabar yuboriladi."
        )
        send_telegram_message(msg)
    except Exception as e:
        log(f"Startup хабар юборишда хато: {e}")


def main_loop():
    threading.Thread(target=run_http_server, daemon=True).start()
    threading.Thread(target=keep_alive_loop, daemon=True).start()

    log(f"Moliya bot ishga tushdi. Model: {MODEL_NAME} | Fundamental + Technical Mode")
    send_startup_summary()

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

        sent_count = 0
        try:
            articles = fetch_combined_news()
            log(f"Жами текширилаётган мақолалар сони: {len(articles)}")

            for article in articles:
                if not can_call_gemini(get_uz_now()):
                    log("Кунлик сўров лимитига етилди.")
                    break

                if sent_count >= 1:
                    break

                article_url = article.get("url")
                if not article_url or article_url in processed_urls:
                    continue

                processed_urls.add(article_url)
                title = article.get("title") or "Sarlavhasiz yangilik"
                description = article.get("description") or ""
                source_tag = article.get("source_tag") or "Manba"

                if not is_market_relevant_article(title, description):
                    log(f"Ўтказиб юборилди (шовқин/алоқасиз): {title[:60]}...")
                    continue

                log(f"[{source_tag}] Техник харита олинмоқда ва таҳлил қилинмоқда: {title[:60]}...")
                market_data_text = fetch_all_market_data()

                register_gemini_call(get_uz_now())
                analysis = analyze_news(title, description, market_data_text)

                if not analysis or "NO_SIGNAL" in analysis:
                    log(f"Сигнал тасдиқланмади (NO_SIGNAL): {title[:60]}...")
                    continue

                message = (
                    f"🚨 *SAVDO SIGNALI [{source_tag}]* ({get_uz_now().strftime('%H:%M')} UZ)\n\n"
                    f"📰 *{title}*\n\n"
                    f"{analysis}\n\n"
                    f"🔗 [Manbani o'qish]({article_url})"
                )
                if send_telegram_message(message):
                    sent_count += 1
                    log(f"Сигнал муваффақиятли юборилди! Бугунги сўровлар: {daily_state['calls']}/{MAX_DAILY_GEMINI_CALLS}")
                time.sleep(2)

            if sent_count == 0:
                log("Ушбу айланишда янги тасдиқланган сигнал топилмади.")

        except Exception as e:
            log(f"Асосий циклда хато: {e}")

        log(f"Навбатдаги текширувгача {sleep_seconds} сония кутилмоқда...")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main_loop()
