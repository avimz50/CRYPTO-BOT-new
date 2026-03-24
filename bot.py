import os
import json
import ccxt
import telebot
import time
import threading
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

# רשימה קבועה לאסטרטגיית EMA בלבד
ENERGY_GEO = ['PAXG/USDT', 'POWR/USDT', 'HNT/USDT']

# נתיב לקובץ המועמדים החמים (לדאשבורד)
HOT_CANDIDATES_FILE = 'artifacts/bot-dashboard/public/hot_candidates.json'

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

def get_data(symbol, timeframe='1h', limit=250):
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    return df

# --- שלב 1+2: משפך — מועמדים חמים ---

def get_hot_candidates():
    """שלב 1: שליפת כל זוגות USDT מ-Bitget
       שלב 2: סינון Top 15 Gainers עם ווליום $1M+ ב-24 שעות"""
    try:
        print("Fetching all tickers for hot candidates...")
        tickers = exchange.fetch_tickers()

        candidates = []
        for symbol, ticker in tickers.items():
            if not symbol.endswith('/USDT'):
                continue
            change_pct = ticker.get('percentage', None)
            volume_usd = ticker.get('quoteVolume', 0) or 0
            last_price = ticker.get('last', 0) or 0

            # סינון: שינוי חיובי + ווליום $1M+ + מחיר קיים
            if change_pct is None or change_pct <= 0:
                continue
            if volume_usd < 1_000_000:
                continue
            if last_price <= 0:
                continue

            candidates.append({
                'symbol': symbol,
                'change_pct': round(change_pct, 2),
                'volume_usd': round(volume_usd),
                'price': last_price
            })

        # מיון לפי שינוי % יורד, לקיחת Top 15
        candidates.sort(key=lambda x: x['change_pct'], reverse=True)
        top_15 = candidates[:15]

        # שמירה לדאשבורד
        data = {
            'updated': datetime.now().strftime('%H:%M:%S'),
            'count': len(top_15),
            'candidates': top_15
        }
        os.makedirs(os.path.dirname(HOT_CANDIDATES_FILE), exist_ok=True)
        with open(HOT_CANDIDATES_FILE, 'w') as f:
            json.dump(data, f)

        print(f"Hot candidates found: {[c['symbol'] for c in top_15]}")
        return top_15

    except Exception as e:
        print(f"Hot candidates error: {e}")
        return []

# --- אסטרטגיות ---

def check_rsi_trend(df):
    """RSI < 40 + מחיר מעל EMA 200"""
    rsi_series = ta.rsi(df['close'], length=14)
    ema200_series = ta.ema(df['close'], length=200)
    if rsi_series is None or ema200_series is None:
        return False, ""
    rsi = rsi_series.iloc[-1]
    ema200 = ema200_series.iloc[-1]
    price = df['close'].iloc[-1]
    if pd.isna(rsi) or pd.isna(ema200):
        return False, ""
    if rsi < 40 and price > ema200:
        return True, f"RSI Oversold ({rsi:.1f}) + Above EMA200"
    return False, ""

def check_breakout(df):
    """פריצת שיא 24 שעות + ווליום ×1.5"""
    if len(df) < 26:
        return False, ""
    current_price = df['close'].iloc[-1]
    high_24h = df['high'].iloc[-25:-1].max()
    breakout_pct = (current_price - high_24h) / high_24h * 100
    current_vol = df['volume'].iloc[-1]
    avg_vol = df['volume'].iloc[-21:-1].mean()
    volume_confirmed = avg_vol > 0 and current_vol > avg_vol * 1.5
    if breakout_pct >= 0.5 and volume_confirmed:
        return True, f"24H Breakout +{breakout_pct:.1f}% + Volume ×{current_vol/avg_vol:.1f}"
    return False, ""

def check_energy_trend(df):
    """EMA 9 חוצה מעל EMA 21 + ווליום ×1.3"""
    ema9 = ta.ema(df['close'], length=9)
    ema21 = ta.ema(df['close'], length=21)
    if ema9 is None or ema21 is None:
        return False, ""
    if pd.isna(ema9.iloc[-2]) or pd.isna(ema9.iloc[-1]):
        return False, ""
    if pd.isna(ema21.iloc[-2]) or pd.isna(ema21.iloc[-1]):
        return False, ""
    cross = ema9.iloc[-2] < ema21.iloc[-2] and ema9.iloc[-1] > ema21.iloc[-1]
    if not cross:
        return False, ""
    current_vol = df['volume'].iloc[-1]
    avg_vol = df['volume'].iloc[-21:-1].mean()
    if avg_vol == 0:
        return False, ""
    vol_ratio = current_vol / avg_vol
    if vol_ratio >= 1.3:
        return True, f"EMA Cross (9/21) + Volume ×{vol_ratio:.1f}"
    return False, ""

# --- ניהול עסקאות דמו ---

def get_strategy_label(reason):
    """מחזיר שם אסטרטגיה קריא לפי תוכן הסיבה"""
    if 'RSI' in reason:
        return "📉 RSI Recovery"
    elif 'Breakout' in reason:
        return "🚀 High Volume Breakout"
    elif 'EMA Cross' in reason:
        return "📈 EMA Trend Cross"
    return "📊 Signal"

def open_demo_trade(symbol, price, candle_low, reason):
    # SL = תחתית הנר (בפחות חיץ קטן), אבל לא יותר מ-2% מתחת לכניסה
    sl = max(candle_low * 0.999, price * 0.98)
    # TP = כניסה + (סיכון × 3) — יחס 1:3 אמיתי לפי ה-SL בפועל
    risk = price - sl
    tp = price + (risk * 3)

    sl_pct = (price - sl) / price * 100
    tp_pct = (tp - price) / price * 100

    trade = {
        'symbol': symbol,
        'entry': price,
        'sl': sl,
        'tp': tp,
        'status': 'OPEN'
    }
    active_trades.append(trade)

    strategy_label = get_strategy_label(reason)

    msg = f"🚀 *עסקת דמו חדשה!*\n\n"
    msg += f"*{strategy_label}*\n"
    msg += f"מטבע: `{symbol}`\n"
    msg += f"פירוט: {reason}\n\n"
    msg += f"מחיר כניסה: `{price:.4f}`\n"
    msg += f"🛑 סטופ לוס: `{sl:.4f}` (-{sl_pct:.1f}% · תחתית נר)\n"
    msg += f"🎯 יעד (1:3): `{tp:.4f}` (+{tp_pct:.1f}%)\n"
    msg += f"💰 גודל פוזיציה: $20"
    send_msg(msg)

def track_trades():
    global active_trades, daily_stats

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
    msg += f"_הבוט פעיל ומסרוק כל שעה_ 🤖"

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

# --- פקודות טלגרם ---

@bot.message_handler(commands=['test'])
def handle_test(message):
    try:
        send_msg("🧪 *מריץ איתות טסט ל-BTC/USDT...*")
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        # נר טסט: תחתית מדומה של 1% מתחת למחיר
        candle_low = price * 0.99
        open_demo_trade('BTC/USDT', price, candle_low, 'RSI Oversold (38.4) + Above EMA200 [TEST]')
        print(f"Test signal sent for BTC/USDT at {price}")
    except Exception as e:
        send_msg(f"❌ שגיאה בטסט: {e}")

@bot.message_handler(commands=['status'])
def handle_status(message):
    if not active_trades:
        send_msg("📭 *אין עסקאות פעילות כרגע.*")
        return
    msg = f"📋 *עסקאות פעילות ({len(active_trades)}):*\n\n"
    for t in active_trades:
        msg += f"• `{t['symbol']}` — כניסה: {t['entry']:.4f} | SL: {t['sl']:.4f} | TP: {t['tp']:.4f}\n"
    send_msg(msg)

@bot.message_handler(commands=['report'])
def handle_report(message):
    send_daily_report()

def start_telegram_polling():
    print("Telegram polling started...")
    while True:
        try:
            bot.polling(non_stop=True, timeout=30, long_polling_timeout=30)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)

# --- הלולאה הראשית ---

def main():
    global last_daily_report_date

    polling_thread = threading.Thread(target=start_telegram_polling, daemon=True)
    polling_thread.start()

    send_msg("🤖 *הבוט התחיל לסרוק ב-Replit!*\n\nפקודות זמינות:\n/test — איתות BTC מזויף\n/status — עסקאות פעילות\n/report — דוח יומי")

    while True:
        try:
            # מעקב עסקאות קיימות
            track_trades()

            # בדיקת דוח יומי
            check_daily_report()

            now_str = datetime.now().strftime('%H:%M:%S')

            # ═══════════════════════════════════════
            # שלב 1+2: שליפת מועמדים חמים (Funnel)
            # ═══════════════════════════════════════
            hot_candidates = get_hot_candidates()
            hot_symbols = [c['symbol'] for c in hot_candidates]

            send_msg(
                f"🔍 *סריקה שעתית* — {now_str}\n"
                f"🌡️ מועמדים חמים: {len(hot_symbols)} מטבעות\n"
                f"⚡ EMA גיאו: {len(ENERGY_GEO)} מטבעות קבועים"
            )

            # ═══════════════════════════════════════
            # שלב 3: סריקה עמוקה — RSI + Breakout
            # על המועמדים החמים בלבד
            # ═══════════════════════════════════════
            for candidate in hot_candidates:
                symbol = candidate['symbol']
                if any(t['symbol'] == symbol for t in active_trades):
                    continue
                try:
                    df = get_data(symbol)
                    price = df['close'].iloc[-1]

                    candle_low = df['low'].iloc[-1]

                    # בדיקת RSI + EMA200
                    signal, reason = check_rsi_trend(df)
                    if signal:
                        open_demo_trade(symbol, price, candle_low, reason)
                        continue

                    # בדיקת פריצת 24 שעות
                    signal, reason = check_breakout(df)
                    if signal:
                        open_demo_trade(symbol, price, candle_low, reason)

                except Exception as e:
                    print(f"Error scanning {symbol}: {e}")

            # ═══════════════════════════════════════
            # אסטרטגיית EMA — רשימה קבועה בלבד
            # ═══════════════════════════════════════
            for symbol in ENERGY_GEO:
                if any(t['symbol'] == symbol for t in active_trades):
                    continue
                try:
                    df = get_data(symbol)
                    price = df['close'].iloc[-1]
                    candle_low = df['low'].iloc[-1]
                    signal, reason = check_energy_trend(df)
                    if signal:
                        open_demo_trade(symbol, price, candle_low, reason)
                except Exception as e:
                    print(f"Error scanning {symbol}: {e}")

            print("Scan complete. Waiting 1 hour...")
            time.sleep(3600)

        except Exception as e:
            print(f"Main Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
