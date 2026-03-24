import os
import ccxt
import telebot
import time
import pandas as pd
import pandas_ta as ta

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
    'ENERGY_GEO': ['PAXG/USDT'] # זהב כנכס מקלט/אנרגיה
}

# רשימה למעקב אחרי עסקאות דמו פתוחות
active_trades = []

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
    # אסטרטגיה: RSI נמוך (מכירת יתר) במגמה עולה
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
    # אסטרטגיה: פריצת שיא של 24 נרות (יום)
    if len(df) < 26:
        return False, ""
    current_price = df['close'].iloc[-1]
    high_24h = df['high'].iloc[-25:-1].max()
    if current_price > high_24h:
        return True, "24H High Breakout"
    return False, ""

def check_energy_trend(df):
    # אסטרטגיה: חציית ממוצעים EMA 9/21
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
    # חישוב יחס 1:3 (סטופ של 2%, יעד של 6%)
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
    
    msg = f"🚀 **עסקת דמו חדשה!**\n\n"
    msg += f"מטבע: `{symbol}`\nסיבה: {reason}\n"
    msg += f"מחיר כניסה: {price:.4f}\n"
    msg += f"🛑 סטופ לוס: {sl:.4f}\n"
    msg += f"🎯 יעד (1:3): {tp:.4f}\n"
    msg += f"💰 גודל פוזיציה: $20"
    send_msg(msg)

def track_trades():
    global active_trades
    for trade in active_trades[:]:
        ticker = exchange.fetch_ticker(trade['symbol'])
        current_price = ticker['last']
        
        if current_price >= trade['tp']:
            send_msg(f"✅ **רווח (TP) הושג ב-{trade['symbol']}!**\nרווח מוערך: $1.20")
            active_trades.remove(trade)
        elif current_price <= trade['sl']:
            send_msg(f"🛑 **הפסד (SL) ב-{trade['symbol']}**\nהפסד מוערך: $0.40")
            active_trades.remove(trade)

# --- הלולאה הראשית ---

def main():
    send_msg("🤖 הבוט התחיל לסרוק ב-Replit...")
    while True:
        try:
            # מעקב אחרי עסקאות קיימות
            track_trades()
            
            # סריקה לאיתותים חדשים
            for category, symbols in WATCHLIST.items():
                for symbol in symbols:
                    # בדיקה אם כבר יש עסקה פתוחה על המטבע הזה
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
            
            print("Scan complete. Waiting 15 minutes...")
            time.sleep(900) # סריקה כל 15 דקות
            
        except Exception as e:
            print(f"Main Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
