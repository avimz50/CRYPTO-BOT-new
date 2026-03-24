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

# --- פרמטרי מינוף (דמו) ---
LEVERAGE       = 10          # מינוף 10x
MARGIN         = 50          # בטחון ($) לכל עסקה
POSITION_SIZE  = MARGIN * LEVERAGE   # $500 נשלט

# רשימה למעקב אחרי עסקאות דמו פתוחות
active_trades = []

# מעקב אחרי עסקאות שנסגרו היום
daily_stats = {
    'wins': 0,
    'losses': 0,
    'total_pnl': 0.0,   # רווח/הפסד כולל ($) עם מינוף
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
    """RSI חוצה מעל 35 (Turnaround) + מחיר מעל EMA 200 + ווליום ×1.5"""
    rsi_series = ta.rsi(df['close'], length=14)
    ema200_series = ta.ema(df['close'], length=200)
    if rsi_series is None or ema200_series is None:
        return False, ""
    rsi_prev = rsi_series.iloc[-2]
    rsi_curr = rsi_series.iloc[-1]
    ema200 = ema200_series.iloc[-1]
    price = df['close'].iloc[-1]
    if pd.isna(rsi_prev) or pd.isna(rsi_curr) or pd.isna(ema200):
        return False, ""
    # תנאי Turnaround: RSI חצה מעל 35 בנר האחרון
    rsi_cross = rsi_prev < 35 and rsi_curr > 35
    if not rsi_cross:
        return False, ""
    if price <= ema200:
        return False, ""
    # אישור ווליום: נר נוכחי × 1.5 מעל ממוצע 10 נרות אחרונים
    current_vol = df['volume'].iloc[-1]
    avg_vol_10 = df['volume'].iloc[-11:-1].mean()
    if avg_vol_10 == 0 or current_vol < avg_vol_10 * 1.5:
        return False, ""
    vol_ratio = current_vol / avg_vol_10
    return True, f"RSI Turnaround ({rsi_prev:.1f}→{rsi_curr:.1f}) + EMA200 + Vol ×{vol_ratio:.1f}"

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
    # ── מחירי כניסה / יציאה ──
    sl      = max(candle_low * 0.999, price * 0.97)   # SL: תחתית נר, תקרה 3%
    tp_full = price * 1.09                             # TP מלא: 9%
    tp1     = price * 1.05                             # TP1 (50%): 5%
    be_lvl  = price * 1.03                             # הפעלת Break Even: 3%

    sl_pct  = round((price - sl)      / price * 100, 2)
    tp_pct  = round((tp_full - price) / price * 100, 2)

    # ── P&L אפשרי עם מינוף ──
    half = POSITION_SIZE / 2                           # $250 — חצי פוזיציה
    tp1_pnl     = round(half * 0.05, 2)               # +$12.50 ב-TP1
    tp_full_pnl = round(half * 0.09, 2)               # +$22.50 ב-TP מלא
    max_profit  = round(tp1_pnl + tp_full_pnl, 2)     # +$35.00
    sl_loss     = round(POSITION_SIZE * sl_pct / 100, 2)

    trade = {
        'symbol':       symbol,
        'entry':        price,
        'sl':           sl,
        'tp':           tp_full,
        'sl_pct':       sl_pct,
        'tp_pct':       tp_pct,
        # שלבי ניהול
        'phase':        'initial',   # initial → trailing
        'be_triggered': False,
        'tp1_triggered':False,
        'tp1_pnl':      0.0,         # רווח נעול מ-TP1
        'peak_price':   price,       # שיא מחיר (לטריילינג)
        'trailing_sl':  None,        # SL דינמי אחרי TP1
    }
    active_trades.append(trade)

    strategy_label = get_strategy_label(reason)

    msg  = f"🚀 *עסקת דמו חדשה!*\n\n"
    msg += f"*{strategy_label}*\n"
    msg += f"מטבע: `{symbol}`\n"
    msg += f"פירוט: {reason}\n\n"
    msg += f"מחיר כניסה: `{price:.4f}`\n"
    msg += f"🛑 SL (-3%):  `{sl:.4f}`\n"
    msg += f"🔒 BE (+3%):  `{be_lvl:.4f}` ← SL עובר לכניסה\n"
    msg += f"🎯 TP1 (+5%): `{tp1:.4f}` ← סגירת 50%\n"
    msg += f"🎯 TP  (+9%): `{tp_full:.4f}` ← שאר 50% + Trailing\n\n"
    msg += f"{'─' * 26}\n"
    msg += f"💼 *Leverage: {LEVERAGE}x (Isolated)*\n"
    msg += f"💰 בטחון: ${MARGIN} · נשלט: ${POSITION_SIZE}\n"
    msg += f"📈 מקסימום רווח: *+${max_profit}* (TP1 + TP)\n"
    msg += f"📉 מקסימום הפסד: *-${sl_loss}* (-{sl_pct}%)"
    send_msg(msg)

def track_trades():
    global active_trades, daily_stats

    if daily_stats['date'] != date.today():
        daily_stats = {'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'date': date.today()}

    half = POSITION_SIZE / 2   # $250 — חצי פוזיציה אחרי TP1

    for trade in active_trades[:]:
        try:
            ticker        = exchange.fetch_ticker(trade['symbol'])
            current_price = ticker['last']
            entry         = trade['entry']
            sym           = trade['symbol']

            # ════════════════════════════════════════
            # שלב: INITIAL — פוזיציה מלאה $500
            # ════════════════════════════════════════
            if trade['phase'] == 'initial':

                # 1. Break Even: מחיר עלה 3% → SL לכניסה
                if not trade['be_triggered'] and current_price >= entry * 1.03:
                    trade['sl']           = entry
                    trade['be_triggered'] = True
                    send_msg(
                        f"🔒 *Break Even מופעל — {sym}*\n"
                        f"מחיר: `{current_price:.4f}` (+3%)\n"
                        f"SL הועבר לכניסה: `{entry:.4f}`\n"
                        f"💼 {LEVERAGE}x Isolated · ההון מוגן!"
                    )

                # 2. TP1: מחיר עלה 5% → סגור 50%, הפעל Trailing
                if current_price >= entry * 1.05:
                    tp1_pnl = round(half * 0.05, 2)
                    tp1_pct = round(tp1_pnl / MARGIN * 100, 1)
                    trade['tp1_triggered'] = True
                    trade['tp1_pnl']       = tp1_pnl
                    trade['phase']         = 'trailing'
                    trade['peak_price']    = current_price
                    trade['trailing_sl']   = current_price * 0.98
                    daily_stats['total_pnl'] += tp1_pnl
                    send_msg(
                        f"🎯 *TP1 הושג — {sym}!*\n"
                        f"מחיר: `{current_price:.4f}` (+5%)\n"
                        f"50% נסגרו · רווח נעול: *+${tp1_pnl} (+{tp1_pct}%)*\n"
                        f"💼 {LEVERAGE}x · שאר 50% ($250) בטריילינג 2%\n"
                        f"Trailing SL: `{trade['trailing_sl']:.4f}`\n"
                        f"סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    continue   # לא לבדוק SL גנרי באותה איטרציה

                # 3. SL נגע
                if current_price <= trade['sl']:
                    if trade['be_triggered']:
                        # יצאנו ב-Break Even
                        daily_stats['losses']    += 1
                        send_msg(
                            f"🔒 *Break Even — יצאנו ב-{sym}*\n"
                            f"מחיר: `{current_price:.4f}` | כניסה: `{entry:.4f}`\n"
                            f"*ללא הפסד · ההון נשמר*\n"
                            f"💼 {LEVERAGE}x Isolated\n"
                            f"סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                        )
                    else:
                        sl_pct = trade['sl_pct']
                        loss   = round(POSITION_SIZE * sl_pct / 100, 2)
                        loss_pct = round(loss / MARGIN * 100, 1)
                        daily_stats['losses']    += 1
                        daily_stats['total_pnl'] -= loss
                        send_msg(
                            f"🛑 *SL נגע — {sym}*\n"
                            f"כניסה: `{entry:.4f}` → SL: `{trade['sl']:.4f}`\n"
                            f"*הפסד: -${loss} (-{loss_pct}% על מרג'ין)*\n"
                            f"💼 {LEVERAGE}x Isolated · בטחון: ${MARGIN}\n"
                            f"סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                        )
                    active_trades.remove(trade)

            # ════════════════════════════════════════
            # שלב: TRAILING — 50% פוזיציה נותרת ($250)
            # ════════════════════════════════════════
            elif trade['phase'] == 'trailing':

                # עדכן שיא ו-Trailing SL
                if current_price > trade['peak_price']:
                    trade['peak_price']  = current_price
                    trade['trailing_sl'] = round(current_price * 0.98, 6)

                # TP מלא (9%) — סגור את השאר
                if current_price >= entry * 1.09:
                    tp_pnl  = round(half * 0.09, 2)
                    tp_pct  = round(tp_pnl / MARGIN * 100, 1)
                    total   = round(trade['tp1_pnl'] + tp_pnl, 2)
                    daily_stats['wins']      += 1
                    daily_stats['total_pnl'] += tp_pnl
                    send_msg(
                        f"✅ *TP מלא הושג — {sym}!*\n"
                        f"מחיר: `{current_price:.4f}` (+9%)\n"
                        f"שאר 50% נסגרו: *+${tp_pnl} (+{tp_pct}%)*\n"
                        f"TP1 + TP סה\"כ: *+${total}*\n"
                        f"💼 {LEVERAGE}x Isolated\n"
                        f"סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    active_trades.remove(trade)

                # Trailing Stop נגע
                elif current_price <= trade['trailing_sl']:
                    exit_pct = (current_price - entry) / entry * 100
                    half_pnl = round(half * exit_pct / 100, 2)
                    total    = round(trade['tp1_pnl'] + half_pnl, 2)
                    if half_pnl >= 0:
                        daily_stats['wins'] += 1
                    else:
                        daily_stats['losses'] += 1
                    daily_stats['total_pnl'] += half_pnl
                    sign = "+" if half_pnl >= 0 else ""
                    send_msg(
                        f"📍 *Trailing Stop נגע — {sym}*\n"
                        f"שיא: `{trade['peak_price']:.4f}` → יציאה: `{current_price:.4f}`\n"
                        f"50% נסגרו: *{sign}${half_pnl}*\n"
                        f"TP1 + Trailing סה\"כ: *{'+' if total>=0 else ''}${total}*\n"
                        f"💼 {LEVERAGE}x Isolated\n"
                        f"סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    active_trades.remove(trade)

        except Exception as e:
            print(f"Track error {trade.get('symbol','?')}: {e}")

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

    total_pnl  = daily_stats.get('total_pnl', 0.0)
    pnl_sign   = "+" if total_pnl >= 0 else ""
    pnl_on_margin = round(total_pnl / MARGIN * 100, 1) if MARGIN else 0

    msg = f"📊 *דוח יומי — {now}*\n"
    msg += f"{'─' * 28}\n\n"
    msg += f"*📂 עסקאות פתוחות ({len(active_trades)}):*\n"
    msg += trades_lines + "\n"
    msg += f"*📈 תוצאות 24 שעות אחרונות:*\n"
    msg += f"  ✅ רווחים: {daily_stats['wins']}\n"
    msg += f"  ❌ הפסדים: {daily_stats['losses']}\n"
    msg += f"  🎯 אחוז הצלחה: {win_rate:.0f}%\n\n"
    msg += f"*💼 P&L כולל (10x Isolated):*\n"
    msg += f"  {pnl_sign}${total_pnl:.2f} ({pnl_sign}{pnl_on_margin}% על מרג'ין)\n\n"
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
