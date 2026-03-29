import os
import io
import json
import ccxt
import telebot
import time
import threading
import pandas as pd
import pandas_ta as ta
from datetime import datetime, date, timedelta
from keep_alive import keep_alive, app as flask_app
from flask import jsonify as flask_jsonify
import gdrive_reporter

# ── אזור זמן ישראל (UTC+2/+3 לפי שעון קיץ) ──
os.environ['TZ'] = 'Asia/Jerusalem'
time.tzset()

# ספריות גרף — fallback אם לא קיימות
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import mplfinance as mpf
    CHARTS_ENABLED = True
except ImportError:
    CHARTS_ENABLED = False
    print("mplfinance not available — charts disabled")

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
HOT_CANDIDATES_FILE   = 'artifacts/bot-dashboard/public/hot_candidates.json'
ACTIVE_TRADES_FILE    = 'artifacts/bot-dashboard/public/active_trades.json'
WALLET_FILE           = 'artifacts/bot-dashboard/public/wallet.json'

# --- ארנק וירטואלי ---
STARTING_BALANCE = 200.0   # יתרת פתיחה $200
DASHBOARD_URL       = 'https://python-script-bymzrkhy.replit.app/'

# ─── Fear & Greed Index — cache גלובלי (מתרענן כל שעה) ───────────────────────
_fng_cache = {'value': 50, 'label': 'Neutral', 'ts': 0}

# ─── Kill-Switch State Tracker ─────────────────────────────────────────────────
# None = לא ידוע (הפעלה ראשונה) | True = פעיל | False = כבוי
_kill_switch_active: bool | None = None

def get_fear_greed():
    """מחזיר (value:int, label:str) — Alternative.me API עם cache של שעה."""
    import time as _time
    now = _time.time()
    if now - _fng_cache['ts'] < 3600:
        return _fng_cache['value'], _fng_cache['label']
    try:
        import requests as _req
        r = _req.get('https://api.alternative.me/fng/?limit=1', timeout=5)
        d = r.json()['data'][0]
        _fng_cache.update({'value': int(d['value']), 'label': d['value_classification'], 'ts': now})
        print(f"  [FNG] עודכן: {_fng_cache['value']} – {_fng_cache['label']}")
    except Exception as e:
        print(f"  [FNG] שגיאה בטעינה: {e} (משתמש ב-cache אחרון)")
    return _fng_cache['value'], _fng_cache['label']

# ─── Global Sentiment Thresholds ──────────────────────────────────────────────
EXTREME_FEAR_THRESHOLD = 13   # Kill-Switch: אין עסקאות חדשות בכלל
FEAR_THRESHOLD         = 30   # Fear Filter: RSI<30 ל-LONG + SL+1%
GREED_THRESHOLD        = 70   # Greed Filter: פוזיציה ×60% + BE@+2%
GREED_EARLY_BE_PCT     = 2.0  # % רווח להפעלת BE מוקדם בחמדנות
FEAR_EXTRA_SL_PCT      = 1.0  # % נוסף ל-SL בתנאי פחד

def sentiment_check(context: str = "scan"):
    """
    בודק את מצב הסנטימנט ומדפיס לוג.
    מחזיר (fng_v:int, label:str, action:str).
    נדרש לוג לכל בדיקת כניסה: 'Sentiment Check: [Value] - [Action Taken]'
    """
    fng_v, lbl = get_fear_greed()
    if fng_v < EXTREME_FEAR_THRESHOLD:
        action = f"KILL-SWITCH — אין עסקאות חדשות (< {EXTREME_FEAR_THRESHOLD})"
    elif fng_v <= FEAR_THRESHOLD:
        action = f"FEAR FILTER — SL +{FEAR_EXTRA_SL_PCT}% · LONG דורש RSI<30"
    elif fng_v >= GREED_THRESHOLD:
        action = f"GREED FILTER — פוזיציה ×60% · BE@+{GREED_EARLY_BE_PCT}%"
    else:
        action = "נייטרלי — אין שינוי"
    print(f"Sentiment Check: {fng_v} [{lbl}] - {action} [{context}]")
    return fng_v, lbl, action


def check_kill_switch_change():
    """
    בודק אם מצב ה-Kill-Switch השתנה מאז הבדיקה הקודמת.
    שולח התראת טלגרם רק כשיש שינוי מצב בפועל.
    """
    global _kill_switch_active
    fng_v, lbl = get_fear_greed()
    now_active = fng_v < EXTREME_FEAR_THRESHOLD

    # הפעלה ראשונה — רק מאתחל, לא שולח
    if _kill_switch_active is None:
        _kill_switch_active = now_active
        print(f"[Kill-Switch] מצב ראשוני: {'ACTIVE' if now_active else 'INACTIVE'} (FNG={fng_v})")
        return

    # אין שינוי — לא עושים כלום
    if now_active == _kill_switch_active:
        return

    # ── שינוי מצב! ──────────────────────────────────────────────────────────────
    _kill_switch_active = now_active

    if now_active:
        # FNG ירד מתחת לסף — Kill-Switch הופעל
        print(f"[Kill-Switch] הופעל! FNG={fng_v} < {EXTREME_FEAR_THRESHOLD}")
        send_msg(
            f"⚠️ *Market Panic Detected*\n\n"
            f"📊 Fear & Greed Index: *{fng_v}* ({lbl})\n"
            f"🛑 *Kill\\-Switch ENABLED*\n\n"
            f"כל פתיחות עסקאות חדשות חסומות לבטיחות\\.\n"
            f"הבוט ממשיך לנטר עסקאות פעילות קיימות כרגיל\\.\n"
            f"_ההגבלה תבוטל אוטומטית כשה\\-FNG יעלה מעל {EXTREME_FEAR_THRESHOLD}_"
        )
    else:
        # FNG עלה מעל הסף — Kill-Switch בוטל
        print(f"[Kill-Switch] בוטל! FNG={fng_v} >= {EXTREME_FEAR_THRESHOLD}")
        send_msg(
            f"✅ *Market Sentiment Recovered*\n\n"
            f"📊 Fear & Greed Index: *{fng_v}* ({lbl})\n"
            f"🟢 *Kill\\-Switch DISABLED*\n\n"
            f"סריקת שוק מלאה חזרה לפעולה\\.\n"
            f"הבוט ימשיך לחפש איתותים בסריקה הבאה\\.\n"
            f"_סריקה הבאה: עד שעה_"
        )

# ═══════════════════════════════════════════════════════════════
# Claude AI Final Filter — GO / NO-GO per signal
# ═══════════════════════════════════════════════════════════════

def claude_filter(symbol: str, direction: str, score: int, breakdown: dict,
                  price: float, timeframe: str, btc_regime: str,
                  fng_v: int, fng_lbl: str) -> tuple[bool, str]:
    """
    מסנן סופי: שולח נתוני האיתות ל-Claude 3.5 Sonnet.
    מחזיר (go: bool, reason: str).
    אם המפתח לא מוגדר / שגיאת API → GO כברירת מחדל (לא חוסם עסקאות).
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        print(f"  [Claude] ANTHROPIC_API_KEY לא מוגדר — דילוג על פילטר")
        return True, "Claude filter skipped (no API key)"

    try:
        import anthropic as _anthropic

        # בניית breakdown קצר לפרומפט
        bd_lines = []
        for k, v in (breakdown or {}).items():
            bd_lines.append(f"  {k}: {v}")
        breakdown_str = "\n".join(bd_lines) if bd_lines else "N/A"

        prompt = (
            f"You are a crypto trading risk validator. Analyze this signal and decide GO or NO-GO.\n\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction}\n"
            f"Timeframe: {timeframe}\n"
            f"Score: {score}/100\n"
            f"Entry Price: {price}\n"
            f"BTC Market Regime: {btc_regime}\n"
            f"Fear & Greed Index: {fng_v} ({fng_lbl})\n"
            f"Score Breakdown:\n{breakdown_str}\n\n"
            f"Rules:\n"
            f"- Trade size: $500 notional, $50 margin, 10x leverage\n"
            f"- SL: 3.5% | TP1: 5.0% | TP Full: 10.5%\n"
            f"- Risk/Reward must be favorable\n"
            f"- Avoid chasing overextended moves\n\n"
            f"Respond with EXACTLY one line: 'GO: <1 sentence reason>' or 'NO-GO: <1 sentence reason>'"
        )

        client = _anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()
        print(f"  [Claude] {symbol} {direction}: {raw}")

        if raw.upper().startswith("GO"):
            reason = raw.split(":", 1)[1].strip() if ":" in raw else raw
            return True, reason
        else:
            reason = raw.split(":", 1)[1].strip() if ":" in raw else raw
            return False, reason

    except Exception as e:
        print(f"  [Claude] שגיאה: {e} — ממשיך ללא פילטר")
        return True, f"Claude error: {e}"


# --- פרמטרי מינוף (דמו) ---
LEVERAGE       = 10          # מינוף 10x
MARGIN         = 50          # בטחון ($) לכל עסקה
POSITION_SIZE  = MARGIN * LEVERAGE   # $500 נשלט

# רשימה למעקב אחרי עסקאות דמו פתוחות
active_trades      = []
trades_lock        = threading.RLock()   # מגן מ-race conditions בין Threads

# לוג עסקאות סגורות (48 שעות אחרונות) לדוח ה-Drive
closed_trades_log  = []

# Audit report — שעות שליחה ומעקב שהוגש
AUDIT_HOURS        = {12}       # 12:00 בצהריים — דוח יומי
_last_audit_hour   = None       # מונע כפילות באותה שעה

# מעקב אחרי עסקאות שנסגרו היום
daily_stats = {
    'wins':         0,
    'losses':       0,
    'total_pnl':    0.0,
    'date':         date.today(),
    'close_reasons': {'TP': 0, 'TP1+Trail': 0, 'Trailing': 0, 'SL': 0, 'BE': 0, 'Manual': 0},
}

# שמירת תאריך הדוח האחרון שנשלח
last_daily_report_date = None

# ── Scan Analysis Report ──────────────────────────────────────────────────────
SCAN_REPORT_FILE = 'artifacts/bot-dashboard/public/last_scan_results.json'

def _reject_reason(score: int, breakdown: str) -> str:
    """הופך breakdown גולמי לסיבת דחייה קריאה לאדם."""
    bd = breakdown.lower()
    if 'veto' in bd:
        return breakdown[:80]
    if score == 0 and not breakdown:
        return "ניקוד 0/100 — שגיאת חישוב"
    parts = []
    try:
        if 'ema=' in bd:
            ema_pts = int(breakdown.split('EMA=')[1].split('/')[0])
            if ema_pts < 35: parts.append(f"EMA חלש ({ema_pts}/35)")
        if 'macd=' in bd:
            macd_pts = int(breakdown.split('MACD=')[1].split('/')[0])
            if macd_pts < 25: parts.append(f"MACD חסר ({macd_pts}/25)")
        if 'rsi=' in bd:
            rsi_pts = int(breakdown.split('RSI=')[1].split('/')[0])
            if rsi_pts < 20: parts.append(f"RSI חלש ({rsi_pts}/20)")
        if 'bb+vol=' in bd:
            bb_pts = int(breakdown.split('BB+Vol=')[1].split('/')[0])
            if bb_pts < 10: parts.append(f"נפח נמוך ({bb_pts}/15)")
    except Exception:
        pass
    reason_str = ", ".join(parts[:2]) if parts else "כלל הפילטרים"
    return f"ניקוד {score}/100 — {reason_str}"


def build_bubble_watch(gainers: list, losers: list, threshold: float = 10.0) -> list:
    """
    מחזיר רשימת מטבעות עם שינוי 24h > threshold% (ברירת מחדל 10%).
    ממוין לפי גודל השינוי המוחלט — לתצפית בלבד, ללא שינוי בציון.
    """
    bubbles = []
    for g in gainers:
        pct = g.get('change_pct', 0)
        if pct > threshold:
            bubbles.append({
                'symbol':     g['symbol'],
                'change_pct': round(pct, 2),
                'direction':  'LONG',
                'price':      g.get('price', 0),
                'volume_usd': g.get('volume_usd', 0),
            })
    for l in losers:
        pct = l.get('change_pct', 0)
        if abs(pct) > threshold:
            bubbles.append({
                'symbol':     l['symbol'],
                'change_pct': round(pct, 2),
                'direction':  'SHORT',
                'price':      l.get('price', 0),
                'volume_usd': l.get('volume_usd', 0),
            })
    bubbles.sort(key=lambda x: abs(x['change_pct']), reverse=True)
    return bubbles[:10]


def save_scan_results(
    total_scanned: int,
    signals_found: int,
    fng_value: int,
    fng_label: str,
    btc_regime: str,
    all_rejections: list,
    system_message: str,
    scan_start_ts: float,
    bubble_watch: list = None,
):
    """שומר last_scan_results.json לאחר כל סריקה."""
    import time as _t
    duration = round(_t.time() - scan_start_ts, 1)

    # top 5 near-misses — הגבוהים ביותר שלא עברו
    near_misses = sorted(all_rejections, key=lambda x: x.get('best_score', 0), reverse=True)[:5]

    # Sentiment impact sentence
    if fng_value < 20:
        sentiment_note = f"Fear & Greed={fng_value} (Extreme Fear) — Kill-Switch הפעיל: כל הסריקות בוטלו"
    elif fng_value <= 30:
        sentiment_note = f"Fear & Greed={fng_value} (Fear) — ל-LONG דרוש RSI<30; SL הורחב ב-{FEAR_EXTRA_SL_PCT}%"
    elif fng_value >= 75:
        sentiment_note = f"Fear & Greed={fng_value} (Extreme Greed) — פוזיציה צומצמה ל-60%; BE מהיר הופעל"
    elif fng_value >= 60:
        sentiment_note = f"Fear & Greed={fng_value} (Greed) — זהירות קלה; ±2 נקודות על ציון"
    else:
        sentiment_note = f"Fear & Greed={fng_value} ({fng_label}) — מצב ניטרלי, אין השפעה על פתיחות"

    report = {
        'scan_time':               datetime.now().isoformat(timespec='seconds'),
        'total_scanned':           total_scanned,
        'signals_found':           signals_found,
        'active_trades_count':     len(active_trades),
        'max_trades':              MAX_TRADES,
        'min_score':               MIN_SCORE,
        'btc_regime':              btc_regime,
        'fng_value':               fng_value,
        'fng_label':               fng_label,
        'market_sentiment_factor': sentiment_note,
        'rejected_coins':          near_misses,
        'system_message':          system_message,
        'scan_duration_s':         duration,
        'bubble_watch':            bubble_watch or [],
    }
    try:
        with open(SCAN_REPORT_FILE, 'w', encoding='utf-8') as f:
            import json as _json
            _json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"Scan report saved → {SCAN_REPORT_FILE}")
    except Exception as e:
        print(f"Failed to save scan report: {e}")

# מניעת שתי סריקות במקביל
_scan_running = False

# Heartbeat — זמן הדוח האחרון (timestamp)
last_heartbeat_time   = None
HEARTBEAT_INTERVAL    = 1800   # 30 דקות בשניות

# ═══════════════════════════════════════════════════════════════
# Flask API — live endpoints (CORS enabled via keep_alive)
# ═══════════════════════════════════════════════════════════════

@flask_app.route('/api/trades')
def api_trades():
    with trades_lock:
        snapshot = list(active_trades)
    return flask_jsonify({
        'updated': datetime.now().strftime('%H:%M:%S'),
        'count':   len(snapshot),
        'trades':  snapshot,
    })

@flask_app.route('/api/wallet')
def api_wallet():
    data = dict(wallet)
    data['equity'] = _get_equity()
    return flask_jsonify(data)

@flask_app.route('/api/hot')
def api_hot():
    try:
        with open(HOT_CANDIDATES_FILE, 'r') as f:
            return flask_jsonify(json.load(f))
    except Exception:
        return flask_jsonify({'updated': '—', 'count': 0, 'candidates': []})

def send_msg(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        print(f"Telegram Error: {e}")


# ═══════════════════════════════════════════════════════════════
# ארנק וירטואלי — Virtual Wallet ($200 starting)
# ═══════════════════════════════════════════════════════════════

wallet: dict = {}

def _get_equity():
    """Total equity = cash balance + margin locked in open trades."""
    return round(wallet.get('balance', STARTING_BALANCE) + len(active_trades) * MARGIN, 2)

def _append_equity_point():
    hist = wallet.setdefault('equity_history', [])
    hist.append({'t': datetime.now().strftime('%m/%d %H:%M'), 'eq': _get_equity()})
    if len(hist) > 120:          # שמירת 120 נקודות (≈10 ימים בסריקה שעתית)
        wallet['equity_history'] = hist[-120:]

def load_wallet():
    global wallet
    try:
        with open(WALLET_FILE, 'r') as f:
            wallet = json.load(f)
        print(f"Wallet loaded: balance=${wallet.get('balance', 0):.2f} equity=${_get_equity():.2f}")
    except Exception:
        wallet = {
            'balance':        STARTING_BALANCE,
            'starting':       STARTING_BALANCE,
            'total_pnl':      0.0,
            'trades_opened':  0,
            'equity_history': [{'t': datetime.now().strftime('%m/%d %H:%M'), 'eq': STARTING_BALANCE}],
        }
        save_wallet()
        print(f"Wallet created fresh: ${STARTING_BALANCE}")

def load_active_trades():
    """טוען עסקאות פעילות מ-JSON לאחר הפעלה מחדש של הבוט."""
    global active_trades
    try:
        with open(ACTIVE_TRADES_FILE, 'r') as f:
            data = json.load(f)
        loaded = data.get('trades', [])
        if loaded:
            with trades_lock:
                active_trades = loaded
            print(f"Active trades loaded: {len(loaded)} trade(s) restored from disk")
        else:
            print("Active trades loaded: none on disk")
    except Exception:
        print("Active trades: no existing file, starting fresh")

def save_wallet():
    try:
        with open(WALLET_FILE, 'w') as f:
            json.dump(wallet, f)
    except Exception as e:
        print(f"Wallet save error: {e}")

def wallet_deduct():
    """קיזוז מרג'ין ($50) בפתיחת עסקה."""
    wallet['balance']       = round(wallet.get('balance', STARTING_BALANCE) - MARGIN, 2)
    wallet['trades_opened'] = wallet.get('trades_opened', 0) + 1
    _append_equity_point()
    save_wallet()

def wallet_credit(pnl_usd: float):
    """זיכוי מרג'ין + P&L בסגירת עסקה."""
    wallet['balance']   = round(wallet.get('balance', STARTING_BALANCE) + MARGIN + pnl_usd, 2)
    wallet['total_pnl'] = round(wallet.get('total_pnl', 0.0) + pnl_usd, 2)
    _append_equity_point()
    save_wallet()

def _log_closed_trade(trade: dict, close_reason: str, pnl_usd: float, close_price: float = None):
    """מוסיף עסקה סגורה ל-closed_trades_log לשימוש בדוח Drive."""
    global closed_trades_log
    record = {
        'symbol':       trade['symbol'],
        'direction':    trade['direction'],
        'timeframe':    trade.get('timeframe', '4H'),
        'entry_price':  trade['entry'],
        'close_price':  close_price or trade.get('current_price', trade['entry']),
        'score':        trade.get('score', 0),
        'rsi':          trade.get('rsi'),
        'ema200':       trade.get('ema200'),
        'score_breakdown': trade.get('score_breakdown', ''),
        'close_reason': close_reason,
        'pnl_usd':      round(pnl_usd, 2),
        'opened_at':    trade.get('opened_at', ''),
        'closed_at':    datetime.now().isoformat(timespec='seconds'),
    }
    closed_trades_log.append(record)
    # שמור רק 48 שעות אחרונות
    cutoff = datetime.now().timestamp() - 48 * 3600
    closed_trades_log = [
        t for t in closed_trades_log
        if datetime.fromisoformat(t['closed_at']).timestamp() >= cutoff
    ]

def wallet_status_text() -> str:
    """מחזיר מחרוזת סטטוס ארנק לטלגרם."""
    bal     = wallet.get('balance', STARTING_BALANCE)
    start   = wallet.get('starting', STARTING_BALANCE)
    pnl     = wallet.get('total_pnl', 0.0)
    equity  = _get_equity()
    pnl_pct = round((equity - start) / start * 100, 1)
    locked  = len(active_trades) * MARGIN
    icon    = "📈" if pnl >= 0 else "📉"
    return (
        f"💼 *ארנק וירטואלי*\n"
        f"יתרה פנויה:   `${bal:.2f}`\n"
        f"נעול בעסקאות: `${locked}`\n"
        f"Total Equity:  `${equity:.2f}`\n"
        f"{icon} P&L כולל: `${pnl:+.2f}` ({pnl_pct:+.1f}% מ-${start:.0f})"
    )


def get_data(symbol, timeframe='1h', limit=250):
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    return df

def generate_chart(df, symbol, entry, sl, tp, direction='LONG'):
    """מייצר גרף נרות עם EMA200, RSI, ווליום וקווי SL/Entry/TP.
       direction='LONG' → ירוק | 'SHORT' → אדום.
       מחזיר BytesIO או None אם נכשל."""
    if not CHARTS_ENABLED:
        return None
    try:
        # ── צבעי נרות לפי כיוון ──
        if direction == 'SHORT':
            candle_up   = '#ef5350'   # אדום — SHORT
            candle_down = '#b71c1c'
            accent      = '#ff5252'   # accent לקו TP/SL
            tp_color    = '#e74c3c'   # TP — מטרה לירידה
            sl_color    = '#2ecc71'   # SL — עצירה מעל
            dir_label   = '🐻 BEARISH SHORT'
        else:
            candle_up   = '#26a69a'   # ירוק — LONG
            candle_down = '#ef5350'
            accent      = '#00e676'
            tp_color    = '#2ecc71'   # TP — מטרה לעלייה
            sl_color    = '#e74c3c'   # SL — עצירה מתחת
            dir_label   = '🐂 BULLISH LONG'

        # ── נתונים: 72 נרות אחרונים (3 ימים ב-1H) ──
        plot_df = df.tail(72).copy()
        plot_df.index = pd.to_datetime(plot_df['timestamp'], unit='ms')
        plot_df = plot_df[['open', 'high', 'low', 'close', 'volume']].rename(
            columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                     'close': 'Close', 'volume': 'Volume'}
        )

        # ── אינדיקטורים ──
        ema200_vals = ta.ema(df['close'], length=200).tail(72).values
        rsi_vals    = ta.rsi(df['close'], length=14).tail(72).values

        rsi_30 = [30] * 72
        rsi_70 = [70] * 72

        apds = [
            mpf.make_addplot(ema200_vals, color='#f5a623', width=1.8,
                             label='EMA 200'),
            mpf.make_addplot(rsi_vals, panel=2, color='#9b59b6',
                             ylabel='RSI', ylim=(0, 100)),
            mpf.make_addplot(rsi_30, panel=2, color='#27ae60',
                             linestyle='--', width=0.8),
            mpf.make_addplot(rsi_70, panel=2, color='#e74c3c',
                             linestyle='--', width=0.8),
        ]

        # ── עיצוב כהה עם צבע לפי כיוון ──
        BG = '#0d1117'
        mc = mpf.make_marketcolors(
            up=candle_up, down=candle_down,
            wick={'up': candle_up, 'down': candle_down},
            volume={'up': candle_up, 'down': candle_down},
            edge='inherit'
        )
        style = mpf.make_mpf_style(
            marketcolors=mc,
            facecolor=BG, figcolor=BG,
            gridcolor='#21262d', gridstyle='-',
            y_on_right=True,
            rc={'axes.labelcolor': '#c9d1d9',
                'xtick.color': '#8b949e',
                'ytick.color': '#8b949e',
                'text.color': '#c9d1d9'}
        )

        buf = io.BytesIO()
        fig, axes = mpf.plot(
            plot_df,
            type='candle',
            style=style,
            addplot=apds,
            volume=True,
            panel_ratios=(4, 1, 2),
            figsize=(13, 9),
            title=f'\n  {symbol}  ·  4H  ·  {dir_label}',
            returnfig=True,
            tight_layout=True
        )

        # ── אזורי רווח / הפסד לפי כיוון ──
        ax = axes[0]

        if direction == 'SHORT':
            # SHORT: רווח (ירוק) מתחת לכניסה → TP | הפסד (אדום) מעל כניסה → SL
            ax.axhspan(tp,    entry, alpha=0.10, color='forestgreen', zorder=0)
            ax.axhspan(entry, sl,    alpha=0.10, color='crimson',     zorder=0)
            # קווי גבול האזורים
            ax.axhline(tp,    color='forestgreen', linewidth=0.6, linestyle=':')
            ax.axhline(sl,    color='crimson',     linewidth=0.6, linestyle=':')
        else:
            # LONG: רווח (ירוק) מעל כניסה → TP | הפסד (אדום) מתחת כניסה → SL
            ax.axhspan(entry, tp,    alpha=0.10, color='forestgreen', zorder=0)
            ax.axhspan(sl,    entry, alpha=0.10, color='crimson',     zorder=0)
            ax.axhline(tp,    color='forestgreen', linewidth=0.6, linestyle=':')
            ax.axhline(sl,    color='crimson',     linewidth=0.6, linestyle=':')

        # ── קווים ראשיים: Entry / TP / SL ──
        ax.axhline(entry, color='#3498db',    linewidth=1.8,
                   linestyle='--', label=f'🔵 Entry  {entry:.4f}')
        ax.axhline(tp,    color='forestgreen', linewidth=1.8,
                   linestyle='--', label=f'✅ TP     {tp:.4f}')
        ax.axhline(sl,    color='crimson',     linewidth=1.8,
                   linestyle='--', label=f'🛑 SL     {sl:.4f}')

        # ── תוויות טקסט על ציר Y ──
        y_min, y_max = ax.get_ylim()
        x_pos = ax.get_xlim()[1] * 0.98
        for price_lvl, label_txt, col in [
            (tp,    'TP',    'forestgreen'),
            (entry, 'ENTRY', '#3498db'),
            (sl,    'SL',    'crimson'),
        ]:
            if y_min < price_lvl < y_max:
                ax.text(x_pos, price_lvl, f' {label_txt}', color=col,
                        fontsize=7.5, fontweight='bold', va='center',
                        bbox=dict(facecolor=BG, edgecolor=col,
                                  boxstyle='round,pad=0.2', alpha=0.8))

        ax.legend(loc='upper left', fontsize=8,
                  facecolor='#161b22', labelcolor='#c9d1d9',
                  edgecolor='#30363d')

        fig.savefig(buf, format='png', dpi=120,
                    bbox_inches='tight', facecolor=BG)
        plt.close(fig)
        buf.seek(0)
        return buf

    except Exception as e:
        print(f"Chart error: {e}")
        return None

def send_chart_alert(chart_buf, symbol, caption):
    """שולח גרף עם כיתוב קצר, ואז את ההודעה המלאה בנפרד."""
    try:
        if chart_buf:
            short = f"📊 *{symbol}* — גרף 1H עם SL/Entry/TP"
            bot.send_photo(CHAT_ID, chart_buf, caption=short,
                           parse_mode='Markdown')
        send_msg(caption)
    except Exception as e:
        print(f"Send chart error: {e}")
        send_msg(caption)

# --- שלב 1+2: משפך — מועמדים חמים ---

def get_hot_candidates():
    """
    שלב 1: שליפת כל זוגות USDT מ-Bitget (ווליום $1M+)
    שלב 2: Top 15 Gainers (LONG) + Top 15 Losers (SHORT)
    מחזיר: (gainers_list, losers_list)
    """
    try:
        print("Fetching all tickers for hot candidates...")
        tickers = exchange.fetch_tickers()

        gainers, losers = [], []
        for symbol, ticker in tickers.items():
            if not symbol.endswith('/USDT'):
                continue
            change_pct = ticker.get('percentage', None)
            volume_usd = ticker.get('quoteVolume', 0) or 0
            last_price = ticker.get('last', 0) or 0

            if change_pct is None or volume_usd < 1_000_000 or last_price <= 0:
                continue

            row = {
                'symbol':     symbol,
                'change_pct': round(change_pct, 2),
                'volume_usd': round(volume_usd),
                'price':      last_price
            }
            if change_pct > 0:
                gainers.append(row)
            elif change_pct < 0:
                losers.append(row)

        gainers.sort(key=lambda x: x['change_pct'], reverse=True)
        losers.sort(key=lambda x: x['change_pct'])   # שלילי ביותר קודם

        top_gainers = gainers[:15]
        top_losers  = losers[:15]

        # שמירה לדאשבורד (גיינרים)
        data = {
            'updated':    datetime.now().strftime('%H:%M:%S'),
            'count':      len(top_gainers),
            'candidates': top_gainers
        }
        os.makedirs(os.path.dirname(HOT_CANDIDATES_FILE), exist_ok=True)
        with open(HOT_CANDIDATES_FILE, 'w') as f:
            json.dump(data, f)

        print(f"Gainers: {[c['symbol'] for c in top_gainers]}")
        print(f"Losers:  {[c['symbol'] for c in top_losers]}")
        return top_gainers, top_losers

    except Exception as e:
        print(f"Hot candidates error: {e}")
        return [], []


def save_active_trades():
    """שומר את רשימת העסקאות הפעילות לקובץ JSON לדאשבורד."""
    try:
        with trades_lock:
            snapshot = list(active_trades)
        data = {
            'updated': datetime.now().strftime('%H:%M:%S'),
            'count':   len(snapshot),
            'trades':  snapshot,
        }
        os.makedirs(os.path.dirname(ACTIVE_TRADES_FILE), exist_ok=True)
        with open(ACTIVE_TRADES_FILE, 'w') as f:
            json.dump(data, f, default=str)
    except Exception as e:
        print(f"save_active_trades error: {e}")


def get_btc_regime():
    """
    מחזיר את מצב השוק לפי BTC/USDT ו-EMA50.
    'BULL' — BTC מעל EMA50 → מאפשר LONG
    'BEAR' — BTC מתחת EMA50 → מאפשר SHORT
    'NEUTRAL' — שגיאה בשליפה → מאפשר הכל (safe fallback)
    """
    try:
        df   = get_data('BTC/USDT', timeframe='4h', limit=100)
        ema50 = ta.ema(df['close'], length=50).iloc[-1]
        price = df['close'].iloc[-1]
        regime = 'BULL' if price > ema50 else 'BEAR'
        pct = round((price - ema50) / ema50 * 100, 2)
        print(f"BTC Regime: {regime} | price={price:.0f} EMA50={ema50:.0f} ({pct:+.2f}%)")
        return regime
    except Exception as e:
        print(f"BTC regime check failed: {e} — defaulting to NEUTRAL")
        return 'NEUTRAL'

# ═══════════════════════════════════════════════════════════════
# מנוע ניקוד מקצועי — Professional Scoring System
# ═══════════════════════════════════════════════════════════════

MIN_SCORE  = 90   # סף מינימום לפתיחת עסקה (90 = alignment כמעט מושלם)
MAX_TRADES = 5    # מקסימום עסקאות פתוחות במקביל
RSI_VETO_LONG  = 65   # Anti-FOMO: RSI מעל 65 = לא קונים (overbought ceiling)
RSI_VETO_SHORT = 28   # RSI מתחת זה = לא מוכרים (oversold)
EMA_PROXIMITY_PCT = 2.5  # מחיר חייב להיות תוך 2.5% מ-EMA200 (Anti-Chase)
BE_BUFFER_PCT  = 2.0  # % עלייה/ירידה לפני הזזת SL ל-Break Even (50% מ-TP1=5%)
TRAIL_PCT      = 1.5  # % Trailing Stop מהשיא
SL_PCT_FIXED   = 3.5  # % SL קבוע (3h chart)
TP1_PCT_FIXED  = 5.0  # % TP1 קבוע — סגירת 50%
TP_PCT_FIXED   = 10.5 # % TP מלא — RR 1:3 (3 × 3.5%)


def score_candles(df_15m, direction):
    """
    זיהוי תבניות נרות יפניים על טיים-פריים 15m — 10 נקודות מקסימום.

    LONG:  Hammer, Bullish Engulfing, Morning Star, Three White Soldiers, Bullish Harami
    SHORT: Shooting Star, Bearish Engulfing, Evening Star, Three Black Crows, Bearish Harami

    משתמש בשלושת הנרות הסגורים האחרונים (iloc[-4:-1]).
    מחזיר: (points: int, pattern_name: str)
    """
    try:
        if len(df_15m) < 6:
            return 0, "no data"

        # 3 נרות סגורים: c3=הישן, c2=האמצעי, c1=האחרון
        o3 = df_15m['open'].iloc[-4];  c3 = df_15m['close'].iloc[-4]
        h3 = df_15m['high'].iloc[-4];  l3 = df_15m['low'].iloc[-4]
        o2 = df_15m['open'].iloc[-3];  c2 = df_15m['close'].iloc[-3]
        h2 = df_15m['high'].iloc[-3];  l2 = df_15m['low'].iloc[-3]
        o1 = df_15m['open'].iloc[-2];  c1 = df_15m['close'].iloc[-2]
        h1 = df_15m['high'].iloc[-2];  l1 = df_15m['low'].iloc[-2]

        body1 = abs(c1 - o1);  range1 = h1 - l1 if h1 != l1 else 1e-10
        body2 = abs(c2 - o2);  body3  = abs(c3 - o3)
        upper_wick1 = h1 - max(c1, o1)
        lower_wick1 = min(c1, o1) - l1

        green1 = c1 > o1;  red1 = c1 < o1
        green2 = c2 > o2;  red2 = c2 < o2
        green3 = c3 > o3;  red3 = c3 < o3

        if direction == 'LONG':
            # 1. Hammer — גוף קטן, צל תחתון ארוך, צל עליון קצר
            if (body1 < range1 * 0.35 and
                    lower_wick1 >= 2.0 * max(body1, range1 * 0.01) and
                    upper_wick1 <= body1 * 1.1):
                return 10, "Hammer"

            # 2. Bullish Engulfing — נר אדום אחריו נר ירוק שבולע
            if red2 and green1 and c1 > o2 and o1 < c2:
                return 10, "Bullish Engulfing"

            # 3. Morning Star — אדום, גוף קטן (doji/spinning), ירוק מעל אמצע הראשון
            if (red3 and body2 < body3 * 0.35 and green1 and
                    c1 > (o3 + c3) / 2):
                return 10, "Morning Star"

            # 4. Three White Soldiers — 3 נרות ירוקים עולים
            if green3 and green2 and green1 and c1 > c2 > c3:
                return 10, "Three White Soldiers"

            # 5. Bullish Harami — נר אדום גדול אחריו ירוק קטן בתוכו
            if (red2 and green1 and
                    o1 >= c2 and c1 <= o2 and body1 < body2 * 0.5):
                return 5, "Bullish Harami"

        else:  # SHORT
            # 1. Shooting Star — גוף קטן, צל עליון ארוך, צל תחתון קצר
            if (body1 < range1 * 0.35 and
                    upper_wick1 >= 2.0 * max(body1, range1 * 0.01) and
                    lower_wick1 <= body1 * 1.1):
                return 10, "Shooting Star"

            # 2. Bearish Engulfing — נר ירוק אחריו אדום שבולע
            if green2 and red1 and c1 < o2 and o1 > c2:
                return 10, "Bearish Engulfing"

            # 3. Evening Star — ירוק, גוף קטן, אדום מתחת אמצע הראשון
            if (green3 and body2 < body3 * 0.35 and red1 and
                    c1 < (o3 + c3) / 2):
                return 10, "Evening Star"

            # 4. Three Black Crows — 3 נרות אדומים יורדים
            if red3 and red2 and red1 and c1 < c2 < c3:
                return 10, "Three Black Crows"

            # 5. Bearish Harami — נר ירוק גדול אחריו אדום קטן בתוכו
            if (green2 and red1 and
                    o1 <= c2 and c1 >= o2 and body1 < body2 * 0.5):
                return 5, "Bearish Harami"

        return 0, "no pattern"

    except Exception as e:
        print(f"  score_candles error: {e}")
        return 0, "error"


def score_symbol(df_3h, df_1h, symbol, direction='LONG'):
    """
    מערכת ניקוד מקצועית 0–100 נקודות.

    direction='LONG'  → גיינרים, מחפש עלייה
    direction='SHORT' → לוזרים,  מחפש ירידה

    ניקוד:
      Trend     (30): EMA200 ב-4H (+20) + ב-1H (+10)
      Momentum  (25): MACD מעל/מתחת Signal (+15) + Histogram מתחזק (+10)
      RSI       (20): Sweet-spot (+20), Acceptable (+10)
      BB+Volume (15): מחיר מעל/מתחת MidBB (+10) + Volume ×1.2 (+5)
      Candles   (10): תבנית נרות יפניים חזקה על 4H (+10), חלשה (+5)

    מחזיר: (score: int, breakdown: str, atr: float)
    """
    score = 0
    parts = []

    try:
        close_3h = df_3h['close']

        # ── אינדיקטורים 3H ──
        ema200_1h = ta.ema(close_3h, length=200)
        macd_df   = ta.macd(close_3h, fast=12, slow=26, signal=9)
        rsi_s     = ta.rsi(close_3h, length=14)
        bb_df     = ta.bbands(close_3h, length=20, std=2)
        atr_s     = ta.atr(df_3h['high'], df_3h['low'], close_3h, length=14)

        # ── EMA200 על 1H (אישור משני) ──
        ema200_15 = ta.ema(df_1h['close'], length=200)

        if any(v is None for v in [ema200_1h, macd_df, rsi_s, bb_df, atr_s, ema200_15]):
            print(f"  [{symbol}] indicator calc failed")
            return 0, "indicator error", 0

        price      = close_3h.iloc[-1]
        ema200_v   = ema200_1h.iloc[-1]
        ema200_15v = ema200_15.iloc[-1]

        # MACD — איתור עמודות (pandas_ta משתנה בשמות לפי פרמטרים)
        macd_col = next(c for c in macd_df.columns if c.startswith('MACD_'))
        sig_col  = next(c for c in macd_df.columns if c.startswith('MACDs_'))
        hist_col = next(c for c in macd_df.columns if c.startswith('MACDh_'))
        macd_v   = macd_df[macd_col].iloc[-1]
        sig_v    = macd_df[sig_col].iloc[-1]
        hist_v   = macd_df[hist_col].iloc[-1]
        hist_p   = macd_df[hist_col].iloc[-2]

        rsi_v    = rsi_s.iloc[-1]

        # Bollinger Middle
        bb_mid_col = next(c for c in bb_df.columns if 'BBM_' in c)
        bb_mid     = bb_df[bb_mid_col].iloc[-1]

        atr_v    = atr_s.iloc[-1]

        # השתמש בנר הסגור האחרון (iloc[-2]) — לא בנר הנוכחי שעדיין פתוח
        vol_curr = df_3h['volume'].iloc[-2]
        vol_avg  = df_3h['volume'].iloc[-12:-2].mean()
        vol_rat  = vol_curr / vol_avg if vol_avg > 0 else 0

        if any(pd.isna(v) for v in [ema200_v, ema200_15v, macd_v, sig_v,
                                      hist_v, hist_p, rsi_v, bb_mid, atr_v]):
            print(f"  [{symbol}] NaN in indicators")
            return 0, "NaN values", 0

        # ════════════════════════════════════════════════
        # ANTI-FOMO HARD VETOES (לפני כל ניקוד)
        # ════════════════════════════════════════════════

        # ── וטו 1: EMA200 Proximity — Anti-Chase ──
        ema_gap_pct = (price - ema200_v) / ema200_v * 100  # + = מעל EMA200
        if direction == 'LONG' and ema_gap_pct > EMA_PROXIMITY_PCT:
            print(f"  [{symbol}] 🚫 EMA200 CHASE VETO: {ema_gap_pct:.1f}% מעל EMA200 (מקסימום {EMA_PROXIMITY_PCT}%) — המתן לפולבק")
            return 0, f"EMA200 chase veto ({ema_gap_pct:.1f}% above EMA200)", atr_v
        if direction == 'SHORT' and ema_gap_pct < -EMA_PROXIMITY_PCT:
            print(f"  [{symbol}] 🚫 EMA200 CHASE VETO: {abs(ema_gap_pct):.1f}% מתחת EMA200 (מקסימום {EMA_PROXIMITY_PCT}%) — המתן לבאונס")
            return 0, f"EMA200 chase veto ({abs(ema_gap_pct):.1f}% below EMA200)", atr_v

        # ── וטו 2: Wick Rejection — Anti-False Breakout ──
        last_c     = df_3h.iloc[-2]   # נר סגור אחרון (מאושר)
        c_body     = abs(last_c['close'] - last_c['open'])
        c_upper    = last_c['high'] - max(last_c['close'], last_c['open'])
        c_lower    = min(last_c['close'], last_c['open']) - last_c['low']
        if direction == 'LONG' and c_upper > c_body and c_body > 0:
            print(f"  [{symbol}] 🚫 WICK REJECTION: פתיל עליון ({c_upper:.6g}) > גוף ({c_body:.6g}) — Shooting Star, מבטל LONG")
            return 0, f"Wick rejection LONG (upper wick {c_upper:.4g} > body {c_body:.4g})", atr_v
        if direction == 'SHORT' and c_lower > c_body and c_body > 0:
            print(f"  [{symbol}] 🚫 WICK REJECTION: פתיל תחתון ({c_lower:.6g}) > גוף ({c_body:.6g}) — Hammer, מבטל SHORT")
            return 0, f"Wick rejection SHORT (lower wick {c_lower:.4g} > body {c_body:.4g})", atr_v

        # ════════════════════════════════
        # 1. TREND — 30 נקודות
        # ════════════════════════════════
        if direction == 'LONG':
            t1h  = price > ema200_v
            t15m = price > ema200_15v
        else:
            t1h  = price < ema200_v
            t15m = price < ema200_15v

        t_pts = 0
        if t1h:
            t_pts += 20
        if t1h and t15m:
            t_pts += 10
        score += t_pts
        parts.append(f"Trend={t_pts}/30")
        print(f"  [{symbol}] {direction} | Trend={t_pts} "
              f"(4H={'✓' if t1h else '✗'} 1H={'✓' if t15m else '✗'})")

        # ════════════════════════════════
        # 2. MOMENTUM (MACD) — 25 נקודות
        # ════════════════════════════════
        if direction == 'LONG':
            macd_ok = macd_v > sig_v        # MACD מעל Signal
            hist_ok = hist_v > hist_p       # Histogram מתחזק (פחות שלילי / יותר חיובי)
        else:
            macd_ok = macd_v < sig_v
            hist_ok = hist_v < hist_p       # Histogram מתחזק לכיוון שלילי

        m_pts = 0
        if macd_ok:
            m_pts += 15
        if hist_ok:
            m_pts += 10
        score += m_pts
        parts.append(f"MACD={m_pts}/25")
        print(f"  [{symbol}] {direction} | MACD={m_pts} "
              f"(aligned={'✓' if macd_ok else '✗'} hist={'✓' if hist_ok else '✗'})")

        # ════════════════════════════════
        # 3. RSI STRENGTH — 20 נקודות
        # ════════════════════════════════

        # וטו קשה — RSI קיצוני = פסילה מוחלטת
        if direction == 'LONG' and rsi_v > RSI_VETO_LONG:
            print(f"  [{symbol}] 🚫 ANTI-FOMO RSI VETO: {rsi_v:.1f} > {RSI_VETO_LONG} (ceiling) — skip")
            return 0, f"Anti-FOMO RSI veto ({rsi_v:.1f} > {RSI_VETO_LONG} ceiling)", atr_v
        if direction == 'SHORT' and rsi_v < RSI_VETO_SHORT:
            print(f"  [{symbol}] 🚫 RSI VETO: {rsi_v:.1f} < {RSI_VETO_SHORT} (oversold) — skip")
            return 0, f"RSI veto ({rsi_v:.1f} oversold)", atr_v

        if direction == 'LONG':
            rsi_ideal = 50 <= rsi_v <= 65   # Sweet spot: מומנטום בלי overbought
            rsi_ok    = 45 <= rsi_v <= 70   # Acceptable
        else:
            rsi_ideal = 35 <= rsi_v <= 50   # Momentum short: לא oversold עדיין
            rsi_ok    = 30 <= rsi_v <= 55

        r_pts = 0
        if rsi_ideal:
            r_pts = 20
        elif rsi_ok:
            r_pts = 10
        score += r_pts
        parts.append(f"RSI={r_pts}/20(={rsi_v:.0f})")
        print(f"  [{symbol}] {direction} | RSI={r_pts} (rsi={rsi_v:.1f})")

        # ════════════════════════════════
        # 4. BOLLINGER + VOLUME — 15 נקודות
        # ════════════════════════════════
        if direction == 'LONG':
            bb_ok = price > bb_mid
        else:
            bb_ok = price < bb_mid

        vol_ok = vol_rat >= 1.2

        b_pts = 0
        if bb_ok:
            b_pts += 10
        if vol_ok:
            b_pts += 5
        score += b_pts
        parts.append(f"BB+Vol={b_pts}/15")
        print(f"  [{symbol}] {direction} | BB+Vol={b_pts} "
              f"(bb={'✓' if bb_ok else '✗'} vol×{vol_rat:.1f}={'✓' if vol_ok else '✗'})")

        # ════════════════════════════════
        # 5. CANDLES (נרות יפניים) — 10 נקודות
        # ════════════════════════════════
        c_pts, pattern_name = score_candles(df_3h, direction)
        score += c_pts
        candle_icon = "🕯" if c_pts > 0 else "—"
        parts.append(f"Candles={c_pts}/10({pattern_name})")
        print(f"  [{symbol}] {direction} | Candles={c_pts} "
              f"({candle_icon} {pattern_name})")

        # ════════════════════════════════════
        # 6. FEAR & GREED INDEX — ±5 נקודות
        # ════════════════════════════════════
        fng_v, fng_lbl = get_fear_greed()
        if direction == 'LONG':
            if   fng_v < 25: fng_adj = +5    # פחד קיצוני  → קנייה בבאונס
            elif fng_v < 45: fng_adj = +2    # פחד          → רוח גבית
            elif fng_v > 75: fng_adj = -5    # חמדנות קיצונית → סיכון LONG
            elif fng_v > 55: fng_adj = -2    # חמדנות         → זהירות קלה
            else:            fng_adj =  0    # נייטרלי
        else:  # SHORT
            if   fng_v > 75: fng_adj = +5    # חמדנות קיצונית → שורט בשיא
            elif fng_v > 55: fng_adj = +2    # חמדנות          → רוח גבית
            elif fng_v < 25: fng_adj = -5    # פחד קיצוני  → סיכון SHORT
            elif fng_v < 45: fng_adj = -2    # פחד           → זהירות קלה
            else:            fng_adj =  0    # נייטרלי
        score = max(0, min(100, score + fng_adj))
        sign  = f"+{fng_adj}" if fng_adj >= 0 else str(fng_adj)
        parts.append(f"FNG={fng_v}({sign})[{fng_lbl}]")
        print(f"  [{symbol}] {direction} | FNG={fng_v} ({fng_lbl}) adj={sign}")

        breakdown = " | ".join(parts) + f"  →  TOTAL={score}/100"
        print(f"  [{symbol}] {direction} SCORE={score}/100 {'🟢 SIGNAL!' if score >= MIN_SCORE else '🔴 skip'}")
        return score, breakdown, atr_v

    except Exception as e:
        print(f"  [{symbol}] score error: {e}")
        return 0, str(e), 0

# --- ניהול עסקאות דמו ---

def get_direction_header(direction):
    if direction == 'SHORT':
        return "🐻 *BEARISH SHORT (מכירה)*\n🔴🔴 _הדוב מוחץ למטה!_ 🔴🔴"
    return "🐂 *BULLISH LONG (קנייה)*\n🟢🟢 _השור נוגח למעלה!_ 🟢🟢"

def get_momentum_tip(direction):
    if direction == 'SHORT':
        return "📉 _השוק נחלש — מנצלים את הירידה._"
    return "🌊 _המומנטום חיובי — רוכבים על הגל._"

def _pnl_on_half(dist_pct):
    """P&L ($) על חצי פוזיציה ($250) לפי % מרחק מהכניסה."""
    return round(POSITION_SIZE / 2 * dist_pct / 100, 2)

def open_demo_trade(symbol, price, reason, df_3h=None,
                    direction='LONG', score=0, atr=0, timeframe='4H', tf_reason='',
                    rsi=None, ema200=None, fng_v=None):
    """
    פותח עסקת דמו עם SL/TP קבועים.
    SL=3.5% | TP1=5% (סגירת 50%) | TP=10.5% (RR 1:3) | BE=2%
    timeframe: '4H' / '1H' — גרף הכניסה שנבחר אדפטיבית
    fng_v: ערך FNG שכבר חושב ב-scan (כדי לא לשאול שוב)
    """
    # בדיקת יתרה — אין לפתוח עסקה אם אין מספיק כסף
    if wallet.get('balance', STARTING_BALANCE) < MARGIN:
        print(f"WALLET: insufficient balance (${wallet.get('balance', 0):.2f}) — skipping {symbol}")
        send_msg(f"⚠️ *יתרה נמוכה* — אין מספיק להפקדת מרג'ין\nנדרש: ${MARGIN} | יש: ${wallet.get('balance', 0):.2f}")
        return

    # ── Sentiment Rules: position size & SL ──────────────────────────────────
    if fng_v is None:
        fng_v, _, _ = sentiment_check("open_trade")
    pos_size = POSITION_SIZE   # default $500
    sl_pct   = SL_PCT_FIXED    # 3.5%
    if fng_v >= GREED_THRESHOLD:
        pos_size = round(POSITION_SIZE * 0.60)   # ×60% — Greed Filter
        print(f"  [SENTIMENT] GREED ({fng_v}) → פוזיציה צומצמה ל-${pos_size}")
    if fng_v <= FEAR_THRESHOLD:
        sl_pct = SL_PCT_FIXED + FEAR_EXTRA_SL_PCT   # +1% — Fear buffer
        print(f"  [SENTIMENT] FEAR ({fng_v}) → SL מורחב ל-{sl_pct}% (+{FEAR_EXTRA_SL_PCT}%)")

    # ── SL/TP קבועים לגרף 4H ──
    tp_pct  = TP_PCT_FIXED    # 10.5%
    tp1_pct = TP1_PCT_FIXED   # 5.0%
    be_pct  = BE_BUFFER_PCT   # 2.0%

    sl_dist  = price * sl_pct  / 100
    tp_dist  = price * tp_pct  / 100
    tp1_dist = price * tp1_pct / 100
    be_dist  = price * be_pct  / 100

    if direction == 'LONG':
        sl_price  = price - sl_dist
        tp_price  = price + tp_dist
        be_price  = price + be_dist
        tp1_price = price + tp1_dist
    else:   # SHORT
        sl_price  = price + sl_dist
        tp_price  = price - tp_dist
        be_price  = price - be_dist
        tp1_price = price - tp1_dist

    # ── P&L (using pos_size for sentiment-adjusted positions) ──
    tp1_pnl    = round(pos_size / 2 * tp1_pct / 100, 2)
    tp2_pnl    = round(pos_size / 2 * tp_pct  / 100, 2)
    max_profit = round(tp1_pnl + tp2_pnl, 2)
    sl_loss    = round(pos_size * sl_pct / 100, 2)

    # ── Expected P&L at TP / SL — formula: abs(target-entry)/entry * pos_size ──
    est_profit_tp = round(abs(tp_price  - price) / price * pos_size, 2)
    est_loss_sl   = round(abs(sl_price  - price) / price * pos_size, 2)
    rr_ratio      = round(est_profit_tp / est_loss_sl, 2) if est_loss_sl > 0 else 0

    trade = {
        'symbol':          symbol,
        'entry':           price,
        'sl':              sl_price,
        'tp':              tp_price,
        'tp1':             tp1_price,
        'be_lvl':          be_price,
        'sl_pct':          sl_pct,
        'tp_pct':          tp_pct,
        'direction':       direction,
        'phase':           'initial',
        'be_triggered':    False,
        'tp1_triggered':   False,
        'tp1_pnl':         0.0,
        'peak_price':      price,
        'trailing_sl':     None,
        'score':           score,
        'atr':             round(atr, 6),
        'timeframe':       timeframe,
        'rsi':             round(rsi, 2) if rsi is not None else None,
        'ema200':          round(ema200, 6) if ema200 is not None else None,
        'score_breakdown': reason,
        'opened_at':       datetime.now().isoformat(timespec='seconds'),
        'pos_size':        pos_size,    # נשמר לחישובי P&L בניהול עסקאות
        'fng_at_entry':    fng_v,       # FNG בזמן הכניסה
    }
    with trades_lock:
        active_trades.append(trade)
    wallet_deduct()        # ← נועל $50 מרג'ין בארנק
    save_active_trades()

    dir_header = get_direction_header(direction)
    tip        = get_momentum_tip(direction)
    emoji      = "🟢" if direction == 'LONG' else "🔴"
    score_bar  = "█" * (score // 10) + "░" * (10 - score // 10)

    # הסבר על הטיים-פריים שנבחר
    if not tf_reason:
        tf_reason = f"טרנד חזק ב-{timeframe}" if timeframe == '4H' else f"פריצה ב-{timeframe} (4H חלש)"
    tf_icon = "📊" if timeframe == '4H' else ("⏱️" if timeframe == '1H' else "⚡")

    equity_after = _get_equity()

    msg  = f"{dir_header}\n\n"
    msg += f"{'─' * 26}\n"
    msg += f"{emoji} *Professional Scoring System*\n"
    msg += f"מטבע: `{symbol}`\n"
    msg += f"{tf_icon} גרף: *{timeframe}* — _{tf_reason}_\n"
    msg += f"פירוט: _{reason}_\n\n"
    msg += f"*ניקוד איתות: {score}/100*\n"
    msg += f"`{score_bar}` {'🟢 STRONG' if score >= 95 else '🟡 GOOD'}\n\n"
    msg += f"מחיר כניסה: `{price:.6g}`\n"
    msg += f"🛑 SL  ({'-' if direction=='LONG' else '+'}{sl_pct}%): `{sl_price:.6g}` ← 3.5% קבוע\n"
    msg += f"🔒 BE  ({'+' if direction=='LONG' else '-'}{be_pct}%): `{be_price:.6g}` ← SL→כניסה\n"
    msg += f"🎯 TP1 ({'+' if direction=='LONG' else '-'}{tp1_pct}%): `{tp1_price:.6g}` ← סגירת 50%\n"
    msg += f"🎯 TP  ({'+' if direction=='LONG' else '-'}{tp_pct}%): `{tp_price:.6g}` ← RR 1:3\n"
    msg += f"📍 Trailing: {TRAIL_PCT}% מהשיא (מיידי עם הרווח הראשון)\n\n"
    msg += f"{'─' * 26}\n"
    msg += f"💼 *Leverage: {LEVERAGE}x (Isolated)*\n"
    msg += f"💰 בטחון: ${MARGIN} · נשלט: ${pos_size}\n"
    msg += f"{'─' * 26}\n"
    msg += f"📊 *Expected P&L*\n"
    msg += f"✅ Est\\. Profit at TP: *+${est_profit_tp}*\n"
    msg += f"❌ Est\\. Loss at SL:   *\\-${est_loss_sl}*\n"
    msg += f"⚖️ Risk / Reward: *1 : {rr_ratio}*\n"
    msg += f"{'─' * 26}\n"
    msg += f"💼 Equity: `${equity_after:.2f}` | יתרה: `${wallet.get('balance', 0):.2f}`\n\n"
    msg += tip

    chart_buf = generate_chart(df_3h, symbol, price, sl_price, tp_price, direction) \
                if df_3h is not None else None
    send_chart_alert(chart_buf, symbol, msg)
    print(f"Trade opened: {symbol} {direction} @ {price:.6g} | SL={sl_price:.6g} TP={tp_price:.6g} | Score={score}")


def track_trades():
    """
    בודק כל עסקה פעילה כל 60 שניות.
    תומך ב-LONG וב-SHORT.
    """
    global active_trades, daily_stats

    if daily_stats['date'] != date.today():
        daily_stats = {
            'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'date': date.today(),
            'close_reasons': {'TP': 0, 'TP1+Trail': 0, 'Trailing': 0, 'SL': 0, 'BE': 0, 'Manual': 0},
        }

    # FNG once per manage cycle (avoid spamming API/log)
    fng_v_mgr, _, _ = sentiment_check("manage_risk")

    for trade in active_trades[:]:
        try:
            ticker        = exchange.fetch_ticker(trade['symbol'])
            current_price = ticker['last']
            trade['current_price'] = current_price   # שמור לדאשבורד (Floating P&L)
            entry         = trade['entry']
            sym           = trade['symbol']
            direction     = trade.get('direction', 'LONG')
            pos_size      = trade.get('pos_size', POSITION_SIZE)  # per-trade position size
            half          = pos_size / 2                          # חצי פוזיציה

            # helpers: "profit direction" — True כאשר המחיר זזה לכיוון הרצוי
            def profit_dir(p):
                return p >= entry if direction == 'LONG' else p <= entry

            def sl_hit(p):
                return p <= trade['sl'] if direction == 'LONG' else p >= trade['sl']

            def tp_full_hit(p):
                return p >= trade['tp'] if direction == 'LONG' else p <= trade['tp']

            def tp1_hit(p):
                return p >= trade['tp1'] if direction == 'LONG' else p <= trade['tp1']

            def be_hit(p):
                return p >= trade['be_lvl'] if direction == 'LONG' else p <= trade['be_lvl']

            # ════════════════════════════════════════════
            # שלב INITIAL — פוזיציה מלאה $500
            # ════════════════════════════════════════════
            if trade['phase'] == 'initial':

                # 0. עדכון Trailing SL ברגע שיש רווח כלשהו (1.5% מהשיא)
                in_profit = (direction == 'LONG' and current_price > entry) or \
                            (direction == 'SHORT' and current_price < entry)
                if in_profit:
                    trail_factor = 1 - TRAIL_PCT / 100 if direction == 'LONG' \
                                   else 1 + TRAIL_PCT / 100
                    new_trail = round(current_price * trail_factor, 8)
                    if direction == 'LONG':
                        if current_price > trade['peak_price']:
                            trade['peak_price'] = current_price
                        if trade['trailing_sl'] is None or new_trail > trade['trailing_sl']:
                            trade['trailing_sl'] = new_trail
                    else:
                        if current_price < trade['peak_price']:
                            trade['peak_price'] = current_price
                        if trade['trailing_sl'] is None or new_trail < trade['trailing_sl']:
                            trade['trailing_sl'] = new_trail

                # בדיקת Trailing Stop — לפני BE/TP1 כדי לנעול רווח
                trail_sl_hit = (
                    (direction == 'LONG'  and trade['trailing_sl'] and current_price <= trade['trailing_sl']) or
                    (direction == 'SHORT' and trade['trailing_sl'] and current_price >= trade['trailing_sl'])
                )
                if trail_sl_hit:
                    dist_pct = abs(current_price - entry) / entry * 100
                    sign     = 1 if profit_dir(current_price) else -1
                    pnl_usd  = round(sign * pos_size * dist_pct / 100, 2)
                    pnl_pct_r = round(sign * dist_pct * LEVERAGE, 1)
                    icon     = "📈" if pnl_usd >= 0 else "📉"
                    if pnl_usd >= 0:
                        daily_stats['wins']  += 1
                    else:
                        daily_stats['losses'] += 1
                    daily_stats['total_pnl'] += pnl_usd
                    daily_stats['close_reasons']['Trailing'] += 1
                    wallet_credit(pnl_usd)
                    _log_closed_trade(trade, 'Trailing', pnl_usd, current_price)
                    ref = trade['peak_price']
                    eq  = _get_equity()
                    send_msg(
                        f"📍 *Trailing Stop נגע — {sym}*\n"
                        f"{'שיא' if direction=='LONG' else 'שפל'}: `{ref:.6g}` → יציאה: `{current_price:.6g}`\n"
                        f"{icon} *P&L: {pnl_usd:+.2f}$ ({pnl_pct_r:+.1f}% על מרג'ין)*\n"
                        f"💼 {LEVERAGE}x Isolated · Trailing {TRAIL_PCT}%\n"
                        f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
                        f"{icon} סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()
                    continue

                # 1a. Greed Early BE — FNG≥70: BE at +2% (immediately, before TP1)
                if not trade['be_triggered'] and fng_v_mgr >= GREED_THRESHOLD:
                    greed_profit_pct = abs(current_price - entry) / entry * 100
                    if profit_dir(current_price) and greed_profit_pct >= GREED_EARLY_BE_PCT:
                        trade['sl']           = entry
                        trade['be_triggered'] = True
                        print(f"  [SENTIMENT] GREED EARLY BE: {sym} SL→BE @ {current_price:.6g} (+{greed_profit_pct:.2f}%, FNG={fng_v_mgr})")
                        send_msg(
                            f"🔒 *Greed Early BE — {sym}*\n"
                            f"מחיר: `{current_price:.6g}` (+{greed_profit_pct:.2f}% רווח)\n"
                            f"SL הועבר לכניסה: `{entry:.6g}` 🛡️ (FNG={fng_v_mgr} — מצב חמדנות)\n"
                            f"💼 {LEVERAGE}x Isolated · ההון מוגן!"
                        )

                # 1b. Break Even (סטנדרטי — 2% רווח)
                if not trade['be_triggered'] and be_hit(current_price):
                    trade['sl']           = entry
                    trade['be_triggered'] = True
                    send_msg(
                        f"🔒 *Break Even מופעל — {sym}*\n"
                        f"מחיר: `{current_price:.6g}` (+{BE_BUFFER_PCT}% מהכניסה)\n"
                        f"SL הועבר לכניסה: `{entry:.6g}`\n"
                        f"💼 {LEVERAGE}x Isolated · ההון מוגן!"
                    )

                # 2. TP1 (5%) — סגור 50%, הפעל Trailing
                if tp1_hit(current_price):
                    dist_pct  = abs(current_price - entry) / entry * 100
                    tp1_pnl   = round(half * dist_pct / 100, 2)
                    tp1_pct_r = round(tp1_pnl / MARGIN * 100, 1)
                    trade['tp1_triggered'] = True
                    trade['tp1_pnl']       = tp1_pnl
                    trade['phase']         = 'trailing'
                    trade['peak_price']    = current_price
                    if direction == 'LONG':
                        trade['trailing_sl'] = current_price * 0.98
                    else:
                        trade['trailing_sl'] = current_price * 1.02
                    daily_stats['total_pnl'] += tp1_pnl
                    send_msg(
                        f"🎯 *TP1 הושג — {sym}!*\n"
                        f"מחיר: `{current_price:.6g}` | {direction}\n"
                        f"50% נסגרו · ✅ *Est\\. Profit at TP1: \\+${tp1_pnl}* (\\+{tp1_pct_r}%)\n"
                        f"💼 {LEVERAGE}x · שאר 50% ($250) בטריילינג 2%\n"
                        f"📍 Trailing SL: `{trade['trailing_sl']:.6g}`\n"
                        f"📈 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    continue

                # 3. SL נגע
                if sl_hit(current_price):
                    if trade['be_triggered']:
                        daily_stats['losses'] += 1
                        daily_stats['close_reasons']['BE'] += 1
                        wallet_credit(0)   # מרג'ין חוזר, ללא P&L
                        _log_closed_trade(trade, 'BE', 0.0, current_price)
                        eq = _get_equity()
                        send_msg(
                            f"🔒 *Break Even — יצאנו ב-{sym}*\n"
                            f"מחיר: `{current_price:.6g}` | כניסה: `{entry:.6g}`\n"
                            f"*ללא הפסד · ההון נשמר*\n"
                            f"💼 {LEVERAGE}x Isolated\n"
                            f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
                            f"📊 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                        )
                    else:
                        loss     = round(pos_size * trade['sl_pct'] / 100, 2)
                        loss_pct = round(loss / MARGIN * 100, 1)
                        daily_stats['losses']    += 1
                        daily_stats['total_pnl'] -= loss
                        daily_stats['close_reasons']['SL'] += 1
                        wallet_credit(-loss)   # מרג'ין חוזר פחות ההפסד
                        _log_closed_trade(trade, 'SL', -loss, current_price)
                        eq = _get_equity()
                        send_msg(
                            f"🛑 *SL נגע — {sym}*\n"
                            f"כניסה: `{entry:.6g}` → SL: `{trade['sl']:.6g}`\n"
                            f"❌ *Est\\. Loss at SL: \\-${loss}* ({loss_pct}% על מרג'ין)\n"
                            f"💼 {LEVERAGE}x Isolated · בטחון: ${MARGIN}\n"
                            f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
                            f"📉 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                        )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()

            # ════════════════════════════════════════════
            # שלב TRAILING — 50% פוזיציה נותרת ($250)
            # ════════════════════════════════════════════
            elif trade['phase'] == 'trailing':

                # עדכן שיא/שפל ו-Trailing SL
                if direction == 'LONG':
                    if current_price > trade['peak_price']:
                        trade['peak_price']  = current_price
                        trade['trailing_sl'] = round(current_price * (1 - TRAIL_PCT / 100), 8)
                else:
                    if current_price < trade['peak_price']:
                        trade['peak_price']  = current_price
                        trade['trailing_sl'] = round(current_price * (1 + TRAIL_PCT / 100), 8)

                # TP מלא — סגור שאר 50%
                if tp_full_hit(current_price):
                    dist_pct = abs(current_price - entry) / entry * 100
                    tp_pnl   = round(half * dist_pct / 100, 2)
                    total    = round(trade['tp1_pnl'] + tp_pnl, 2)
                    daily_stats['wins']      += 1
                    daily_stats['total_pnl'] += tp_pnl
                    daily_stats['close_reasons']['TP'] += 1
                    wallet_credit(total)
                    _log_closed_trade(trade, 'TP', total, current_price)
                    eq = _get_equity()
                    est_tp_full = round(abs(current_price - entry) / entry * half, 2)
                    send_msg(
                        f"✅ *TP מלא הושג — {sym}!* 🎉\n"
                        f"מחיר: `{current_price:.6g}` | {direction}\n"
                        f"שאר 50% נסגרו · ✅ *Est\\. Profit at TP: \\+${est_tp_full}*\n"
                        f"TP1 \\+ TP סה\"כ: 📈 *\\+${total}*\n"
                        f"💼 {LEVERAGE}x Isolated\n"
                        f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
                        f"📈 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()

                # Trailing Stop נגע
                elif sl_hit(current_price) or \
                     (direction == 'LONG' and trade['trailing_sl'] and current_price <= trade['trailing_sl']) or \
                     (direction == 'SHORT' and trade['trailing_sl'] and current_price >= trade['trailing_sl']):
                    dist_pct = abs(current_price - entry) / entry * 100
                    sign     = 1 if profit_dir(current_price) else -1
                    half_pnl = round(sign * half * dist_pct / 100, 2)
                    total    = round(trade['tp1_pnl'] + half_pnl, 2)
                    icon     = "📈" if half_pnl >= 0 else "📉"
                    if half_pnl >= 0:
                        daily_stats['wins'] += 1
                    else:
                        daily_stats['losses'] += 1
                    daily_stats['total_pnl'] += half_pnl
                    daily_stats['close_reasons']['TP1+Trail'] += 1
                    wallet_credit(total)
                    _log_closed_trade(trade, 'TP1+Trail', total, current_price)
                    eq = _get_equity()
                    ref_price = trade['peak_price']
                    send_msg(
                        f"📍 *Trailing Stop נגע — {sym}*\n"
                        f"{'שיא' if direction=='LONG' else 'שפל'}: `{ref_price:.6g}` → יציאה: `{current_price:.6g}`\n"
                        f"50% נסגרו: {icon} *{half_pnl:+}$*\n"
                        f"TP1 + Trailing סה\"כ: {icon} *{total:+}$*\n"
                        f"💼 {LEVERAGE}x Isolated\n"
                        f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
                        f"{icon} סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()

        except Exception as e:
            print(f"Track error {trade.get('symbol','?')}: {e}")

    save_active_trades()   # שמור גם שינויי BE / Trailing SL

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

    # ── Close Reason Breakdown ──
    cr = daily_stats.get('close_reasons', {})
    cr_total = sum(cr.values())
    cr_lines = ""
    reason_map = [
        ('TP',        '🎯 TP מלא (RR 1:3)'),
        ('TP1+Trail', '📍 TP1 + Trailing'),
        ('Trailing',  '📍 Trailing Stop (Initial)'),
        ('BE',        '🔒 Break Even'),
        ('SL',        '🛑 Stop Loss'),
        ('Manual',    '✋ סגירה ידנית'),
    ]
    for key, label in reason_map:
        count = cr.get(key, 0)
        if count > 0:
            pct = round(count / cr_total * 100) if cr_total > 0 else 0
            cr_lines += f"  {label}: *{count}* ({pct}%)\n"
    if not cr_lines:
        cr_lines = "  _אין סגירות עדיין_\n"

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
    msg += f"*🔬 סיבות סגירה ({cr_total} עסקאות):*\n"
    msg += cr_lines + "\n"
    msg += f"*🔌 חיבור Bitget API:* {api_status}\n"
    msg += f"{'─' * 28}\n"
    msg += f"_הבוט פעיל ומסרוק כל שעה_ 🤖"

    send_msg(msg)
    print(f"Daily report sent at {now}")

def send_heartbeat():
    """
    שולח עדכון קצר לכל עסקה פעילה:
    מחיר נוכחי, P&L ב-% ו-$, מרחק ל-SL הבא וה-TP הבא.
    """
    with trades_lock:
        trades_snapshot = list(active_trades)
    if not trades_snapshot:
        return

    now_str   = datetime.now().strftime('%H:%M')
    pnl_today = round(daily_stats.get('total_pnl', 0), 2)
    pnl_icon  = "📈" if pnl_today >= 0 else "📉"

    msg = f"💓 *Heartbeat — {now_str}*\n{'─' * 24}\n"

    for t in trades_snapshot:
        sym       = t['symbol']
        entry     = t['entry']
        direction = t.get('direction', 'LONG')
        phase     = t.get('phase', 'initial')

        try:
            price = exchange.fetch_ticker(sym)['last']
        except Exception as e:
            print(f"Heartbeat fetch error {sym}: {e}")
            continue

        # ── P&L ──
        _ps     = t.get('pos_size', POSITION_SIZE)   # per-trade position size
        raw_pct = (price - entry) / entry * 100
        pnl_pct = raw_pct if direction == 'LONG' else -raw_pct
        if t.get('tp1_triggered'):
            half_pnl = round(_ps / 2 * pnl_pct / 100, 2)
            pnl_usd  = round(t.get('tp1_pnl', 0) + half_pnl, 2)
        else:
            pnl_usd = round(_ps * pnl_pct / 100, 2)

        pnl_arrow = "📈" if pnl_usd >= 0 else "📉"

        # ── מרחק ל-SL ול-TP הבא ──
        if phase == 'trailing':
            tsl      = t.get('trailing_sl')
            tp       = t['tp']
            dist_tp  = abs(tp - price) / price * 100
            dist_sl  = abs(tsl - price) / price * 100 if tsl else 0
            sl_lbl   = f"📍 Trailing SL: `{tsl:.6g}`  ({dist_sl:.1f}% רחוק)" if tsl else "📍 Trailing SL: N/A"
            tp_lbl   = f"🎯 TP: `{tp:.6g}`  ({dist_tp:.1f}% רחוק)"
        else:
            sl       = t['sl']
            next_tp  = t['tp1'] if not t.get('tp1_triggered') else t['tp']
            tp_label = "TP1" if not t.get('tp1_triggered') else "TP"
            dist_sl  = abs(sl - price) / price * 100
            dist_tp  = abs(next_tp - price) / price * 100
            sl_lbl   = f"🛑 SL: `{sl:.6g}`  ({dist_sl:.1f}% רחוק)"
            tp_lbl   = f"🎯 {tp_label}: `{next_tp:.6g}`  ({dist_tp:.1f}% רחוק)"

        dir_emoji   = "🟢" if direction == 'LONG' else "🔴"
        phase_label = "🔄 Trailing" if phase == 'trailing' else "📊 Initial"
        be_label    = " · 🔒 BE" if t.get('be_triggered') else ""

        run_label = "💰 Running Profit" if pnl_usd >= 0 else "🔻 Running Loss"
        msg += (
            f"\n{dir_emoji} *{sym}*  {phase_label}{be_label}\n"
            f"   כניסה: `{entry:.6g}` → עכשיו: `{price:.6g}`\n"
            f"   {run_label}: *${pnl_usd:+.2f}*  ({pnl_pct:+.1f}%)\n"
            f"   {sl_lbl}\n"
            f"   {tp_lbl}\n"
        )

    # ── Floating P&L כולל ──
    total_floating = 0.0
    for t in trades_snapshot:
        cp  = t.get('current_price', t.get('entry', 0))
        ep  = t['entry']
        d   = t.get('direction', 'LONG')
        _ps = t.get('pos_size', POSITION_SIZE)
        raw_pct = (cp - ep) / ep * 100 if ep else 0
        p_pct   = raw_pct if d == 'LONG' else -raw_pct
        if t.get('tp1_triggered'):
            total_floating += round(t.get('tp1_pnl', 0) + _ps / 2 * p_pct / 100, 2)
        else:
            total_floating += round(_ps * p_pct / 100, 2)

    float_icon = "📈" if total_floating >= 0 else "📉"
    realized   = round(wallet.get('total_pnl', 0.0), 2)
    total_bal  = round(wallet.get('starting', STARTING_BALANCE) + realized + total_floating, 2)

    msg += (
        f"\n{'─' * 24}\n"
        f"{pnl_icon} Realized P&L: *${realized:+.2f}*\n"
        f"{float_icon} Floating P&L: *${total_floating:+.2f}*\n"
        f"💼 Total Balance: *${total_bal:.2f}*\n"
        f"📊 P&L היום: *${pnl_today:+}* · {len(trades_snapshot)} עסקה פעילה"
    )
    send_msg(msg)
    print(f"Heartbeat sent at {now_str} — {len(trades_snapshot)} trade(s)")


def check_heartbeat():
    """מפעיל Heartbeat כל 30 דקות אם יש עסקאות פעילות."""
    global last_heartbeat_time
    if not active_trades:
        return
    now = time.time()
    if last_heartbeat_time is None or (now - last_heartbeat_time) >= HEARTBEAT_INTERVAL:
        send_heartbeat()
        last_heartbeat_time = now


def check_daily_report():
    global last_daily_report_date
    now = datetime.now()
    today = date.today()

    if now.hour == 12 and now.minute < 15:
        if last_daily_report_date != today:
            send_daily_report()
            last_daily_report_date = today

# --- פקודות טלגרם ---

@bot.message_handler(commands=['test'])
def handle_test(message):
    try:
        send_msg("🧪 *מריץ איתות טסט ל-BTC/USDT (4H)...*")
        df_4h  = get_data('BTC/USDT', timeframe='4h', limit=250)
        df_1h  = get_data('BTC/USDT', timeframe='1h', limit=250)
        price  = df_4h['close'].iloc[-1]
        atr    = ta.atr(df_4h['high'], df_4h['low'], df_4h['close'], length=14).iloc[-1]
        open_demo_trade(
            'BTC/USDT', price,
            'Trend=30/30 | MACD=25/25 | RSI=20/20 | BB+Vol=15/15 | Candles=0/10 → TOTAL=90/100 [TEST]',
            df_4h, direction='LONG', score=90, atr=atr,
            timeframe='4H', tf_reason='TEST SIGNAL — טרנד חזק בגרף 4H'
        )
        print(f"Test signal sent for BTC/USDT at {price}")
    except Exception as e:
        send_msg(f"❌ שגיאה בטסט: {e}")

@bot.message_handler(commands=['status'])
def handle_status(message):
    msg = wallet_status_text() + "\n\n"
    if not active_trades:
        msg += "📭 *אין עסקאות פעילות כרגע.*\n"
        msg += f"_סף: {MIN_SCORE}/100 · מקס {MAX_TRADES} עסקאות · Anti-FOMO: RSI≤{RSI_VETO_LONG} · EMA±{EMA_PROXIMITY_PCT}% · Wick Filter_"
        send_msg(msg)
        return
    msg += f"📋 *עסקאות פעילות ({len(active_trades)}/{MAX_TRADES}):*\n\n"
    for i, t in enumerate(active_trades, 1):
        phase_label = "🔄 Trailing" if t.get('phase') == 'trailing' else "📊 Initial"
        be_label    = " · 🔒 BE" if t.get('be_triggered') else ""
        tp1_label   = " · TP1✅" if t.get('tp1_triggered') else ""
        dirlab      = "🟢 LONG" if t.get('direction', 'LONG') == 'LONG' else "🔴 SHORT"
        score       = t.get('score', 0)
        tf          = t.get('timeframe', '4H')
        msg += (
            f"*{i}. {t['symbol']}* {dirlab} [{tf}] · {phase_label}{be_label}{tp1_label}\n"
            f"   ניקוד: *{score}/100* | ATR: `{t.get('atr', 0):.6g}`\n"
            f"   כניסה: `{t['entry']:.6g}`\n"
            f"   🛑 SL: `{t['sl']:.6g}` | 🎯 TP: `{t['tp']:.6g}`\n"
            f"   🔒 BE: `{t['be_lvl']:.6g}` | 🎯 TP1: `{t['tp1']:.6g}`\n"
        )
        if t.get('trailing_sl'):
            ref = "שיא" if t.get('direction', 'LONG') == 'LONG' else "שפל"
            msg += f"   📍 Trailing SL: `{t['trailing_sl']:.6g}` | {ref}: `{t['peak_price']:.6g}`\n"
        msg += "\n"
    msg += f"_לעדכון SL/TP: /update SYMBOL SL TP_\n"
    msg += f"_לסגירה ידנית: /close SYMBOL_"
    send_msg(msg)

@bot.message_handler(commands=['update'])
def handle_update(message):
    """
    שימוש: /update BTC 84000 95000
    או:    /update BTC sl=84000
    או:    /update BTC tp=95000
    """
    try:
        parts = message.text.strip().split()
        if len(parts) < 3:
            send_msg(
                "⚠️ *שימוש שגוי*\n\n"
                "פורמט: `/update SYMBOL SL TP`\n"
                "דוגמה: `/update BTC 84000 95000`\n\n"
                "לעדכון SL בלבד: `/update BTC sl=84000`\n"
                "לעדכון TP בלבד: `/update BTC tp=95000`"
            )
            return

        raw_sym = parts[1].upper()
        symbol  = raw_sym if '/' in raw_sym else f"{raw_sym}/USDT"

        # מציאת העסקה
        trade = next((t for t in active_trades if t['symbol'] == symbol), None)
        if not trade:
            symbols_list = ', '.join(f"`{t['symbol']}`" for t in active_trades) or "_אין_"
            send_msg(f"❌ לא נמצאה עסקה פתוחה עבור `{symbol}`\n\nפתוחות: {symbols_list}")
            return

        new_sl = trade['sl']
        new_tp = trade['tp']
        changes = []

        # פענוח פרמטרים: /update BTC 84000 95000  או sl=84000 tp=95000
        for arg in parts[2:]:
            arg_l = arg.lower()
            if arg_l.startswith('sl='):
                new_sl = float(arg_l.replace('sl=', ''))
                changes.append(f"SL → `{new_sl:.4f}`")
            elif arg_l.startswith('tp='):
                new_tp = float(arg_l.replace('tp=', ''))
                changes.append(f"TP → `{new_tp:.4f}`")
            else:
                # פוזיציה: ארגומנט 2 = SL, ארגומנט 3 = TP
                try:
                    val = float(arg)
                    if len(changes) == 0:
                        new_sl = val
                        changes.append(f"SL → `{new_sl:.4f}`")
                    else:
                        new_tp = val
                        changes.append(f"TP → `{new_tp:.4f}`")
                except ValueError:
                    pass

        # ולידציה: SL < מחיר כניסה < TP
        entry = trade['entry']
        if new_sl >= entry:
            send_msg(f"⚠️ SL ({new_sl}) חייב להיות *מתחת* למחיר הכניסה ({entry:.4f})")
            return
        if new_tp <= entry:
            send_msg(f"⚠️ TP ({new_tp}) חייב להיות *מעל* למחיר הכניסה ({entry:.4f})")
            return

        # עדכון
        old_sl, old_tp = trade['sl'], trade['tp']
        trade['sl'] = new_sl
        trade['tp'] = new_tp
        trade['sl_pct'] = round((entry - new_sl) / entry * 100, 2)
        trade['tp_pct'] = round((new_tp - entry) / entry * 100, 2)

        sl_pct    = trade['sl_pct']
        tp_pct    = trade['tp_pct']
        _ps_upd   = trade.get('pos_size', POSITION_SIZE)
        sl_pnl    = round(_ps_upd * sl_pct / 100, 2)
        tp_pnl    = round(_ps_upd * tp_pct / 100, 2)

        send_msg(
            f"✏️ *עסקה עודכנה — {symbol}*\n\n"
            f"{''.join(chr(10) + '  ' + c for c in changes)}\n\n"
            f"כניסה: `{entry:.4f}`\n"
            f"🛑 SL חדש: `{new_sl:.4f}` (-{sl_pct}% · סיכון: -${sl_pnl})\n"
            f"🎯 TP חדש: `{new_tp:.4f}` (+{tp_pct}% · פוטנציאל: +${tp_pnl})\n\n"
            f"💼 {LEVERAGE}x Isolated · בטחון: ${MARGIN}"
        )
        print(f"Trade updated: {symbol} SL={new_sl} TP={new_tp}")

    except Exception as e:
        send_msg(f"❌ שגיאה בעדכון: {e}")
        print(f"Update error: {e}")

@bot.message_handler(commands=['close'])
def handle_close(message):
    """סגירה ידנית של עסקה: /close BTC"""
    try:
        parts = message.text.strip().split()
        if len(parts) < 2:
            send_msg("⚠️ שימוש: `/close BTC` או `/close BTC/USDT`")
            return

        raw_sym = parts[1].upper()
        symbol  = raw_sym if '/' in raw_sym else f"{raw_sym}/USDT"

        trade = next((t for t in active_trades if t['symbol'] == symbol), None)
        if not trade:
            send_msg(f"❌ לא נמצאה עסקה פתוחה עבור `{symbol}`")
            return

        ticker        = exchange.fetch_ticker(symbol)
        current_price = ticker['last']
        entry         = trade['entry']

        # חישוב P&L בפועל
        _ps_m = trade.get('pos_size', POSITION_SIZE)   # per-trade position size
        if trade.get('tp1_triggered'):
            # חצי פוזיציה נסגרת עכשיו, חצי כבר נסגר ב-TP1
            half_m   = _ps_m / 2
            half_pnl = round(half_m * (current_price - entry) / entry * 100 / 100, 2)
            total    = round(trade.get('tp1_pnl', 0) + half_pnl, 2)
            pnl_str  = f"TP1 + יציאה: *{'+' if total>=0 else ''}${total}*"
        else:
            pct     = (current_price - entry) / entry * 100
            pnl     = round(_ps_m * pct / 100, 2)
            pnl_str = f"P&L: *{'+' if pnl>=0 else ''}${pnl}* ({pct:+.2f}%)"

        # חישוב P&L נטו לארנק
        if trade.get('tp1_triggered'):
            net_pnl = round(trade.get('tp1_pnl', 0) + round(_ps_m / 2 * (current_price - entry) / entry, 2), 2)
        else:
            direction_m = trade.get('direction', 'LONG')
            raw_pct     = (current_price - entry) / entry * 100
            pnl_pct_m   = raw_pct if direction_m == 'LONG' else -raw_pct
            net_pnl     = round(_ps_m * pnl_pct_m / 100, 2)

        with trades_lock:
            active_trades.remove(trade)
        wallet_credit(net_pnl)
        _log_closed_trade(trade, 'Manual', net_pnl, current_price)
        eq = _get_equity()
        save_active_trades()
        if current_price >= entry:
            daily_stats['wins'] += 1
        else:
            daily_stats['losses'] += 1
        daily_stats['close_reasons']['Manual'] += 1

        send_msg(
            f"🚪 *סגירה ידנית — {symbol}*\n\n"
            f"כניסה: `{entry:.4f}` → יציאה: `{current_price:.4f}`\n"
            f"{pnl_str}\n"
            f"💼 {LEVERAGE}x Isolated\n"
            f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
            f"סה\"כ היום: ${round(daily_stats.get('total_pnl', 0), 2):+}"
        )
        print(f"Manual close: {symbol} at {current_price}")

    except Exception as e:
        send_msg(f"❌ שגיאה בסגירה: {e}")

@bot.message_handler(commands=['report'])
def handle_report(message):
    send_daily_report()

@bot.message_handler(commands=['ping'])
def handle_ping(message):
    now        = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    uptime_msg = f"🟢 *הבוט פעיל!*\n\n"
    uptime_msg += f"🕐 שעה: `{now}`\n"
    uptime_msg += f"📊 עסקאות פעילות: *{len(active_trades)}*\n"
    if active_trades:
        for t in active_trades:
            phase = "🔄 Trailing" if t.get('phase') == 'trailing' else "📊 Initial"
            uptime_msg += f"   • `{t['symbol']}` — {phase}\n"
    uptime_msg += f"\n📈 P&L היום: *${round(daily_stats.get('total_pnl', 0), 2):+}*\n"
    uptime_msg += f"✅ ניצחונות: {daily_stats.get('wins', 0)} · ❌ הפסדים: {daily_stats.get('losses', 0)}\n"
    uptime_msg += f"\n🖥 [פתח דאשבורד]({DASHBOARD_URL})\n"
    uptime_msg += f"_הסריקה הבאה בעוד פחות משעה_"
    send_msg(uptime_msg)

@bot.message_handler(commands=['dashboard'])
def handle_dashboard(message):
    send_msg(
        f"🖥 *דאשבורד הבוט*\n\n"
        f"[👉 לחץ כאן לפתיחת הדאשבורד]({DASHBOARD_URL})\n\n"
        f"_תראה שם: Top 15 מועמדים חמים, גיינרים, ווליום ועוד_"
    )

@bot.message_handler(commands=['home', 'start', 'help', 'menu'])
def handle_home(message):
    """מסך ראשי — כל הפקודות של הבוט."""
    with trades_lock:
        n_trades = len(active_trades)
    eq        = _get_equity()
    bal       = wallet.get('balance', STARTING_BALANCE)
    free      = max(0, bal - n_trades * MARGIN)
    pnl_today = round(daily_stats.get('total_pnl', 0), 2)
    pnl_icon  = "📈" if pnl_today >= 0 else "📉"
    regime    = get_btc_regime()
    regime_emoji = "🟢" if regime == 'BULL' else ("🔴" if regime == 'BEAR' else "🟡")

    send_msg(
        f"🤖 *Crypto Trading Bot — תפריט ראשי*\n"
        f"{'─' * 30}\n\n"
        f"📊 *מצב נוכחי*\n"
        f"  {regime_emoji} BTC Regime: *{regime}*\n"
        f"  💼 עסקאות פעילות: *{n_trades}/{MAX_TRADES}*\n"
        f"  💰 Equity: *${eq:.2f}*  |  פנוי: *${free:.2f}*\n"
        f"  {pnl_icon} P&L היום: *${pnl_today:+}*\n\n"
        f"{'─' * 30}\n"
        f"📋 *פקודות מידע*\n"
        f"  /status     — עסקאות פעילות + SL/TP\n"
        f"  /report     — דוח יומי מלא\n"
        f"  /scanreport — דוח סריקה אחרון\n"
        f"  /ping       — בדיקת חיות הבוט\n\n"
        f"🔍 *פקודות פעולה*\n"
        f"  /scan              — סריקה ידנית עכשיו\n"
        f"  /close BTC         — סגירת עסקה ידנית\n"
        f"  /update BTC 84000 95000 — עדכון SL/TP\n\n"
        f"🖥 *דאשבורד*\n"
        f"  /dashboard — קבל קישור לדאשבורד\n"
        f"  [👉 פתח דאשבורד]({DASHBOARD_URL})\n\n"
        f"{'─' * 30}\n"
        f"⚙️ *פרמטרים*\n"
        f"  📐 סף איתות: *{MIN_SCORE}/100*\n"
        f"  💵 מרג'ין לעסקה: *${MARGIN}*  ·  מינוף: *{LEVERAGE}x*\n"
        f"  🛡️ SL: *{SL_PCT_FIXED}%*  ·  TP1: *{TP1_PCT_FIXED}%*  ·  TP: *{TP_PCT_FIXED}%*\n"
        f"  ⏰ סריקה כל שעה  ·  מעקב כל 60 שניות"
    )


@bot.message_handler(commands=['scanreport'])
def handle_scanreport(message):
    """שולח את דוח הסריקה האחרונה."""
    import json as _json
    try:
        with open(SCAN_REPORT_FILE, 'r', encoding='utf-8') as f:
            d = _json.load(f)
    except FileNotFoundError:
        send_msg("⚠️ *אין דוח סריקה עדיין*\n_הפעל /scan כדי לייצר דוח ראשון_")
        return
    except Exception as e:
        send_msg(f"❌ שגיאה בקריאת הדוח: {e}")
        return

    scan_time = d.get('scan_time', '')
    try:
        from datetime import datetime as _dt
        dt = _dt.fromisoformat(scan_time)
        time_str = dt.strftime('%d/%m/%Y %H:%M')
    except Exception:
        time_str = scan_time

    total     = d.get('total_scanned', 0)
    signals   = d.get('signals_found', 0)
    regime    = d.get('btc_regime', 'NEUTRAL')
    fng_v     = d.get('fng_value', 50)
    fng_lbl   = d.get('fng_label', 'Neutral')
    sentiment = d.get('market_sentiment_factor', '')
    sys_msg   = d.get('system_message', '')
    duration  = d.get('scan_duration_s', 0)
    rejected  = d.get('rejected_coins', [])
    regime_e  = "🟢" if regime == 'BULL' else ("🔴" if regime == 'BEAR' else "🟡")
    sig_e     = "✅" if signals > 0 else "⭕"

    msg  = f"🔍 *Scan Analysis Report*\n"
    msg += f"{'─' * 28}\n"
    msg += f"🕐 זמן סריקה: `{time_str}` ({duration}s)\n"
    msg += f"📦 נסרקו: *{total}* מטבעות\n"
    msg += f"{sig_e} איתותים שנמצאו: *{signals}*\n"
    msg += f"{regime_e} BTC Regime: *{regime}*\n"
    msg += f"📊 FNG: *{fng_v}* ({fng_lbl})\n\n"
    msg += f"📈 *Sentiment Factor:*\n_{sentiment}_\n\n"
    msg += f"💬 *System Message:*\n_{sys_msg}_\n"

    if rejected:
        msg += f"\n{'─' * 28}\n"
        msg += f"🚫 *Top Near\\-Misses (לא עברו סף {MIN_SCORE}/100):*\n"
        for i, c in enumerate(rejected[:5], 1):
            score = c.get('best_score', 0)
            dir_  = c.get('direction', '')
            dir_e = "🟢" if dir_ == 'LONG' else "🔴"
            reason = c.get('reason', '')[:55]
            score_e = "🟡" if score >= 80 else ("🟠" if score >= 60 else "⚫")
            msg += f"{i}\\. {dir_e} `{c.get('symbol','?')}` {score_e} *{score}/100*\n"
            msg += f"   _{reason}_\n"

    # Bubble Watch section
    bubbles = d.get('bubble_watch', [])
    if bubbles:
        msg += f"\n{'─' * 28}\n"
        msg += f"🫧 *Bubble Watch — תנודתיות גבוהה \\(>10% ב\\-24h\\)*\n"
        msg += f"_לתצפית בלבד · ללא שינוי בציון · כל כללי הבטיחות פעילים_\n"
        for b in bubbles[:8]:
            sym     = b.get('symbol', '?')
            pct     = b.get('change_pct', 0)
            dir_    = b.get('direction', '')
            dir_e   = "🟢" if dir_ == 'LONG' else "🔴"
            vol_m   = round(b.get('volume_usd', 0) / 1_000_000, 1)
            pct_str = f"+{pct:.1f}" if pct >= 0 else f"{pct:.1f}"
            fire    = "🔥" if abs(pct) > 20 else "⚡"
            msg += f"{fire} {dir_e} `{sym}` *{pct_str}%* · Vol: ${vol_m}M\n"
    else:
        msg += f"\n{'─' * 28}\n"
        msg += f"🫧 *Bubble Watch:* _אין מטבעות עם שינוי >10% ב-24h_\n"

    send_msg(msg)


@bot.message_handler(commands=['scan'])
def handle_scan(message):
    """סריקה מיידית — מופעלת ב-Thread נפרד כדי לא לחסום את ה-polling."""
    global _scan_running
    if _scan_running:
        send_msg("⏳ *סריקה כבר רצה ברקע* — המתן לסיומה.")
        return

    send_msg(
        f"🔍 *מפעיל סריקה ידנית עכשיו...*\n"
        f"📐 סף: *{MIN_SCORE}/100* · מקסימום {MAX_TRADES} עסקאות\n"
        f"_תקבל עדכון בסיום הסריקה_"
    )

    def run_manual_scan():
        global _scan_running
        _scan_running = True
        try:
            now_str    = datetime.now().strftime('%H:%M:%S')
            btc_regime = get_btc_regime()
            regime_emoji = "🟢" if btc_regime == 'BULL' else ("🔴" if btc_regime == 'BEAR' else "🟡")
            regime_note  = (
                "BULL — LONGs מאושרים" if btc_regime == 'BULL' else
                "BEAR — SHORTs מאושרים" if btc_regime == 'BEAR' else
                "NEUTRAL — כל הכיוונים"
            )

            gainers, losers = get_hot_candidates()
            total_scanned   = len(gainers) + len(losers) + len(ENERGY_GEO)

            send_msg(
                f"🔍 *Manual Scan* — {now_str}\n"
                f"🟢 Gainers: *{len(gainers)}*  🔴 Losers: *{len(losers)}*\n"
                f"{regime_emoji} BTC Regime: *{regime_note}*"
            )

            fng_v_m, fng_lbl_m, _ = sentiment_check("manual_scan")
            all_rejections_m  = []
            signals_found     = 0
            scan_start_m      = time.time()
            bubble_watch_m    = build_bubble_watch(gainers, losers, threshold=10.0)
            signals_found += _scan_batch(gainers, 'LONG', btc_regime, all_rejections_m)
            signals_found += _scan_batch(losers, 'SHORT', btc_regime, all_rejections_m)
            energy_candidates = [{'symbol': s} for s in ENERGY_GEO]
            signals_found += _scan_batch(energy_candidates, 'LONG', btc_regime, all_rejections_m)

            pnl_today = round(daily_stats.get('total_pnl', 0), 2)
            bub_note = ""
            if bubble_watch_m:
                bub_names = ", ".join(
                    f"{b['symbol'].replace('/USDT','')} ({b['change_pct']:+.1f}%)"
                    for b in bubble_watch_m[:4]
                )
                bub_note = f"\n🫧 *Bubble Watch ({len(bubble_watch_m)}):* {bub_names}"
            send_msg(
                f"✅ *סריקה ידנית הושלמה*\n\n"
                f"🔍 נסרקו: *{total_scanned}* מטבעות\n"
                f"📊 איתותים: *{signals_found}*\n"
                f"📊 עסקאות פעילות: *{len(active_trades)}*\n"
                f"💰 P&L היום: *${pnl_today:+}*"
                f"{bub_note}\n"
                f"_השתמש ב /scanreport לדוח מלא_"
            )

            # שמירת דוח סריקה
            if signals_found == 0 and len(active_trades) >= MAX_TRADES:
                sys_msg_m = f"מקסימום עסקאות ({MAX_TRADES}/{MAX_TRADES}) — ממתין לסגירה"
            elif signals_found == 0:
                top_m = sorted(all_rejections_m, key=lambda x: x.get('best_score', 0), reverse=True)
                best_m = top_m[0] if top_m else None
                sys_msg_m = (
                    f"אף מטבע לא הגיע לציון {MIN_SCORE}/100. הטוב ביותר: {best_m['symbol']} עם {best_m['best_score']}/100" if best_m
                    else f"אף מטבע לא עמד בסף {MIN_SCORE}/100"
                )
            else:
                sys_msg_m = f"{signals_found} עסקה/ות נפתחו — סריקה ידנית"
            save_scan_results(total_scanned, signals_found, fng_v_m, fng_lbl_m,
                              btc_regime, all_rejections_m, sys_msg_m, scan_start_m,
                              bubble_watch=bubble_watch_m)
        except Exception as e:
            send_msg(f"❌ שגיאה בסריקה: {e}")
        finally:
            _scan_running = False

    threading.Thread(target=run_manual_scan, daemon=True).start()


def start_telegram_polling():
    """
    Polling עם טיפול חכם ב-409:
    - אם מגיע 409 → יש instance אחר כבר פועל (Autoscale).
      ממתינים 5 דקות לפני ניסיון נוסף.
    - שאר השגיאות → ניסיון חוזר אחרי 5 שניות.
    - ה-send_msg() ממשיך לעבוד גם ב-send-only mode.
    """
    import os
    is_deployed = os.environ.get('REPLIT_DEPLOYMENT', '') == '1'
    print(f"Telegram polling started... [{'PROD/Autoscale' if is_deployed else 'DEV'}]")
    consecutive_409 = 0
    while True:
        try:
            bot.polling(non_stop=False, timeout=30, long_polling_timeout=30)
            consecutive_409 = 0
        except Exception as e:
            err_str = str(e)
            if '409' in err_str:
                consecutive_409 += 1
                wait = min(300, 30 * consecutive_409)   # עד 5 דקות
                print(
                    f"⚠️  Telegram 409 — instance אחר פועל (Autoscale?). "
                    f"send_msg עדיין פעיל. ממתין {wait}s לפני retry #{consecutive_409}..."
                )
                time.sleep(wait)
            else:
                consecutive_409 = 0
                print(f"Polling error: {e}")
                time.sleep(5)

# --- לולאת מעקב עסקאות — Thread נפרד ---

def trade_monitor_loop():
    """
    רץ בThread נפרד.
    בודק SL / TP / BE / Trailing כל 60 שניות — ללא תלות בסריקה.
    שולח Heartbeat כל 30 דקות כשיש עסקאות פעילות.
    בודק שינוי מצב Kill-Switch (FNG) בכל איטרציה.
    """
    print("Trade monitor started — checking every 60s")
    while True:
        try:
            # ── Kill-Switch state change detection ──
            check_kill_switch_change()

            if active_trades:
                track_trades()
                check_daily_report()
                check_heartbeat()
        except Exception as e:
            print(f"Trade monitor error: {e}")
        time.sleep(60)

# --- לולאת סריקת איתותים — Thread נפרד ---

def _scan_batch(candidates, direction, btc_regime='NEUTRAL', rejected_out=None):
    """
    עוזר לסריקה: מריץ score_symbol על רשימת מועמדים.
    direction: 'LONG' או 'SHORT'
    btc_regime: 'BULL' / 'BEAR' / 'NEUTRAL' — BTC EMA50 Market Regime Filter
    rejected_out: רשימה שבה יצטברו מטבעות שנדחו (לדוח הסריקה)
    מחזיר מספר האיתותים שנמצאו.
    """
    if rejected_out is None:
        rejected_out = []

    # ── Sentiment Kill-Switch (Extreme Fear < 15) ─────────────────────────────
    fng_v_scan, fng_lbl_scan, fng_action = sentiment_check("scan")
    if fng_v_scan < EXTREME_FEAR_THRESHOLD:
        print(f"SENTIMENT KILL-SWITCH: FNG={fng_v_scan} < {EXTREME_FEAR_THRESHOLD} — סריקה בוטלה לגמרי")
        for c in candidates:
            rejected_out.append({
                'symbol': c['symbol'], 'direction': direction, 'best_score': 0,
                'reason': f'Sentiment Kill-Switch: FNG={fng_v_scan} (Extreme Fear < {EXTREME_FEAR_THRESHOLD})',
                'scores': {},
            })
        return 0

    # ── BTC Market Regime Safety Switch ──
    if direction == 'LONG' and btc_regime == 'BEAR':
        print(f"BTC REGIME VETO: BEAR market — skipping all {len(candidates)} LONG candidates")
        for c in candidates:
            rejected_out.append({
                'symbol': c['symbol'], 'direction': direction, 'best_score': 0,
                'reason': 'BTC BEAR Regime — LONGs חסומים',
                'scores': {},
            })
        return 0
    if direction == 'SHORT' and btc_regime == 'BULL':
        print(f"BTC REGIME VETO: BULL market — skipping all {len(candidates)} SHORT candidates")
        for c in candidates:
            rejected_out.append({
                'symbol': c['symbol'], 'direction': direction, 'best_score': 0,
                'reason': 'BTC BULL Regime — SHORTs חסומים',
                'scores': {},
            })
        return 0

    found = 0
    for candidate in candidates:
        if len(active_trades) >= MAX_TRADES:
            print(f"Max trades ({MAX_TRADES}) reached — skipping rest of batch")
            break
        symbol = candidate['symbol']
        if any(t['symbol'] == symbol for t in active_trades):
            continue
        try:
            # ── שלב 1: ניסיון על 4H — טרנד ראשי ──
            df_4h  = get_data(symbol, timeframe='4h', limit=250)
            df_1h  = get_data(symbol, timeframe='1h', limit=250)
            price  = df_4h['close'].iloc[-1]

            print(f"Scoring {symbol} [{direction}] @ {price:.6g} [4H]")
            score, breakdown, atr = score_symbol(df_4h, df_1h, symbol, direction)
            score_4h  = score
            score_1h  = 0
            score_15m = 0
            best_score     = score
            best_breakdown = breakdown
            chosen_tf = '4H'
            chosen_df = df_4h
            tf_reason = 'טרנד חזק בגרף 4H'

            # ── שלב 2: אם 4H לא מספיק — נסה 1H ──
            if score < MIN_SCORE:
                df_15m = get_data(symbol, timeframe='15m', limit=250)
                score_1h, breakdown_1h, atr_1h = score_symbol(df_1h, df_15m, symbol, direction)
                print(f"  4H={score_4h} < {MIN_SCORE} → try 1H: {score_1h}")
                if score_1h > best_score:
                    best_score     = score_1h
                    best_breakdown = breakdown_1h
                if score_1h >= MIN_SCORE:
                    score     = score_1h
                    breakdown = breakdown_1h
                    atr       = atr_1h
                    chosen_tf = '1H'
                    chosen_df = df_1h
                    price     = df_1h['close'].iloc[-1]
                    tf_reason = f'High Volatility Entry ב-1H (4H={score_4h} < {MIN_SCORE})'

                # ── שלב 3: אם גם 1H לא מספיק — נסה 15m (Scalp) ──
                else:
                    score_15m, breakdown_15m, atr_15m = score_symbol(df_15m, df_1h, symbol, direction)
                    print(f"  1H={score_1h} < {MIN_SCORE} → try 15m: {score_15m}")
                    if score_15m > best_score:
                        best_score     = score_15m
                        best_breakdown = breakdown_15m
                    if score_15m >= MIN_SCORE:
                        score     = score_15m
                        breakdown = breakdown_15m
                        atr       = atr_15m
                        chosen_tf = '15m'
                        chosen_df = df_15m
                        price     = df_15m['close'].iloc[-1]
                        tf_reason = f'Scalp Entry ב-15m (4H={score_4h}, 1H={score_1h} < {MIN_SCORE})'

            if score >= MIN_SCORE:
                # חישוב RSI ו-EMA200 רגע לפני פתיחה לשמירה בדוח
                try:
                    _last_rsi   = ta.rsi(chosen_df['close'], length=14).iloc[-1]
                    _last_ema   = ta.ema(chosen_df['close'], length=200).iloc[-1]
                except Exception:
                    _last_rsi = _last_ema = None

                # ── Fear Filter: LONG requires RSI < 30 ─────────────────────
                if fng_v_scan <= FEAR_THRESHOLD and direction == 'LONG':
                    rsi_ok = _last_rsi is not None and _last_rsi < 30
                    if not rsi_ok:
                        rsi_str = f"{_last_rsi:.1f}" if _last_rsi else "N/A"
                        print(f"  [SENTIMENT] FEAR FILTER: {symbol} LONG rejected — RSI={rsi_str} ≥ 30 (דרוש RSI<30 במצב פחד)")
                        rejected_out.append({
                            'symbol': symbol, 'direction': direction, 'best_score': best_score,
                            'reason': f'Fear Filter: RSI={rsi_str} ≥ 30 (ב-Sentiment FEAR נדרש RSI<30)',
                            'scores': {'4H': score_4h, '1H': score_1h, '15m': score_15m},
                        })
                        continue

                # ── Claude AI Final Filter ──────────────────────────────────────
                claude_go, claude_reason = claude_filter(
                    symbol=symbol, direction=direction, score=score,
                    breakdown=breakdown, price=price, timeframe=chosen_tf,
                    btc_regime=btc_regime, fng_v=fng_v_scan, fng_lbl=fng_lbl_scan
                )
                if not claude_go:
                    print(f"  [Claude] ❌ NO-GO: {symbol} {direction} — {claude_reason}")
                    send_msg(
                        f"🤖 *Claude AI — NO\\-GO*\n\n"
                        f"{'🟢' if direction == 'LONG' else '🔴'} `{symbol}` {direction} · {chosen_tf}\n"
                        f"📊 ציון: *{score}/100* ✅ עבר\n"
                        f"🚫 *Claude חסם:* _{claude_reason}_"
                    )
                    rejected_out.append({
                        'symbol': symbol, 'direction': direction, 'best_score': score,
                        'reason': f'Claude NO-GO: {claude_reason[:60]}',
                        'scores': {'4H': score_4h, '1H': score_1h, '15m': score_15m},
                    })
                    continue

                print(f"  [Claude] ✅ GO: {symbol} {direction} — {claude_reason}")
                # ── פתיחת עסקה ──────────────────────────────────────────────────
                open_demo_trade(
                    symbol, price, breakdown,
                    chosen_df, direction=direction,
                    score=score, atr=atr,
                    timeframe=chosen_tf, tf_reason=tf_reason,
                    rsi=_last_rsi, ema200=_last_ema,
                    fng_v=fng_v_scan
                )
                found += 1
            else:
                # ניקוד לא מספיק בכל הטיים-פריימים → דחייה
                rejected_out.append({
                    'symbol':     symbol,
                    'direction':  direction,
                    'best_score': best_score,
                    'reason':     _reject_reason(best_score, best_breakdown),
                    'scores':     {'4H': score_4h, '1H': score_1h, '15m': score_15m},
                })

        except Exception as e:
            print(f"Error scanning {symbol}: {e}")
    return found


def _maybe_run_drive_audit():
    """מפעיל דוח יומי כולל ב-12:00 — פעם אחת ביום."""
    global _last_audit_hour
    now_hour = datetime.now().hour
    if now_hour in AUDIT_HOURS and now_hour != _last_audit_hour:
        _last_audit_hour = now_hour
        try:
            from gdrive_reporter import run_audit_upload
            run_audit_upload(
                active_trades, wallet, closed_trades_log,
                send_telegram=send_msg
            )
            print(f"Daily audit report generated at {now_hour:02d}:00")
        except ImportError:
            print("gdrive_reporter not available — skipping audit")
        except Exception as e:
            print(f"Audit error: {e}")


def scan_loop():
    """
    רץ בThread נפרד.
    סורק Top 15 Gainers (LONG) + Top 15 Losers (SHORT) פעם בשעה.
    מפעיל Professional Scoring System — מינימום 85 נקודות לאיתות.
    """
    print("Scan loop started — scanning every 60 minutes")
    while True:
        try:
            check_daily_report()
            now_str = datetime.now().strftime('%H:%M:%S')
            scan_start_ts = time.time()

            # ── שלב 1: BTC Market Regime ──
            btc_regime = get_btc_regime()
            regime_emoji = "🟢" if btc_regime == 'BULL' else ("🔴" if btc_regime == 'BEAR' else "🟡")
            regime_note  = (
                "BULL — LONGs מאושרים, SHORTs חסומים" if btc_regime == 'BULL' else
                "BEAR — SHORTs מאושרים, LONGs חסומים" if btc_regime == 'BEAR' else
                "NEUTRAL — כל הכיוונים פתוחים"
            )

            # ── שלב 2: שלוף גיינרים ולוזרים ──
            gainers, losers = get_hot_candidates()
            total_scanned   = len(gainers) + len(losers) + len(ENERGY_GEO)

            # Bubble Watch — תצפית בלבד, ללא שינוי ציון
            bubble_watch_list = build_bubble_watch(gainers, losers, threshold=10.0)

            # FNG לדוח הסריקה
            fng_v_loop, fng_lbl_loop, _ = sentiment_check("scan_summary")

            send_msg(
                f"🔍 *Professional Scoring Scan* — {now_str}\n"
                f"🟢 Gainers (LONG): *{len(gainers)}*  🔴 Losers (SHORT): *{len(losers)}*\n"
                f"📐 סף: *{MIN_SCORE}/100* · גרפים: 4H → 1H → 15m\n"
                f"🛡️ Anti-FOMO: RSI≤{RSI_VETO_LONG} · EMA±{EMA_PROXIMITY_PCT}% · Wick Filter\n"
                f"{regime_emoji} *BTC Regime: {regime_note}*"
            )

            signals_found  = 0
            all_rejections = []   # ← אוסף דחיות מכל הבאצ'ים

            # ── שלב 3: סריקת גיינרים — LONG ──
            signals_found += _scan_batch(gainers, 'LONG', btc_regime, all_rejections)

            # ── שלב 4: סריקת לוזרים — SHORT ──
            signals_found += _scan_batch(losers, 'SHORT', btc_regime, all_rejections)

            # ── שלב 5: רשימה קבועה — ENERGY_GEO (LONG בלבד) ──
            energy_candidates = [{'symbol': s} for s in ENERGY_GEO]
            signals_found += _scan_batch(energy_candidates, 'LONG', btc_regime, all_rejections)

            print(f"Scan done — {signals_found} signal(s) / {total_scanned} scanned")

            # ── סיכום סריקה ──
            now       = datetime.now().strftime('%H:%M')
            next_scan = (datetime.now() + timedelta(hours=1)).strftime('%H:%M')
            pnl_today = round(daily_stats.get('total_pnl', 0), 2)
            pnl_icon  = "📈" if pnl_today >= 0 else "📉"

            summary  = f"✅ *סריקה הושלמה — {now}*\n\n"
            summary += f"🔍 נסרקו: *{total_scanned}* מטבעות\n"
            summary += f"📊 איתותים שנמצאו: *{signals_found}*\n"
            summary += f"📊 עסקאות פעילות: *{len(active_trades)}/{MAX_TRADES}*\n"
            if active_trades:
                for t in active_trades:
                    phase  = "🔄 Trailing" if t.get('phase') == 'trailing' else "📊 Initial"
                    dirlab = "🟢" if t.get('direction') == 'LONG' else "🔴"
                    score  = t.get('score', 0)
                    tf     = t.get('timeframe', '4H')
                    summary += f"   {dirlab} `{t['symbol']}` [{tf}] {phase} · {score}/100\n"
            summary += f"\n{pnl_icon} P&L היום: *${pnl_today:+}*\n"
            summary += wallet_status_text().replace('💼 *ארנק וירטואלי*\n', '') + "\n"
            if bubble_watch_list:
                bub_str = ", ".join(
                    f"{b['symbol'].replace('/USDT','')} ({b['change_pct']:+.1f}%)"
                    for b in bubble_watch_list[:5]
                )
                summary += f"🫧 *Bubble Watch ({len(bubble_watch_list)}):* {bub_str}\n"
            summary += f"⏰ סריקה הבאה: `{next_scan}`\n"
            summary += f"_📍 מעקב עסקאות פעיל כל 60 שניות_"
            send_msg(summary)

            print(f"Scan complete at {now}. Next scan at {next_scan}.")

            # ── שמירת Scan Analysis Report ──
            if signals_found == 0 and len(active_trades) >= MAX_TRADES:
                sys_msg = f"מקסימום עסקאות פעיל ({MAX_TRADES}/{MAX_TRADES}) — ממתין לסגירת עסקה לפני פתיחה חדשה"
            elif signals_found == 0 and fng_v_loop < EXTREME_FEAR_THRESHOLD:
                sys_msg = f"Kill-Switch Sentiment: FNG={fng_v_loop} (Extreme Fear) — הסריקה בוטלה לבטיחות"
            elif signals_found == 0 and btc_regime == 'BEAR':
                sys_msg = f"BTC BEAR Regime — כל ה-LONGs חסומים; SHORTs בלבד מאושרים"
            elif signals_found == 0 and btc_regime == 'BULL':
                sys_msg = f"BTC BULL Regime — כל ה-SHORTs חסומים; LONGs בלבד מאושרים"
            elif signals_found == 0:
                top = sorted(all_rejections, key=lambda x: x.get('best_score', 0), reverse=True)
                best = top[0] if top else None
                sys_msg = (
                    f"אף מטבע לא הגיע לציון {MIN_SCORE}/100 הנדרש. "
                    f"הטוב ביותר: {best['symbol']} עם {best['best_score']}/100" if best
                    else f"אף מטבע לא עמד בסף {MIN_SCORE}/100"
                )
            else:
                sys_msg = f"{signals_found} עסקה/ות נפתחו בהצלחה — Professional Score ≥ {MIN_SCORE}/100"

            save_scan_results(
                total_scanned  = total_scanned,
                signals_found  = signals_found,
                fng_value      = fng_v_loop,
                fng_label      = fng_lbl_loop,
                btc_regime     = btc_regime,
                all_rejections = all_rejections,
                system_message = sys_msg,
                scan_start_ts  = scan_start_ts,
                bubble_watch   = bubble_watch_list,
            )

            # ── דוח Drive ב-12:00 ──
            _maybe_run_drive_audit()

        except Exception as e:
            print(f"Scan loop error: {e}")

        time.sleep(3600)

# --- הלולאה הראשית ---

def main():
    keep_alive()
    load_wallet()          # ← טעינת ארנק וירטואלי
    load_active_trades()   # ← שחזור עסקאות פעילות לאחר restart

    # Thread 1 — Telegram polling
    polling_thread = threading.Thread(target=start_telegram_polling, daemon=True)
    polling_thread.start()

    # Thread 2 — מעקב עסקאות כל 60 שניות
    monitor_thread = threading.Thread(target=trade_monitor_loop, daemon=True)
    monitor_thread.start()

    # Thread 3 — סריקת איתותים כל שעה
    scan_thread = threading.Thread(target=scan_loop, daemon=True)
    scan_thread.start()

    send_msg(
        "🤖 *הבוט התחיל ב-Replit!*\n\n"
        "⚙️ *מצב הלולאות:*\n"
        "🔍 סריקת איתותים: כל *60 דקות*\n"
        "📍 מעקב SL/TP:    כל *60 שניות*\n\n"
        f"📋 /home — תפריט ראשי עם כל הפקודות\n"
        f"🖥 [פתח דאשבורד]({DASHBOARD_URL})"
    )

    # Thread הראשי נשאר ער
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
