import os
import ccxt
import telebot
import time
import pandas as pd
import pandas_ta as ta
from datetime import datetime, date

# --- הגדרות וחיבורים ---
exchange = ccxt.bitget({
    'apiKey': os.environ['BITGET_KEY'],
    'secret': os.environ['BITGET_SECRET'],
    'password': os.environ['BITGET_PW'],
    'enableRateLimit': True,
})

bot = telebot.TeleBot(os.environ['TELEGRAM_TOKEN'])
CHAT_ID = os.environ['CHAT_ID']

# --- רשימות מעקב (Watchlist) ---
WATCHLIST = {
    'TOP_10': ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'],
    'AI_GEMS': ['FET/USDT', 'RENDER/USDT', 'NEAR/USDT'],
    'ENERGY_GEO': ['PAXG/USDT']
}

# רשימה למעקב אחרי עסקאות דמו פתוחות
active_trades = []

# מעקב אחרי עסקאות שנסגרו היום
daily_stats = {
    'wins': 0,
    'losses': 0,
    'date': date.today()
}

# שמירת תאריך הדוח האחרון שנשלח
last_daily_report_date = None

def send_msg(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_data(symbol, timeframe='1h', limit=100):
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    return df

# --- אסטרטגיות ---

def check_top_10(df):
    rsi_series = ta.rsi(df['close'], length=14)
    ema200_series = ta.ema(df['close'], length=200)
    if rsi_series is None or ema200_series is None:
        return False, ""
    rsi = rsi_series.iloc[-1]
    ema200 = ema200_series.iloc[-1]
    price = df['close'].iloc[-1]
    if pd.isna(rsi) or pd.isna(ema200):
        return False, ""
    if rsi < 35 and price > ema200:
        return True, "RSI Oversold + Bullish Trend"
    return False, ""

def check_ai_breakout(df):
    if len(df) < 26:
        return False, ""
    current_price = df['close'].iloc[-1]
    high_24h = df['high'].iloc[-25:-1].max()
    # אישור ווליום — הנר הנוכחי חייב להיות מעל פי 1.5 מממוצע 20 הנרות האחרונים
    current_vol = df['volume'].iloc[-1]
    avg_vol = df['volume'].iloc[-21:-1].mean()
    volume_confirmed = current_vol > avg_vol * 1.5
    if current_price > high_24h and volume_confirmed:
        return True, f"24H High Breakout + Volume ×{current_vol/avg_vol:.1f}"
    return False, ""

def check_energy_trend(df):
    ema9 = ta.ema(df['close'], length=9)
    ema21 = ta.ema(df['close'], length=21)
    if ema9 is None or ema21 is None:
        return False, ""
    if pd.isna(ema9.iloc[-2]) or pd.isna(ema9.iloc[-1]):
        return False, ""
    if pd.isna(ema21.iloc[-2]) or pd.isna(ema21.iloc[-1]):
        return False, ""
    if ema9.iloc[-2] < ema21.iloc[-2] and ema9.iloc[-1] > ema21.iloc[-1]:
        return True, "EMA Cross (9/21)"
    return False, ""

# --- ניהול עסקאות דמו ---

def open_demo_trade(symbol, price, reason):
    sl = price * 0.98
    tp = price * 1.06
    trade = {
        'symbol': symbol,
        'entry': price,
        'sl': sl,
        'tp': tp,
        'status': 'OPEN'
    }
    active_trades.append(trade)

    msg = f"🚀 *עסקת דמו חדשה!*\n\n"
    msg += f"מטבע: `{symbol}`\nסיבה: {reason}\n"
    msg += f"מחיר כניסה: {price:.4f}\n"
    msg += f"🛑 סטופ לוס: {sl:.4f}\n"
    msg += f"🎯 יעד (1:3): {tp:.4f}\n"
    msg += f"💰 גודל פוזיציה: $20"
    send_msg(msg)

def track_trades():
    global active_trades, daily_stats

    # אם עבר יום, אפס את הסטטיסטיקות
    if daily_stats['date'] != date.today():
        daily_stats = {'wins': 0, 'losses': 0, 'date': date.today()}

    for trade in active_trades[:]:
        ticker = exchange.fetch_ticker(trade['symbol'])
        current_price = ticker['last']

        if current_price >= trade['tp']:
            send_msg(f"✅ *רווח (TP) הושג ב-{trade['symbol']}!*\nרווח מוערך: $1.20")
            daily_stats['wins'] += 1
            active_trades.remove(trade)
        elif current_price <= trade['sl']:
            send_msg(f"🛑 *הפסד (SL) ב-{trade['symbol']}*\nהפסד מוערך: $0.40")
            daily_stats['losses'] += 1
            active_trades.remove(trade)

# --- דוח יומי ---

def check_api_connection():
    try:
        exchange.fetch_ticker('BTC/USDT')
        return True
    except Exception:
        return False

def send_daily_report():
    now = datetime.now().strftime('%d/%m/%Y %H:%M')
    total_closed = daily_stats['wins'] + daily_stats['losses']
    win_rate = (daily_stats['wins'] / total_closed * 100) if total_closed > 0 else 0
    api_ok = check_api_connection()
    api_status = "✅ פעיל" if api_ok else "❌ בעיה בחיבור!"

    # עסקאות פעילות
    if active_trades:
        trades_lines = ""
        for t in active_trades:
            trades_lines += f"  • `{t['symbol']}` — כניסה: {t['entry']:.4f}\n"
    else:
        trades_lines = "  _אין עסקאות פעילות כרגע_\n"

    msg = f"📊 *דוח יומי — {now}*\n"
    msg += f"{'─' * 28}\n\n"
    msg += f"*📂 עסקאות פתוחות ({len(active_trades)}):*\n"
    msg += trades_lines + "\n"
    msg += f"*📈 תוצאות 24 שעות אחרונות:*\n"
    msg += f"  ✅ רווחים: {daily_stats['wins']}\n"
    msg += f"  ❌ הפסדים: {daily_stats['losses']}\n"
    msg += f"  🎯 אחוז הצלחה: {win_rate:.0f}%\n\n"
    msg += f"*🔌 חיבור Bitget API:* {api_status}\n"
    msg += f"{'─' * 28}\n"
    msg += f"_הבוט פעיל ומסרוק כל 15 דקות_ 🤖"

    send_msg(msg)
    print(f"Daily report sent at {now}")

def check_daily_report():
    global last_daily_report_date
    now = datetime.now()
    today = date.today()

    if now.hour == 9 and now.minute < 15:
        if last_daily_report_date != today:
            send_daily_report()
            last_daily_report_date = today

# --- הלולאה הראשית ---

def main():
    global last_daily_report_date
    send_msg("🤖 הבוט התחיל לסרוק ב-Replit...")
    while True:
        try:
            # מעקב אחרי עסקאות קיימות
            track_trades()

            # בדיקה אם צריך לשלוח דוח יומי (09:00)
            check_daily_report()

            # הודעת התחלת סריקה
            now = datetime.now().strftime('%H:%M:%S')
            send_msg(f"🔍 *סריקה שעתית* — {now}\nבודק {sum(len(v) for v in WATCHLIST.values())} מטבעות...")

            # סריקה לאיתותים חדשים
            for category, symbols in WATCHLIST.items():
                for symbol in symbols:
                    if any(t['symbol'] == symbol for t in active_trades):
                        continue

                    df = get_data(symbol)
                    price = df['close'].iloc[-1]
                    signal = False
                    reason = ""

                    if category == 'TOP_10':
                        signal, reason = check_top_10(df)
                    elif category == 'AI_GEMS':
                        signal, reason = check_ai_breakout(df)
                    elif category == 'ENERGY_GEO':
                        signal, reason = check_energy_trend(df)

                    if signal:
                        open_demo_trade(symbol, price, reason)

            print("Scan complete. Waiting 1 hour...")
            time.sleep(3600)

        except Exception as e:
            print(f"Main Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
