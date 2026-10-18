import re
import os
import feedparser
import pandas as pd
import numpy as np
import ccxt
from google import genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

CONFIG = {
    # يتم سحب المفاتيح أماناً من إعدادات السيرفر أو البيئة
    "telegram_token": os.environ.get("TELEGRAM_TOKEN"),
    "gemini_api_key": os.environ.get("GEMINI_API_KEY"),
    "timeframe": "1h",
    "news_feeds": [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/"
    ],
    "fallback_models": [
        "gemini-3.8-flash",
        "gemini-2.5-flash",
        "gemini-1.5-flash"
    ]
}


class MarketAndNewsEngine:
    def __init__(self):
        self.exchange = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
        self.scan_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT", "BNB/USDT"]

    def get_market_snapshot(self, symbol: str, timeframe: str) -> dict:
        ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=50)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        df['sma_fast'] = df['close'].rolling(window=9).mean()
        df['sma_slow'] = df['close'].rolling(window=21).mean()

        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        df['rsi'] = 100 - (100 / (1 + (gain / loss)))

        current_price = float(df.iloc[-1]['close'])
        closed = df.iloc[-2]

        return {
            "symbol": symbol,
            "current_price": current_price,
            "fast_ma": float(closed['sma_fast']),
            "slow_ma": float(closed['sma_slow']),
            "rsi": float(closed['rsi']),
            "support_24h": float(df['low'].tail(24).min()),
            "resistance_24h": float(df['high'].tail(24).max())
        }

    def get_market_overview(self) -> str:
        overview = []
        for sym in self.scan_symbols:
            try:
                m = self.get_market_snapshot(sym, CONFIG["timeframe"])
                trend = "صاعد" if m['fast_ma'] > m['slow_ma'] else "هابط"
                overview.append(f"- {sym}: السعر = {m['current_price']}, الاتجاه = {trend}, RSI = {m['rsi']:.1f}, الدعم = {m['support_24h']}, المقاومة = {m['resistance_24h']}")
            except Exception:
                continue
        return "\n".join(overview)

    def get_headlines(self) -> str:
        headlines = []
        for url in CONFIG["news_feeds"]:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    headlines.append(entry.get("title", ""))
            except Exception:
                continue
        return "\n".join([f"- {h}" for h in headlines[:5]])


engine = MarketAndNewsEngine()
client = genai.Client(api_key=CONFIG["gemini_api_key"]) if CONFIG["gemini_api_key"] else None


def extract_symbol(user_text: str) -> str:
    text_low = user_text.lower()
    coin_pairs = [
        ("doge", "DOGE/USDT"), ("دوج", "DOGE/USDT"),
        ("sol", "SOL/USDT"), ("سولانا", "SOL/USDT"),
        ("btc", "BTC/USDT"), ("بيتكوين", "BTC/USDT"),
        ("eth", "ETH/USDT"), ("ايثيريوم", "ETH/USDT"),
        ("xrp", "XRP/USDT"), ("bnb", "BNB/USDT")
    ]
    for word, sym in coin_pairs:
        if word in text_low:
            return sym

    match = re.search(r'\b([A-Za-z]{2,6})/USDT\b', user_text.upper())
    if match:
        return f"{match.group(1)}/USDT"
    return ""


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_question = update.message.text
    await update.message.reply_text("⏳ جاري قراءة السوق والأخبار والتفكير في رد مناسب...")

    try:
        market_overview = engine.get_market_overview()
        headlines = engine.get_headlines()
        
        specific_symbol = extract_symbol(user_question)
        specific_data = ""
        if specific_symbol:
            m = engine.get_market_snapshot(specific_symbol, CONFIG["timeframe"])
            specific_data = f"\n[بيانات تفصيلية للعملة المطلوبة {specific_symbol}]:\n- السعر: {m['current_price']}\n- SMA9: {m['fast_ma']} | SMA21: {m['slow_ma']}\n- RSI: {m['rsi']:.1f}\n- الدعم: {m['support_24h']} | المقاومة: {m['resistance_24h']}\n"
    except Exception as e:
        await update.message.reply_text(f"❌ تعذر جلب البيانات الحية: {e}")
        return

    prompt = f"""
أنت مستشار تداول كمي وخبير كريبتو ذكي وودود تسولف مع صديقك وتتناقش معه بحرية تامة وبدون قيود.
هذه هي بيانات السوق الحية الآن من باينانس:
{market_overview}
{specific_data}

وهذه أحدث عناوين الأخبار المالية:
{headlines}

رسالة أو سؤال المستخدم: "{user_question}"

تعليمات الرد:
1. اسولف معه بطريقة طبيعية، ذكية، ومريحة (بدون جمود أو ردود آلية مكررة).
2. إذا طلب ترشيح عملات أو سأل عن السوق، اقترح عليه أفضل العملات من البيانات أعلاه واشرح له "ليه" اخترتها بناءً على الأرقام الحقيقية.
3. أعطه نصائح عملية واضحة (أين يدخل، أين يضع وقف الخسارة، وأين الهدف).
4. أجب باللغة العربية.
"""

    response = None
    for model_name in CONFIG["fallback_models"]:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            if response and response.text:
                break
        except Exception:
            continue

    if response and response.text:
        await update.message.reply_text(response.text.strip())
    else:
        await update.message.reply_text("❌ حدث خطأ: تعذر توليد الرد من النماذج حالياً.")


def main():
    if not CONFIG["telegram_token"] or not CONFIG["gemini_api_key"]:
        print("❌ تنبيه: تأكد من ضبط متغيرات البيئة TELEGRAM_TOKEN و GEMINI_API_KEY.")
        return

    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = ApplicationBuilder().token(CONFIG["telegram_token"]).request(request).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("🤖 بوت تيليجرام يعمل الآن بنجاح ومستعد لاستقبال رسائلك...")
    app.run_polling()


if __name__ == "__main__":
    main()