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


# --- 1. Render HTTP сервер ва Keep-alive ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Moliya Bot (Precision Signal) is running!".encode("utf-8"))

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


# --- 2. Созламалар ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

CANDIDATE_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
]
UZ_TZ = timezone(timedelta(hours=5))
MAX_DAILY_GEMINI_CALLS = 18

# Аниқ нозик вақт оралиқлари (Ўзбекистон вақти билан, дақиқаларда)
# 12:00-13:30 -> 720..810 | 17:30-19:30 -> 1050..1170
WINDOWS_UZ = [
    (720, 810, "Лондон ойнаси (12:00–13:30 UZ)"),
    (1050, 1170, "Нью-Йорк / АҚШ макро ойнаси (17:30–19:30 UZ)"),
]

RSS_FEEDS = [
    ("FXStreet Forex", "https://xml.fxstreet.com/news/forex-news/index.xml"),
    ("Investing Forex", "https://www.investing.com/rss/news_1.rss"),
    ("Investing Economy", "https://www.investing.com/rss/news_14.rss"),
    ("CNBC Economy", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
]

MARKET_IMPACT_KEYWORDS = (
    "cpi", "inflation", "fomc", "fed", "federal reserve", "powell",
    "interest rate", "rate cut", "rate hike", "nfp", "non-farm", "nonfarm",
    "payroll", "unemployment", "jobless", "gdp", "pmi", "manufacturing",
    "services", "retail sales", "pce", "boe", "bank of england", "bailey",
    "boc", "bank of canada", "macklem", "boj", "ueda", "ecb", "lagarde",
    "pboc", "central bank", "tariff", "tariffs", "sanctions",
    "treasury", "yield", "yields", "xau", "gold",
    "gbp/usd", "gbpusd", "usd/jpy", "usdjpy", "usd/cad", "usdcad",
    "nasdaq", "us100", "dxy", "crude oil", "wti", "opec"
)

NOISE_KEYWORDS = (
    "24k", "22k", "18k", "retail rates", "city retail", "jewellery", "jewelry",
    "visakhapatnam", "andhra pradesh", "mumbai", "delhi", "chennai",
    "kolkata", "hyderabad", "bengaluru", "pune", "ahmedabad", "hallmark", "wedding",
    "bitcoin", "ethereum", "solana", "ripple", "xrp", "dogecoin", "crypto",
    "forint", "nbh", "hungarian", "zloty", "koruna", "lira", "rand", "peso", "rupee",
    "sensex", "nifty", "bse",
    "price forecast", "technical analysis", "chart of the day", "elliott wave",
    "fibo retracement", "fibonacci", "crossroads"
)

MACRO_OVERRIDE_KEYWORDS = (
    "cpi", "pce", "inflation", "fomc", "fed", "powell",
    "interest rate", "rate cut", "rate hike", "nfp", "nonfarm", "payroll",
    "gdp", "pmi", "tariff", "tariffs", "boe", "boc", "boj", "ecb", "opec"
)

MARKET_SYMBOLS = {
    "XAU/USD (Oltin)": (["GC=F"], 2),
    "GBP/USD": (["GBPUSD=X"], 5),
    "USD/JPY": (["JPY=X"], 3),
    "USD/CAD": (["CAD=X"], 5),
    "Nasdaq (US100)": (["NQ=F", "^NDX"], 2),
    "WTI Oil": (["CL=F", "USO"], 2),
}

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
processed_urls = set()
daily_state = {"date": None, "calls": 0}
sent_in_current_window = {"window_key": None, "sent": False}


def get_uz_now() -> datetime:
    return datetime.now(UZ_TZ)


def get_active_window(now_uz: datetime):
    mins = now_uz.hour * 60 + now_uz.minute
    for start_m, end_m, label in WINDOWS_UZ:
        if start_m <= mins <= end_m:
            key = f"{now_uz.strftime('%Y-%m-%d')}_{start_m}_{end_m}"
            return key, label
    return None, None


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


def clean_telegram_formatting(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*-{3,}\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\*{1,2}([^*\n]+)\*{1,2}", r"\1", cleaned)
    cleaned = cleaned.replace("**", "").replace("*", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def is_market_relevant_article(title: str, description: str) -> bool:
    combined = f"{title} {description}".lower()
    if any(noise in combined for noise in NOISE_KEYWORDS):
        return False
    if any(macro in combined for macro in MACRO_OVERRIDE_KEYWORDS):
        return True
    return any(kw in combined for kw in MARKET_IMPACT_KEYWORDS)


# --- 3. Техник индикаторлар ---
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
    recent_high = round(max(highs[-24:]), decimals) if highs else current_price
    recent_low = round(min(lows[-24:]), decimals) if lows else current_price

    trend = "Bullish" if current_price > ema20 else "Bearish"

    return {
        "symbol": symbol,
        "price": current_price,
        "ema20": ema20,
        "rsi14": rsi14,
        "trend": trend,
        "resistance_24h": recent_high,
        "support_24h": recent_low,
    }


def fetch_all_market_data() -> str:
    blocks = []
    for label, (candidates, decimals) in MARKET_SYMBOLS.items():
        snap = None
        for sym in candidates:
            try:
                snap = fetch_technical_snapshot(sym, decimals)
                break
            except Exception as e:
                log(f"Техник маълумот хатоси ({label} / {sym}): {e}")

        if snap:
            blocks.append(
                f"- {label} [{snap['symbol']}]: Joriy narx={snap['price']} | "
                f"EMA-20={snap['ema20']} ({snap['trend']}) | "
                f"RSI(14)={snap['rsi14']} | "
                f"Support={snap['support_24h']} | Resistance={snap['resistance_24h']}"
            )
        else:
            blocks.append(f"- {label}: Noma'lum")

    return "\n".join(blocks)


# --- 4. Янгиликларни олиш ---
def fetch_all_rss_feeds():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    }
    articles = []
    for feed_name, feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:7]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = clean_html_tags(item.findtext("description") or "")
                if title and link:
                    articles.append({"title": title, "description": desc, "url": link})
        except Exception as e:
            log(f"RSS [{feed_name}] хатоси: {e}")
    return articles


def fetch_newsapi_articles():
    if not NEWS_API_KEY:
        return []
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": (
            '("gold price" OR XAU OR "GBP/USD" OR "USD/JPY" OR "USD/CAD" OR Nasdaq OR DXY OR "crude oil" OR WTI) '
            'AND (Fed OR FOMC OR CPI OR PCE OR inflation OR NFP OR PMI OR "interest rate" OR BoE OR BoC OR BoJ OR yields OR tariff)'
        ),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 15,
        "apiKey": NEWS_API_KEY,
    }
    articles = []
    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        for art in response.json().get("articles", []):
            if art.get("url") and art.get("title"):
                articles.append({
                    "title": art.get("title", "").strip(),
                    "description": clean_html_tags(art.get("description") or ""),
                    "url": art.get("url", "").strip(),
                })
    except Exception as e:
        log(f"NewsAPI хатоси: {e}")
    return articles


def fetch_combined_news():
    return fetch_all_rss_feeds() + fetch_newsapi_articles()


# --- 5. Битта нозик сигнал яратиш ---
def analyze_single_best_signal(title: str, description: str, market_data_text: str, window_label: str) -> str:
    if not client:
        return ""

    prompt = (
        "Sen tajribali fundamental va texnik treyder-tahlilchisisisan.\n"
        f"Hozirgi vaqt oynasi: {window_label}.\n"
        "Quyidagi makroiqtisodiy yangilik va 6 ta aktivning joriy narx + EMA-20 + RSI-14 + Support/Resistance ma'lumotlariga qarab "
        "FAQAT BITTA eng kuchli va eng aniq signalga ega aktivni tanla.\n"
        "Qoidalar:\n"
        "1. 6 ta aktivni uzun ro'yxat qilib yozma — faqat eng ishonchli BITTA aktivni ber.\n"
        "2. Agar hech bir aktivda aniq ustunlik yoki trend tasdig'i bo'lmasa, faqat 'SIGNAL YOQ: Kuzatuv rejimi' deb yoz.\n"
        "3. Signal berilganda MT5 da Stop Hunt (ilk 15-30 daqiqa soxta impuls) tugab, narx so'nggi soyadan qaytganidan keyin kirishni eslat.\n"
        "4. Markdown belgilarini (###, **, ---) umuman ishlatma.\n\n"
        f"JORIY BOZOR MA'LUMOTLARI:\n{market_data_text}\n\n"
        f"YANGILIK: {title}\n"
        f"TAVSIF: {description or 'Tavsif yoq'}\n\n"
        "Javobni faqat quyidagi ko'rinishda yoz:\n"
        "Oyna: [vaqt oynasi]\n"
        "Aktiv: [bitta aktiv nomi] (Joriy narx: [narx])\n"
        "Yo'nalish: [Buy yoki Sell]\n"
        "Entry: [kirish narxi yoki oralig'i]\n"
        "SL: [himoya Stop Loss narxi]\n"
        "TP: [maqsad Take Profit narxi]\n"
        "Nozik qoida: [15-30 daqiqa Stop Hunt / wick qaytishini kutib kirish bo'yicha 1 qisqa izoh]\n\n"
        "Bu tahlil moliyaviy maslahat emas, faqat o'quv/tahliliy mo'ljal."
    )

    for model_name in CANDIDATE_MODELS:
        for attempt in range(1, 3):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                cleaned = clean_telegram_formatting((response.text or "").strip())
                if cleaned:
                    return cleaned
            except Exception as e:
                err_str = str(e)
                log(f"Gemini хатоси [{model_name} | уриниш {attempt}/2]: {err_str}")
                if "404" in err_str or "NOT_FOUND" in err_str:
                    break
                if ("503" in err_str or "UNAVAILABLE" in err_str or "500" in err_str or "429" in err_str) and attempt < 2:
                    time.sleep(3)
                else:
                    break
    return ""


def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"Telegram хатоси: {e}")
        return False


def notify_startup():
    now_str = get_uz_now().strftime("%H:%M")
    send_telegram_message(
        f"✅ Бот 24/7 режимида ишга тушди ({now_str} UZ)\n"
        f"Аниқ вақт ойнаси: 12:00–13:30 ва 17:30–19:30 UZ\n"
        f"Режим: Ҳар ойнада фақат 1 та нозик сигнал"
    )


def main_loop():
    threading.Thread(target=run_http_server, daemon=True).start()
    threading.Thread(target=keep_alive_loop, daemon=True).start()
    notify_startup()

    while True:
        now_uz = get_uz_now()
        window_key, window_label = get_active_window(now_uz)

        if not window_key:
            log(f"[{now_uz.strftime('%H:%M')} UZ] Нозик вақт ойнасидан ташқари — лимит тежалмоқда (15 дақиқа кутиш).")
            time.sleep(900)
            continue

        if sent_in_current_window["window_key"] != window_key:
            sent_in_current_window["window_key"] = window_key
            sent_in_current_window["sent"] = False

        if sent_in_current_window["sent"]:
            log(f"[{now_uz.strftime('%H:%M')} UZ] {window_label} учун нозик сигнал аллақачон берилган.")
            time.sleep(600)
            continue

        if not can_call_gemini(now_uz):
            log("Кунлик бепул Gemini лимити тўлди.")
            time.sleep(1800)
            continue

        try:
            articles = fetch_combined_news()
            for art in articles:
                url = art.get("url")
                if not url or url in processed_urls:
                    continue
                processed_urls.add(url)

                title = art.get("title", "")
                desc = art.get("description", "")
                if not is_market_relevant_article(title, desc):
                    continue

                market_data = fetch_all_market_data()
                signal_text = analyze_single_best_signal(title, desc, market_data, window_label)
                register_gemini_call(get_uz_now())

                if signal_text and "SIGNAL YOQ" not in signal_text.upper():
                    msg = (
                        f"🎯 Нозик Сигнал ({get_uz_now().strftime('%H:%M')} UZ)\n\n"
                        f"{signal_text}\n\n"
                        f"📰 {title}\n"
                        f"🔗 {url}"
                    )
                    if send_telegram_message(msg):
                        sent_in_current_window["sent"] = True
                        log(f"Нозик сигнал юборилди ({window_label}).")
                        break
                else:
                    log("Мос келган янгиликда ҳозирча аниқ сигнал чиқмади.")
        except Exception as e:
            log(f"Цикл хатоси: {e}")

        time.sleep(300)


if __name__ == "__main__":
    main_loop()
