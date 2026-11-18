import time
import json
import requests
from google import genai
from google.genai import types

# 1. Калитлар ва созламалар
NEWS_API_KEY = "1043ae8441ae40cb935"
GEMINI_API_KEY = "AQ.Ab8RN6LbZpd0ZwBZBjvQP-LM9JM..." # Ўзингизнинг калитингиз
TELEGRAM_TOKEN = "9270595335:AAFrPS1w1aEo5kUFUC7e9xnliR0DVal2yfM"
TELEGRAM_CHAT_ID = "5955129098"

# 2. Клиентни созлаш
client = genai.Client(api_key=GEMINI_API_KEY)
processed_urls = set()

SYSTEM_PROMPT = """Sen global moliyaviy tahlilchi va avtomatlashtirilgan savdo agentisan.
Sizning vazifangiz XAU/USD (Oltin) va EUR/USD juftliklari bo'yicha yangiliklarni tahlil qilish.
Faqatgina agar yangilik kuchli savdo signaliga ega bo'lsa, JSON formatida javob bering:
{
  "signal": true,
  "asset": "XAU/USD yoki EUR/USD",
  "direction": "BUY yoki SELL",
  "reason": "Qisqacha asosnoma"
}
Agar muhim yangilik bo'lmasa, "signal": false qaytaring.
"""

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram хатоси: {e}")

def analyze_news(news_text):
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash-exp',
            contents=f"{SYSTEM_PROMPT}\n\nYangilik: {news_text}"
        )
        return response.text
    except Exception as e:
        print(f"Gemini xatosi: {e}")
        return None

def fetch_and_check_news():
    url = f"https://newsapi.org/v2/everything?q=gold+forex+EURUSD&language=en&sortBy=publishedAt&apiKey={NEWS_API_KEY}"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        articles = data.get("articles", [])
        
        for article in articles[:3]:
            news_url = article.get("url")
            if news_url and news_url not in processed_urls:
                processed_urls.add(news_url)
                title = article.get("title", "")
                description = article.get("description", "")
                content_text = f"{title}. {description}"
                
                print(f"Текширилаётган янгилик: {title}")
                ai_response = analyze_news(content_text)
                
                if ai_response:
                    print(f"Gemini жавоби: {ai_response}")
                    if "true" in ai_response.lower() and "signal" in ai_response.lower():
                        send_telegram_message(f"🚨 **Янги Савдо Сигнали!**\n\n{ai_response}")
    except Exception as e:
        print(f"Yangiliklarni olishda xatolik: {e}")

if __name__ == "__main__":
    print("Agent muvaffaqiyatli ishga tushdi va bozorlarni kuzatmoqda...")
    send_telegram_message("🤖 Savdo agenti ishga tushdi va XAU/USD ҳамда EUR/USD янгиликларини кузатмоқда!")
    
    while True:
        fetch_and_check_news()
        time.sleep(300)
