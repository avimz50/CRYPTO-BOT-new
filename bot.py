import os
import io
import json
import csv
import signal
import sys
import claude_gate
import claude_research

print("[BOT-BEACON] bot.py process started — beginning imports", flush=True, file=sys.stderr)
sys.stderr.flush()
import asyncio
import requests
import ccxt
import ccxt.pro as ccxt_async
import telebot
import time
import threading
import pandas as pd
import pandas_ta as ta
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from keep_alive import keep_alive, app as flask_app
from flask import jsonify as flask_jsonify, request as flask_request
import gdrive_reporter
from config import *
from market_logic import (
    get_fear_greed,
    get_fng_blended,
    get_fng_regime,
    get_fng_components,
    sentiment_check, set_sentiment_thresholds,
    score_symbol, detect_fvg, detect_order_blocks,
    detect_flag, detect_bb_squeeze, detect_volume_buildup,
    detect_rsi_divergence, score_candles,
    momentum_gate,
    adaptive_threshold,
    adaptive_exit_params,
)

# ── אזור זמן ישראל — ZoneInfo עובד גם ב-Production ──
_IL_TZ = ZoneInfo('Asia/Jerusalem')

def now_il() -> datetime:
    """מחזיר datetime נוכחי בשעון ישראל — עובד ב-dev וב-production."""
    return datetime.now(_IL_TZ)

# ספריות גרף — נטענות lazy בתוך generate_chart (לא בטעינת module)
# כך Flask מתחיל מיד ולא מחכה לבניית font cache של matplotlib
CHARTS_ENABLED = True  # ניסיון ייבוא יתבצע בפונקציה
_mpl_imported = False  # דגל lazy-import
plt = None  # יאותחל ב-generate_chart בפעם הראשונה
mpf = None  # יאותחל ב-generate_chart בפעם הראשונה

# --- הגדרות וחיבורים ---
import state_store        # Replit Object Storage persistence (durable across deploys)
import database_manager as db  # Single source of truth — all state I/O goes here
import strategy_engine  as se  # Pure math — ATR / SL / TP calculations (no wallet access)
print("[BOOT] bot.py loading — env check...", flush=True)
_missing = [k for k in ('BITGET_KEY','BITGET_SECRET','BITGET_PW','TELEGRAM_TOKEN','CHAT_ID') if not os.environ.get(k)]
if _missing:
    print(f"[BOOT] FATAL — missing env vars: {_missing}", flush=True)
else:
    print("[BOOT] All required env vars present.", flush=True)

exchange = ccxt.bitget({
    'apiKey': os.environ['BITGET_KEY'],
    'secret': os.environ['BITGET_SECRET'],
    'password': os.environ['BITGET_PW'],
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'},
})
# Market-data-only instance — NO paper flag.
# Bitget Paper mode blocks fetch_tickers() (returns 0 pairs).
# Real swap market data is identical for virtual & live accounts.
exchange_md = ccxt.bitget({
    'apiKey': os.environ['BITGET_KEY'],
    'secret': os.environ['BITGET_SECRET'],
    'password': os.environ['BITGET_PW'],
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'},
})
print("[BOOT] ccxt exchange OK.", flush=True)
claude_gate.set_exchange(exchange)      # enrich gate signals with live market data
claude_research.set_exchange(exchange)  # enrich research with live TA

bot = telebot.TeleBot(os.environ['TELEGRAM_TOKEN'])
CHAT_ID = os.environ['CHAT_ID']
print("[BOOT] Telegram bot OK.", flush=True)

# Webhook secret — must match the secret_token used when registering the webhook via setWebhook.
# Set TG_WEBHOOK_SECRET to a strong random string in the environment.
_TG_WEBHOOK_SECRET = os.environ.get('TG_WEBHOOK_SECRET', '')

def _is_authorized(chat_id) -> bool:
    """Return True only if chat_id matches the configured operator CHAT_ID."""
    return str(chat_id) == str(CHAT_ID)

# ENERGY_GEO, HOT_CANDIDATES_FILE, ACTIVE_TRADES_FILE, WALLET_FILE, AUDIT_LOG_FILE,
# STARTING_BALANCE, DASHBOARD_URL → config.py (from config import *)

# ─── Fear & Greed Index — cache גלובלי (מתרענן כל שעה) ───────────────────────
_fng_cache = {'value': 50, 'label': 'Neutral', 'ts': 0}

# ─── Bot Pause Flag ────────────────────────────────────────────────────────────
_bot_paused: bool = False   # /stop → True | /resume → False

# ─── FNG State Tracker ──────────────────────────────────────────────────────────
# None = לא ידוע (הפעלה ראשונה) | True = Extreme Fear | False = רגיל
_extreme_fear_active: bool | None = None

# get_fear_greed() → moved to market_logic.py

# ─── Global Sentiment Thresholds — loaded via config.load_fng_settings() ──────
_fng_loaded            = load_fng_settings()   # from config import *
EXTREME_FEAR_THRESHOLD = _fng_loaded['extreme_fear']  # Extreme Fear: סף FNG נמוך
FEAR_THRESHOLD         = _fng_loaded['fear']           # Fear Filter: RSI<30 ל-LONG + SL+1%
GREED_THRESHOLD        = _fng_loaded['greed']          # Greed Filter: פוזיציה ×60% + BE@+2%
GREED_EARLY_BE_PCT     = 2.0  # % רווח להפעלת BE מוקדם בחמדנות
FEAR_EXTRA_SL_PCT      = 1.0  # % נוסף ל-SL בתנאי פחד

print(f"[FNG Settings] נטענו: extreme={EXTREME_FEAR_THRESHOLD} fear={FEAR_THRESHOLD} greed={GREED_THRESHOLD}", flush=True)
set_sentiment_thresholds(EXTREME_FEAR_THRESHOLD, FEAR_THRESHOLD, GREED_THRESHOLD)


def _save_fng_settings():
    """Persist runtime FNG thresholds to fng_settings.json."""
    try:
        import json as _json
        with open(FNG_SETTINGS_FILE, 'w') as _f:
            _json.dump({'extreme_fear': EXTREME_FEAR_THRESHOLD,
                        'fear':         FEAR_THRESHOLD,
                        'greed':        GREED_THRESHOLD}, _f)
    except Exception as _e:
        print(f"[FNG Settings] שגיאת שמירה: {_e}", flush=True)


# ─── Bot Config — max_trades via config.load_config() ─────────────────────────
_config_loaded = load_config()   # from config import *
print(f"[Config] נטען: max_trades={_config_loaded['max_trades']}", flush=True)


def _save_config():
    """Persist runtime MAX_TRADES to config.json."""
    try:
        import json as _json
        with open(CONFIG_FILE, 'w') as _f:
            _json.dump({'max_trades': MAX_TRADES}, _f, indent=2)
        print(f"[Config] שמור: max_trades={MAX_TRADES}", flush=True)
    except Exception as _e:
        print(f"[Config] שגיאת שמירה: {_e}", flush=True)

# ─── Daily Circuit Breaker (DAILY_LOSS_LIMIT → config.py) ─────────────────────
# DAILY_LOSS_LIMIT = -30.0 — in config.py (from config import *)
_daily_circuit_notified: bool = False  # מונע ריבוי הודעות על אותו אירוע

# sentiment_check / set_sentiment_thresholds → market_logic.py


def check_fng_state_change():
    """
    בודק אם מצב ה-FNG השתנה מאז הבדיקה הקודמת.
    שולח התראת טלגרם רק כשיש שינוי מצב בפועל.
    """
    global _extreme_fear_active
    fng_v, lbl = get_fear_greed()
    now_active = fng_v < EXTREME_FEAR_THRESHOLD

    # הפעלה ראשונה — רק מאתחל, לא שולח
    if _extreme_fear_active is None:
        _extreme_fear_active = now_active
        print(f"[FNG Monitor] מצב ראשוני: {'EXTREME FEAR' if now_active else 'NORMAL'} (FNG={fng_v})")
        return

    # אין שינוי — לא עושים כלום
    if now_active == _extreme_fear_active:
        return

    # ── שינוי מצב! ──────────────────────────────────────────────────────────────
    _extreme_fear_active = now_active

    if now_active:
        # FNG ירד מתחת לסף
        print(f"[FNG Monitor] Extreme Fear! FNG={fng_v} < {EXTREME_FEAR_THRESHOLD}")
        send_msg(
            f"⚠️ *Market Panic Detected*\n\n"
            f"📊 Fear & Greed Index: *{fng_v}* ({lbl})\n"
            f"😱 *Extreme Fear Zone*\n\n"
            f"הבוט ממשיך לנטר עסקאות פעילות קיימות כרגיל\\.\n"
            f"_סריקה תמשיך לפעול — חוקים קבועים פעילים_"
        )
    else:
        # FNG עלה מעל הסף
        print(f"[FNG Monitor] Recovered! FNG={fng_v} >= {EXTREME_FEAR_THRESHOLD}")
        send_msg(
            f"✅ *Market Sentiment Recovered*\n\n"
            f"📊 Fear & Greed Index: *{fng_v}* ({lbl})\n"
            f"🟢 *Extreme Fear Zone Exited*\n\n"
            f"סריקת שוק מלאה חזרה לפעולה\\.\n"
            f"הבוט ימשיך לחפש איתותים בסריקה הבאה\\.\n"
            f"_סריקה הבאה: עד שעה_"
        )

# ═══════════════════════════════════════════════════════════════
# Daily Circuit Breaker
# ═══════════════════════════════════════════════════════════════

def check_daily_circuit_breaker() -> bool:
    """
    מחזיר True אם הפסד יומי עבר את הסף (DAILY_LOSS_LIMIT = -$30).
    שולח התראת טלגרם פעם אחת ביום בלבד.
    מתאפס אוטומטית בחצות (daily_stats מאופס ב-manage_risk).
    """
    global _daily_circuit_notified
    today_pnl = daily_stats.get('total_pnl', 0.0)
    if today_pnl <= DAILY_LOSS_LIMIT:
        if not _daily_circuit_notified:
            print(f"⛔ DAILY CIRCUIT BREAKER: P&L={today_pnl:.2f} ≤ {DAILY_LOSS_LIMIT} — no new trades today")
            send_msg(
                f"⛔ *Daily Circuit Breaker Active*\n\n"
                f"📉 P&L היום: *${today_pnl:.2f}*\n"
                f"🛑 סף הפסד: *${DAILY_LOSS_LIMIT:.0f}* (15% מיתרת הפתיחה)\n\n"
                f"_לא ייפתחו עסקאות חדשות עד חצות\\._\n"
                f"_ניהול עסקאות פעילות נמשך כרגיל\\._"
            )
            _daily_circuit_notified = True
        return True
    if _daily_circuit_notified:
        _daily_circuit_notified = False  # שחרור אם התאושש (ניהול ידני)
    return False


# ═══════════════════════════════════════════════════════════════
# Sector Concentration Guard
# SECTOR_MAP → config.py (from config import *)
# ═══════════════════════════════════════════════════════════════


def get_sector(symbol: str) -> str:
    """מחזיר סקטור המטבע לפי SECTOR_MAP, או 'Other' אם לא ידוע."""
    base = symbol.replace('/USDT', '').replace('USDT', '').upper()
    return SECTOR_MAP.get(base, 'Other')


def check_sector_concentration(symbol: str, direction: str) -> tuple[bool, str]:
    """
    בודק אם כבר קיימת עסקה פעילה בסקטור + כיוון זהה.
    מחזיר (blocked: bool, reason: str).
    'Other' לא נחסם — מטבעות ללא מיפוי מותרים תמיד.
    """
    sector = get_sector(symbol)
    if sector == 'Other':
        return False, ""
    with trades_lock:
        same = [
            t for t in active_trades
            if t.get('direction') == direction
            and get_sector(t['symbol']) == sector
            and t['symbol'] != symbol
        ]
    if same:
        existing = same[0]['symbol']
        reason = f"Sector block: {existing} ({sector} {direction}) already open"
        return True, reason
    return False, ""


# ═══════════════════════════════════════════════════════════════
# Claude AI Risk Advisor — ADVISOR MODE (לא חוסם עסקאות)
# ═══════════════════════════════════════════════════════════════
# Claude Filter Disabled as Judge — Scoring system is the sole entry gate.
# Claude מספק הערת סיכון בלבד. ציון ≥ MIN_SCORE + BTC Regime = כניסה.

def claude_filter(symbol: str, direction: str, score: int, breakdown: str,
                  price: float, timeframe: str, btc_regime: str,
                  fng_v: int, fng_lbl: str,
                  rsi_4h: float | None = None,
                  rsi_1h: float | None = None,
                  rsi_15m: float | None = None,
                  volume_ratio: float | None = None,
                  change_24h: float | None = None,
                  daily_pnl: float = 0.0) -> tuple[bool, str]:
    """
    ADVISOR MODE: Claude אינו חוסם עסקאות. תמיד מחזיר GO=True.
    Claude מספק הערת סיכון בלבד — נרשמת בלוג ובטלגרם כ-info.
    ציון טכני ≥ MIN_SCORE + BTC Regime = החלטה הסופית.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return True, "Advisor skipped (no API key)"

    try:
        import anthropic as _anthropic

        breakdown_str = str(breakdown) if breakdown else "N/A"
        rsi_parts = []
        if rsi_4h  is not None: rsi_parts.append(f"4H={rsi_4h:.1f}")
        if rsi_1h  is not None: rsi_parts.append(f"1H={rsi_1h:.1f}")
        if rsi_15m is not None: rsi_parts.append(f"15m={rsi_15m:.1f}")
        rsi_str = "  |  ".join(rsi_parts) if rsi_parts else "N/A"
        vol_str = f"{volume_ratio:.2f}× avg" if volume_ratio is not None else "N/A"
        chg_str = f"{change_24h:+.2f}%" if change_24h is not None else "N/A"

        prompt = (
            f"You are a crypto risk ADVISOR (NOT a gatekeeper). "
            f"The trade WILL execute regardless of your opinion — the technical score already approved it. "
            f"Your job: provide ONE concise risk note for the log.\n\n"
            f"=== SIGNAL ===\n"
            f"Symbol: {symbol}  |  Direction: {direction}  |  Entry TF: {timeframe}\n"
            f"Technical Score: {score}/100 (threshold: 60) — APPROVED\n"
            f"BTC Regime: {btc_regime} — {'LONG entries enabled' if btc_regime == 'BULL' else 'SHORT entries enabled'}\n\n"
            f"=== MARKET DATA ===\n"
            f"Fear & Greed: {fng_v} ({fng_lbl}) — informational only, does NOT block trade\n"
            f"Multi-TF RSI: {rsi_str}\n"
            f"Volume vs 10-bar avg: {vol_str}\n"
            f"24h Change: {chg_str}\n"
            f"Score Breakdown: {breakdown_str}\n\n"
            f"Respond with EXACTLY one line: 'RISK NOTE: <1 concise sentence about the main risk>'"
        )

        client = _anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()
        note = raw.replace("RISK NOTE:", "").strip() if "RISK NOTE:" in raw.upper() else raw
        print(f"  [Advisor] 📝 {symbol}: {note}")
        # תמיד GO — Claude הוא יועץ בלבד
        return True, f"Advisor note: {note}"

    except Exception as e:
        print(f"  [Advisor] שגיאה: {e} — GO")
        return True, f"Advisor error (GO): {str(e)[:50]}"




# IS_DEPLOYED / GEMINI_URL / GEMINI_KEY → config.py (from config import *)

# LEVERAGE / MARGIN / POSITION_SIZE → config.py (from config import *)

# רשימה למעקב אחרי עסקאות דמו פתוחות
active_trades      = []
trades_lock        = threading.RLock()   # מגן מ-race conditions בין Threads

# לוג עסקאות סגורות (48 שעות אחרונות) לדוח ה-Drive
closed_trades_log  = []

# Audit Log — מתמיד ל-trade_audit.json (100 עסקאות אחרונות)
trade_audit_log: list[dict] = []

# Validation Trial — a clean, durable sample of the next 100 closed paper trades.
# It is intentionally separate from the rolling audit history.
validation_trial: dict = {}

# ─── Breakout SL Cooldown — מונע כניסה חוזרת אחרי SL ───────────────────────
# מבנה: { 'BNB/USDT': timestamp_of_sl_exit }  →  5 שעות המתנה
breakout_sl_cooldown: dict       = {}
BREAKOUT_SL_COOLDOWN_SEC: int    = 5 * 3600   # 5 שעות

# ─── RegimeClose Cooldown — מונע פינג-פונג אחרי סגירת RegimeClose ──────────
# מבנה: { 'SOL/USDT': timestamp_of_regime_close }  →  15 דקות המתנה
regime_close_cooldown: dict      = {}
REGIME_CLOSE_COOLDOWN_SEC: int   = 15 * 60    # 15 דקות

# ─── General Trade Close Cooldown — מונע כניסה מחדש מיידית לאחר כל סגירה ───
# FastLoss / SL / ReversalGuard → 2 שעות המתנה לאותו סימבול
# מגן מפני פתיחת עסקה שנייה אחרי setup כושל (BNB SHORT ×2 ביום)
trade_close_cooldown: dict       = {}
TRADE_CLOSE_COOLDOWN_SEC: int    = 2 * 60 * 60   # 2 שעות

# ─── MaxDuration Cooldown — cooldown ארוך אחרי יציאה בזמן (SOL loop) ────────
# MaxDuration → 2 שעות המתנה — מונע SOL loop (9 עסקאות ב-4 ימים)
max_duration_cooldown: dict      = {}
MAX_DURATION_COOLDOWN_SEC: int   = 2 * 60 * 60   # 2 שעות

# ─── Watch List — מעקב מטבעות ספציפיים כל 15 דקות ───────────────────────────
# מבנה: { 'SOL/USDT': {'direction':'LONG','added_at':..., 'last_score':0, 'expires_at':...} }
watch_list: dict = {}
watch_lock = threading.Lock()

# AUDIT_HOURS → config.py (from config import *)
_last_audit_hour   = None       # מונע כפילות באותה שעה

# מעקב אחרי עסקאות שנסגרו היום
daily_stats = {
    'wins':         0,
    'losses':       0,
    'total_pnl':    0.0,
    'date':         now_il().date(),
    'close_reasons': {'TP': 0, 'TP1+Trail': 0, 'Trailing': 0, 'SL': 0, 'BE': 0, 'Manual': 0},
}

def add_daily_pnl(amount: float):
    """מוסיף ל-daily_stats['total_pnl'] באופן אטומי (מוזן ל-Circuit Breaker — קריטי תחת ריבוי threads)."""
    with trades_lock:
        daily_stats['total_pnl'] = round(daily_stats.get('total_pnl', 0.0) + amount, 2)

# שמירת תאריך הדוח האחרון שנשלח
last_daily_report_date = None

# SCAN_REPORT_FILE → config.py (from config import *)

def send_push(title: str, body: str, tag: str = "") -> None:
    """שולח Web Push Notification לדשבורד דרך API Server (fire-and-forget)."""
    try:
        internal_secret = os.environ.get('INTERNAL_API_SECRET', '')
        if not internal_secret:
            return
        payload = {'title': title, 'body': body}
        if tag:
            payload['tag'] = tag
        requests.post(
            'http://localhost:8080/api/push/send',
            json=payload,
            headers={'X-Internal-Token': internal_secret},
            timeout=3,
        )
    except Exception as _pe:
        print(f"[Push] שגיאה (non-critical): {_pe}", flush=True)


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


def _sandbox_value_levels(df_4h, df_1h, price: float, direction: str) -> dict:
    """
    מחשב רמות כניסת ערך אידיאליות:
    - EMA200 ב-4H (תמיכה/התנגדות מרכזית)
    - Fibonacci 0.5 ו-0.618 מהתנועה האחרונה (20 נרות 1H)
    מחזיר dict שנשלח ל-Claude כהקשר.
    מחזיר {} ריק ומדפיס אזהרה אם הנתונים אינם מספיקים.
    """
    try:
        # ── EMA 200 על 4H ────────────────────────────────────────────────────
        ema_4h_series = ta.ema(df_4h['close'], length=200)
        if ema_4h_series is None or len(ema_4h_series) < 1:
            if VERBOSE_LOG:
                print(f"  [Sandbox] EMA200 4H missing — skipping value levels")
            return {}
        ema200_4h = float(ema_4h_series.iloc[-1])

        # ── EMA 200 על 1H (אם df_1h זמין) ────────────────────────────────────
        ema200_1h = None
        swing_df  = df_4h      # fallback: נשתמש ב-4H אם 1H לא זמין
        if df_1h is not None and len(df_1h) >= 20:
            ema_1h_series = ta.ema(df_1h['close'], length=200)
            if ema_1h_series is not None and len(ema_1h_series) >= 1:
                ema200_1h = float(ema_1h_series.iloc[-1])
            swing_df = df_1h   # עדיפות ל-1H לחישוב swing

        # ── Swing High/Low מ-20 הנרות האחרונים ────────────────────────────────
        recent     = swing_df.iloc[-20:]
        swing_high = float(recent['high'].max())
        swing_low  = float(recent['low'].min())
        rng        = swing_high - swing_low

        if rng <= 0 or swing_low <= 0:
            return {'ema200_4h': round(ema200_4h, 6)}

        if direction == 'LONG':
            # פולבק אחרי פאמפ — כניסה בסביבת 50%-61.8% ריטרייסמנט
            fib_500 = round(swing_high - rng * 0.500, 6)   # מדיאנה
            fib_618 = round(swing_high - rng * 0.618, 6)   # Golden ratio (עמוק יותר)
        else:  # SHORT
            # באונס אחרי דאמפ — כניסה בסביבת 50%-61.8% מהנפילה
            fib_500 = round(swing_low + rng * 0.500, 6)
            fib_618 = round(swing_low + rng * 0.618, 6)

        result = {
            'ema200_4h':  round(ema200_4h, 6),
            'fib_500':    fib_500,
            'fib_618':    fib_618,
            'swing_high': round(swing_high, 6),
            'swing_low':  round(swing_low, 6),
            'range_pct':  round(rng / swing_low * 100, 2),
        }
        if ema200_1h is not None:
            result['ema200_1h'] = round(ema200_1h, 6)
        return result

    except Exception as e:
        print(f"  [Sandbox] _sandbox_value_levels error: {e}")
        return {}


def _parse_sandbox_fields(text: str) -> dict:
    """
    מפרסר את התשובה המובנית של Claude.
    מחפש שורות ENTRY_ZONE / ENTRY_REASON / RSI_WAIT.
    """
    fields = {'entry_zone': None, 'entry_reason': None, 'rsi_wait': None}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith('ENTRY_ZONE:'):
            fields['entry_zone'] = stripped.split(':', 1)[1].strip()
        elif stripped.upper().startswith('ENTRY_REASON:'):
            fields['entry_reason'] = stripped.split(':', 1)[1].strip()
        elif stripped.upper().startswith('RSI_WAIT:'):
            fields['rsi_wait'] = stripped.split(':', 1)[1].strip()
    return fields


def claude_sandbox_analysis(bubble_watch_list: list, btc_regime: str,
                            fng_v: int, fng_lbl: str) -> list:
    """
    Sandbox Mode — ניתוח חינוכי בלבד ב-Extreme Fear.
    מנתח TOP 2 מ-Bubble Watch עם:
    - נתוני 3 TF (4H/1H/15m)
    - רמות ערך (EMA200 + Fibonacci 0.5/0.618)
    - שאלה היפותטית + הגדרת אזור כניסה אידיאלי
    לא פותח עסקאות בשום מקרה.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key or not bubble_watch_list:
        return []

    results = []
    top2 = bubble_watch_list[:2]  # Top 2 לפי שינוי מוחלט (כבר ממוין)

    for coin in top2:
        symbol    = coin['symbol']
        direction = coin['direction']
        change_24 = coin.get('change_pct', 0)

        try:
            import anthropic as _anthropic

            df_4h  = get_data(symbol, timeframe='4h',  limit=250)
            df_1h  = get_data(symbol, timeframe='1h',  limit=250)
            df_15m = get_data(symbol, timeframe='15m', limit=250)
            if df_4h is None or df_1h is None or df_15m is None:
                if VERBOSE_LOG:
                    print(f"  [Sandbox] {symbol}: data missing — skip")
                continue

            price = float(df_4h['close'].iloc[-1])

            # RSI על כל טיים-פריים
            rsi_4h  = float(ta.rsi(df_4h['close'],  length=14).iloc[-1])
            rsi_1h  = float(ta.rsi(df_1h['close'],  length=14).iloc[-1])
            rsi_15m = float(ta.rsi(df_15m['close'], length=14).iloc[-1])

            # Volume ratio ב-4H
            vol_avg   = df_4h['volume'].iloc[-12:-2].mean()
            vol_ratio = df_4h['volume'].iloc[-2] / vol_avg if vol_avg > 0 else 0.0

            # ניקוד טכני — לצורך הקשר בלבד
            score, breakdown, _atr = score_symbol(df_4h, df_1h, symbol, direction)

            # ── Value Entry Levels ────────────────────────────────────────────
            levels = _sandbox_value_levels(df_4h, df_1h, price, direction)
            ema200_4h = levels.get('ema200_4h', 'N/A')
            ema200_1h = levels.get('ema200_1h', 'N/A')
            fib_500   = levels.get('fib_500', 'N/A')
            fib_618   = levels.get('fib_618', 'N/A')
            swing_h   = levels.get('swing_high', 'N/A')
            swing_l   = levels.get('swing_low',  'N/A')
            rng_pct   = levels.get('range_pct', 'N/A')

            overextended = (
                (direction == 'LONG'  and isinstance(fib_500, float) and price > fib_500 * 1.03) or
                (direction == 'SHORT' and isinstance(fib_500, float) and price < fib_500 * 0.97)
            )

            prompt = (
                f"You are a senior crypto quant analyst. This is an EDUCATIONAL sandbox — "
                f"This is an EDUCATIONAL analysis only (Extreme Fear, FNG={fng_v}).\n\n"
                f"=== COIN ===\n"
                f"Symbol: {symbol}  |  Direction: {direction}  |  24h Change: {change_24:+.1f}%\n"
                f"Current Price: {price:.6g}\n"
                f"Price is {'OVEREXTENDED above' if overextended and direction=='LONG' else 'OVEREXTENDED below' if overextended else 'near'} the 0.5 Fib level\n\n"
                f"=== MULTI-TIMEFRAME RSI ===\n"
                f"RSI 4H: {rsi_4h:.1f}  |  RSI 1H: {rsi_1h:.1f}  |  RSI 15m: {rsi_15m:.1f}\n"
                f"Volume vs avg: {vol_ratio:.2f}×  |  Technical Score: {score}/100\n\n"
                f"=== VALUE ENTRY LEVELS (pre-calculated) ===\n"
                f"EMA 200 (4H): {ema200_4h}\n"
                f"EMA 200 (1H): {ema200_1h}\n"
                f"Recent Swing High: {swing_h}  |  Swing Low: {swing_l}  |  Range: {rng_pct}%\n"
                f"Fibonacci 0.5  retracement: {fib_500}\n"
                f"Fibonacci 0.618 retracement: {fib_618}\n\n"
                f"=== MARKET CONTEXT ===\n"
                f"BTC Regime: {btc_regime}  |  Fear & Greed: {fng_v} ({fng_lbl})\n\n"
                f"=== YOUR STRUCTURED RESPONSE ===\n"
                f"Line 1: APPROVE or REJECT (single word — would you trade this based on technicals alone?)\n"
                f"Line 2: 1-sentence analysis of the 3-TF RSI alignment and whether the move is overextended\n"
                f"Line 3: ENTRY_ZONE: [low_price] - [high_price]  "
                f"(the ideal buy/short zone using EMA200 or Fib levels; use the pre-calculated values above)\n"
                f"Line 4: ENTRY_REASON: [one phrase, e.g. 'EMA200 4H retest' or 'Fib 0.618 pullback']\n"
                f"Line 5: RSI_WAIT: [exact RSI condition before entering, e.g. 'Wait for RSI 15m to drop below 45']\n\n"
                f"Use ONLY the 5 lines above. No extra text."
            )

            client   = _anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
            raw      = response.content[0].text.strip()
            verdict  = "APPROVE ✅" if raw.upper().startswith("APPROVE") else "REJECT ❌"
            parsed   = _parse_sandbox_fields(raw)

            # שורה 2 = שורת הניתוח הכללי (לא ENTRY_ZONE / ENTRY_REASON / RSI_WAIT)
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            analysis_line = ""
            for ln in lines[1:]:
                if not any(ln.upper().startswith(k) for k in ('ENTRY_ZONE:', 'ENTRY_REASON:', 'RSI_WAIT:')):
                    analysis_line = ln
                    break

            print(f"  [Sandbox] {symbol} {direction}: {verdict.split()[0]} | "
                  f"Entry={parsed.get('entry_zone','?')} | Wait={parsed.get('rsi_wait','?')}")

            results.append({
                'symbol':       symbol,
                'direction':    direction,
                'change_24h':   round(change_24, 2),
                'price':        round(price, 6),
                'rsi_4h':       round(rsi_4h, 1),
                'rsi_1h':       round(rsi_1h, 1),
                'rsi_15m':      round(rsi_15m, 1),
                'volume_ratio': round(vol_ratio, 2),
                'score':        score,
                'verdict':      verdict,
                'analysis':     analysis_line,
                # Value Entry fields
                'ema200_4h':    ema200_4h,
                'fib_500':      fib_500,
                'fib_618':      fib_618,
                'entry_zone':   parsed.get('entry_zone'),
                'entry_reason': parsed.get('entry_reason'),
                'rsi_wait':     parsed.get('rsi_wait'),
                'overextended': overextended,
            })

        except Exception as e:
            print(f"  [Sandbox] שגיאה ב-{symbol}: {e}")

    return results


# .json and not .jsonl: the dashboard static server only serves known
# extensions, so a .jsonl request falls through to the SPA and the file
# cannot be fetched from outside the deployment. Contents stay JSONL.
REJECT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts", "bot-dashboard", "public", "rejected_log.json")
REJECT_LOG_MAX_BYTES = 8 * 1024 * 1024
REJECT_LOG_ZERO_SAMPLE = 5


def log_rejections(all_rejections: list, fng_value: int, btc_regime: str) -> None:
    """
    Append rejected candidates to data/rejected_log.jsonl.

    WHY THIS EXISTS
        gate_stats.json keeps only the last 3 decisions and the scan report keeps
        the top 5, so every candidate the bot turned down is discarded. Without
        them it is impossible to test whether the filters and the score separate
        good setups from bad ones - only whether they rank the survivors, which
        we measured on 2026-08-13 and they do not (pearson -0.037, n=94).

        Scored rejections are always kept: those are the ones that carry ranking
        information. Blanket vetoes (score 0, all identical) are sampled so the
        file stays small.

    SAFETY
        Wrapped in try/except - logging must never be able to break a scan.
    """
    try:
        if not all_rejections:
            return
        os.makedirs(os.path.dirname(REJECT_LOG_PATH), exist_ok=True)
        ts = now_il().isoformat(timespec="seconds")
        scored = [r for r in all_rejections if (r.get("best_score") or 0) > 0]
        zeros = [r for r in all_rejections if (r.get("best_score") or 0) <= 0]
        rows = []
        for r in scored + zeros[:REJECT_LOG_ZERO_SAMPLE]:
            rows.append(json.dumps({
                "ts": ts,
                "symbol": r.get("symbol"),
                "direction": r.get("direction"),
                "score": r.get("best_score") or 0,
                "reason": str(r.get("reason") or "")[:120],
                "fng": fng_value,
                "regime": btc_regime,
            }, ensure_ascii=False))
        if not rows:
            return
        with open(REJECT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        if os.path.getsize(REJECT_LOG_PATH) > REJECT_LOG_MAX_BYTES:
            with open(REJECT_LOG_PATH, encoding="utf-8") as f:
                keep = f.readlines()[-40000:]
            with open(REJECT_LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(keep)
        print(f"[RejectLog] +{len(rows)} rows ({len(scored)} scored)", flush=True)
    except Exception as _e:
        print(f"[RejectLog] skipped: {_e}", flush=True)


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
    sandbox_analysis: list = None,
):
    """שומר last_scan_results.json לאחר כל סריקה."""
    import time as _t
    duration = round(_t.time() - scan_start_ts, 1)

    # top 5 near-misses — הגבוהים ביותר שלא עברו
    near_misses = sorted(all_rejections, key=lambda x: x.get('best_score', 0), reverse=True)[:5]

    # Sentiment note — informational, no longer affects trade sizing/rules
    if fng_value < 25:
        sentiment_note = f"Fear & Greed={fng_value} (Extreme Fear) — Fixed rules apply"
    elif fng_value <= 40:
        sentiment_note = f"Fear & Greed={fng_value} (Fear) — Fixed rules apply"
    elif fng_value >= 75:
        sentiment_note = f"Fear & Greed={fng_value} (Extreme Greed) — Fixed rules apply"
    elif fng_value >= 60:
        sentiment_note = f"Fear & Greed={fng_value} (Greed) — Fixed rules apply"
    else:
        sentiment_note = f"Fear & Greed={fng_value} ({fng_label}) — Fixed rules apply"

    report = {
        'scan_time':               now_il().isoformat(timespec='seconds'),
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
        'sandbox_analysis':        sandbox_analysis or [],
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

# HEARTBEAT_INTERVAL → config.py (from config import *)
last_heartbeat_time   = None
_polling_last_activity: float = 0.0   # watchdog: updated on every Telegram update

# ═══════════════════════════════════════════════════════════════
# Flask API — live endpoints (CORS enabled via keep_alive)
# ═══════════════════════════════════════════════════════════════

@flask_app.route('/api/trades')
def api_trades():
    # current_price מתעדכן כל 60s ע"י track_trades() — ללא קריאת API כאן
    with trades_lock:
        snapshot = []
        for source in active_trades:
            trade = dict(source)
            entry = float(trade.get('entry', 0) or 0)
            current = float(trade.get('current_price', entry) or entry)
            remaining = _remaining_notional(trade)
            raw_pct = (current - entry) / entry * 100 if entry else 0.0
            direction_pct = raw_pct if trade.get('direction') == 'LONG' else -raw_pct
            floating_gross = remaining * direction_pct / 100
            accounting = trade.get('fee_accounting', {})
            realized_gross = float(accounting.get('gross_pnl_usd', 0.0) or 0.0)
            fees_paid = float(accounting.get('fees_usd', 0.0) or 0.0)
            estimated_fees = fees_paid + (remaining * TAKER_FEE_RATE if accounting else 0.0)
            trade['gross_pnl_usd'] = round(realized_gross + floating_gross, 2)
            trade['estimated_exit_fee_usd'] = round(remaining * TAKER_FEE_RATE if accounting else 0.0, 2)
            trade['estimated_fees_usd'] = round(estimated_fees, 2)
            trade['net_pnl_usd'] = round(trade['gross_pnl_usd'] - trade['estimated_fees_usd'], 2)
            trade['fee_rate_pct'] = round(TAKER_FEE_RATE * 100, 4) if accounting else 0.0
            snapshot.append(trade)
    return flask_jsonify({
        'updated': now_il().strftime('%H:%M:%S'),
        'count':   len(snapshot),
        'trades':  snapshot,
    })

@flask_app.route('/api/wallet')
def api_wallet():
    """Serve wallet snapshot. Equity always uses the immutable formula via database_manager."""
    unrealized = _unrealized_cached()
    # Post-restart fallback: if trades exist but none have a fresh current_price
    # yet, serve the last-persisted unrealized P&L from wallet.json instead of 0.
    _no_prices = active_trades and all('current_price' not in t for t in active_trades)
    if unrealized == 0 and _no_prices:
        unrealized = wallet.get('unrealized_pnl', 0.0)
    realized   = wallet.get('total_pnl', 0.0)
    locked     = round(sum(t.get('margin', MARGIN) for t in active_trades), 2)
    equity     = db.calc_equity(realized, unrealized)
    available  = db.reconcile_balance(wallet.get('balance', STARTING_BALANCE), equity, locked)
    data = dict(wallet)
    data['equity']            = equity
    data['available_balance'] = available
    data['balance']           = available
    data['locked_balance']    = locked
    data['unrealized_pnl']    = unrealized
    data['realized_pnl']      = realized
    data['fee_rate_pct']      = round(TAKER_FEE_RATE * 100, 4)
    data['gross_total_pnl']   = round(wallet.get('gross_total_pnl', realized + wallet.get('total_fees_usd', 0.0)), 2)
    data['total_fees_usd']    = round(wallet.get('total_fees_usd', 0.0), 2)
    data['starting']          = db.STARTING_BALANCE
    data['active_count']      = len(active_trades)
    return flask_jsonify(data)

@flask_app.route('/api/hot')
def api_hot():
    try:
        with open(HOT_CANDIDATES_FILE, 'r') as f:
            return flask_jsonify(json.load(f))
    except Exception:
        return flask_jsonify({'updated': '—', 'count': 0, 'candidates': []})

@flask_app.route('/api/trade_audit')
def api_trade_audit():
    # Serve from in-memory trade_audit_log (loaded from Replit DB on startup
    # and kept current by _save_audit_log on every close). Disk file is just
    # a cache for the api-server fallback layer — never read live from here.
    return flask_jsonify({
        'updated': now_il().isoformat(timespec='seconds'),
        'count':   len(trade_audit_log),
        'trades':  list(trade_audit_log),
    })


@flask_app.route('/api/validation_trial')
def api_validation_trial():
    """Serve the dedicated, chronological 100-trade validation sample."""
    data = dict(validation_trial)
    data['summary'] = _validation_summary(data.get('trades', []))
    data['remaining'] = max(0, data.get('target', VALIDATION_TRIAL_TARGET) - len(data.get('trades', [])))
    return flask_jsonify(data)


@flask_app.route('/api/validation_trial/reset', methods=['POST'])
def api_validation_trial_reset():
    if not _require_internal_token():
        return flask_jsonify({'ok': False, 'error': 'Forbidden'}), 403
    trial = _reset_validation_trial()
    return flask_jsonify({'ok': True, **trial})


@flask_app.route('/api/active_trades')
def api_active_trades():
    """Alias for /api/trades — used by the dashboard in production."""
    return api_trades()

@flask_app.route('/api/status')
def api_status():
    """Aggregate status snapshot. Equity always uses the immutable formula via database_manager."""
    from keep_alive import _fng_ka
    fng_v   = _fng_ka.get('value') or 50
    fng_lbl = _fng_ka.get('label') or 'Neutral'
    realized   = wallet.get('total_pnl', 0.0)
    unrealized = _unrealized_cached()
    return flask_jsonify({
        'connected':       True,
        'exchange':        'Bitget VIRTUAL',
        'mode':            'VIRTUAL',
        'starting':        db.STARTING_BALANCE,
        'realized':        realized,
        'unrealized':      unrealized,
        'equity':          db.calc_equity(realized, unrealized),
        'available':       round(wallet.get('balance', db.STARTING_BALANCE), 2),
        'locked':          round(sum(t.get('margin', MARGIN) for t in active_trades), 2),
        'fng_value':       fng_v,
        'fng_label':       fng_lbl,
        'active_trades':   len(active_trades),
        'max_trades':      MAX_TRADES,
        'btc_price':       wallet.get('btc_price', 0),
        'ts':              int(now_il().timestamp() * 1000),
        'updated':         now_il().strftime('%H:%M:%S'),
    })

def _require_internal_token() -> bool:
    """Return True if the request carries the correct internal API token.
    Express always adds X-Internal-Token when proxying to Flask.
    If INTERNAL_API_SECRET is not configured, all requests are rejected (fail closed).
    """
    secret = os.environ.get('INTERNAL_API_SECRET', '')
    if not secret:
        return False
    return flask_request.headers.get('X-Internal-Token', '') == secret

@flask_app.route('/api/sync', methods=['POST'])
def api_sync():
    """Dashboard SYNC button — שולח /status לטלגרם.
    NOTE: handle_status runs in a background thread to avoid blocking the Flask server.
    _get_unrealized_pnl() is called INSIDE the thread, not in the request handler.
    """
    if not _require_internal_token():
        return flask_jsonify({'ok': False, 'error': 'Forbidden'}), 403
    def _do_sync():
        try:
            _get_unrealized_pnl()   # רענון מחירים — בתוך thread נפרד, לא חוסם Flask
        except Exception:
            pass
        class _DummyMsg:
            text = '/status'
        handle_status(_DummyMsg())

    import threading as _th
    _th.Thread(target=_do_sync, daemon=True).start()
    n = len(active_trades)
    return flask_jsonify({'ok': True, 'trades': n, 'sent': True})


@flask_app.route('/api/fng_settings', methods=['GET'])
def api_fng_settings_get():
    return flask_jsonify({
        'extreme_fear': EXTREME_FEAR_THRESHOLD,
        'fear':         FEAR_THRESHOLD,
        'greed':        GREED_THRESHOLD,
        'ranges': {
            'extreme_fear': {'min': 5,  'max': 25, 'desc': 'Extreme Fear — חוקים קבועים פעילים'},
            'fear':         {'min': 15, 'max': 45, 'desc': 'Fear — RSI<30 ל-LONG + SL+1%'},
            'greed':        {'min': 55, 'max': 85, 'desc': 'Greed — פוזיציה 60% + BE מוקדם'},
        }
    })

# ─── Make.com Incoming Webhook ────────────────────────────────────────────────
# MAKE_INCOMING_SECRET loaded from env via config.py (from config import *)

@flask_app.route('/api/make', methods=['POST'])
def api_make_command():
    """
    מקבל פקודות / ניתוח מ-Make.com Agent.

    גוף JSON נדרש:
      { "secret": "<MAKE_WEBHOOK_SECRET env var>", "command": "<cmd>", ... }

    פקודות נתמכות:
      news_alert   — { symbol, headline, sentiment }
                     שולח התראה לטלגרם + מהדק SL ב-1% לאותו מטבע
      whale_alert  — { symbol, direction, amount_usd }
                     שולח התראה לטלגרם בלבד
      analysis     — { text }
                     שולח הודעה חופשית לטלגרם
      close_trade  — { symbol }
                     סוגר עסקה פעילה ידנית
      tighten_sl   — { symbol, new_sl_pct }
                     מהדק SL לאחוז מהכניסה שנשלח
    """
    data = flask_request.get_json(force=True, silent=True) or {}

    # ── אימות ──
    if data.get('secret') != MAKE_INCOMING_SECRET:
        return flask_jsonify({'ok': False, 'error': 'unauthorized'}), 401

    command = data.get('command', '').lower().strip()
    symbol_raw = data.get('symbol', '').upper().replace('USDT', '').strip()
    symbol = f"{symbol_raw}/USDT" if symbol_raw and '/USDT' not in symbol_raw else symbol_raw

    # ── news_alert ──
    if command == 'news_alert':
        headline  = data.get('headline', 'חדשות חדשות')
        sentiment = data.get('sentiment', 'neutral').lower()
        sent_icon = '🔴' if 'neg' in sentiment else ('🟢' if 'pos' in sentiment else '⚪')
        msg = (
            f"📰 *חדשות מ-Make | {symbol_raw or 'שוק'}*\n"
            f"{sent_icon} סנטימנט: *{sentiment}*\n"
            f"_{headline}_"
        )
        send_msg(msg)
        print(f"[Make→Bot] news_alert: {symbol_raw} | {sentiment} | {headline[:60]}")
        return flask_jsonify({'ok': True, 'action': 'alert_sent'})

    # ── whale_alert ──
    elif command == 'whale_alert':
        direction  = data.get('direction', '').upper()
        amount_usd = data.get('amount_usd', 0)
        dir_icon   = '🐳🟢' if direction == 'BUY' else ('🐳🔴' if direction == 'SELL' else '🐳')
        msg = (
            f"{dir_icon} *ווייתן זוהה — {symbol_raw}*\n"
            f"כיוון: *{direction}* | סכום: `${amount_usd:,.0f}`\n"
            f"_עדכון מ-Make Whale Tracker_"
        )
        send_msg(msg)
        print(f"[Make→Bot] whale_alert: {symbol_raw} {direction} ${amount_usd:,.0f}")
        return flask_jsonify({'ok': True, 'action': 'alert_sent'})

    # ── analysis (הודעה חופשית) ──
    elif command == 'analysis':
        text = data.get('text', '')
        if text:
            send_msg(f"🤖 *Make AI Analysis*\n\n{text}")
        print(f"[Make→Bot] analysis: {text[:80]}")
        return flask_jsonify({'ok': True, 'action': 'message_sent'})

    # ── close_trade ──
    elif command == 'close_trade':
        with trades_lock:
            trade = next((t for t in active_trades if t['symbol'] == symbol), None)
        if not trade:
            return flask_jsonify({'ok': False, 'error': f'{symbol} לא נמצא בעסקאות פעילות'})
        try:
            price      = exchange.fetch_ticker(symbol)['last']
            entry      = trade['entry']
            _ps        = _remaining_notional(trade)
            direction  = trade.get('direction', 'LONG')
            raw_pct    = (price - entry) / entry * 100
            pnl_pct    = raw_pct if direction == 'LONG' else -raw_pct
            gross_pnl  = round(_ps * pnl_pct / 100, 2)
            with trades_lock:
                if trade in active_trades:
                    active_trades.remove(trade)
            wallet_credit(gross_pnl, trade.get('margin', MARGIN))
            record = _log_closed_trade(trade, 'Manual', gross_pnl, price)
            save_active_trades()
            send_msg(f"✋ *Make סגר עסקה* — `{symbol}` @ `{price:.6g}` | Net: `{record['net_pnl_usd']:+.2f}$` (fees `${record['fees_usd']:.2f}`)")
            print(f"[Make→Bot] close_trade: {symbol} @ {price} net={record['net_pnl_usd']:+.2f}")
            return flask_jsonify({'ok': True, 'action': 'trade_closed', 'price': price, 'pnl': record['net_pnl_usd']})
        except Exception as e:
            return flask_jsonify({'ok': False, 'error': str(e)}), 500

    # ── tighten_sl ──
    elif command == 'tighten_sl':
        new_sl_pct = float(data.get('new_sl_pct', 0))
        if new_sl_pct <= 0:
            return flask_jsonify({'ok': False, 'error': 'new_sl_pct חייב להיות > 0'})
        with trades_lock:
            trade = next((t for t in active_trades if t['symbol'] == symbol), None)
        if not trade:
            return flask_jsonify({'ok': False, 'error': f'{symbol} לא נמצא'})
        entry     = trade['entry']
        direction = trade.get('direction', 'LONG')
        new_sl    = round(entry * (1 - new_sl_pct / 100) if direction == 'LONG'
                          else entry * (1 + new_sl_pct / 100), 8)
        old_sl    = trade.get('sl', 0)
        trade['sl'] = new_sl
        save_active_trades()
        send_msg(
            f"🔧 *Make הידק SL — {symbol}*\n"
            f"SL ישן: `{old_sl:.6g}` → SL חדש: `{new_sl:.6g}` (-{new_sl_pct}%)"
        )
        print(f"[Make→Bot] tighten_sl: {symbol} {old_sl:.6g} → {new_sl:.6g}")
        return flask_jsonify({'ok': True, 'action': 'sl_updated',
                              'old_sl': old_sl, 'new_sl': new_sl})

    else:
        return flask_jsonify({'ok': False, 'error': f'פקודה לא מוכרת: {command}'}), 400


@flask_app.route('/api/fng_settings', methods=['POST'])
def api_fng_settings_post():
    if not _require_internal_token():
        return flask_jsonify({'ok': False, 'error': 'Forbidden'}), 403
    global EXTREME_FEAR_THRESHOLD, FEAR_THRESHOLD, GREED_THRESHOLD
    data = flask_request.get_json(force=True, silent=True) or {}
    errors = []

    if 'extreme_fear' in data:
        v = int(data['extreme_fear'])
        if 5 <= v <= 25:
            EXTREME_FEAR_THRESHOLD = v
        else:
            errors.append(f'extreme_fear חייב להיות 5–25 (קיבלתי {v})')

    if 'fear' in data:
        v = int(data['fear'])
        if 15 <= v <= 45:
            FEAR_THRESHOLD = v
        else:
            errors.append(f'fear חייב להיות 15–45 (קיבלתי {v})')

    if 'greed' in data:
        v = int(data['greed'])
        if 55 <= v <= 85:
            GREED_THRESHOLD = v
        else:
            errors.append(f'greed חייב להיות 55–85 (קיבלתי {v})')

    if errors:
        return flask_jsonify({'ok': False, 'errors': errors}), 400

    _save_fng_settings()
    set_sentiment_thresholds(EXTREME_FEAR_THRESHOLD, FEAR_THRESHOLD, GREED_THRESHOLD)
    print(f"[FNG Settings] שמורים: extreme={EXTREME_FEAR_THRESHOLD} fear={FEAR_THRESHOLD} greed={GREED_THRESHOLD}", flush=True)
    return flask_jsonify({
        'ok': True,
        'extreme_fear': EXTREME_FEAR_THRESHOLD,
        'fear':         FEAR_THRESHOLD,
        'greed':        GREED_THRESHOLD,
    })

@flask_app.route('/api/close', methods=['POST'])
def api_close_trade():
    """סגירה ידנית מהירה — POST {"symbol":"BNB"} ללא אימות (internal only)."""
    data   = flask_request.get_json(force=True, silent=True) or {}
    raw    = data.get('symbol', '').upper().replace('USDT', '').strip()
    symbol = f"{raw}/USDT" if raw and '/USDT' not in raw else raw
    if not symbol:
        return flask_jsonify({'ok': False, 'error': 'symbol required'}), 400
    with trades_lock:
        trade = next((t for t in active_trades if t['symbol'] == symbol), None)
    if not trade:
        return flask_jsonify({'ok': False, 'error': f'{symbol} לא נמצא'}), 404
    try:
        price     = exchange.fetch_ticker(symbol)['last']
        entry     = trade['entry']
        _ps       = _remaining_notional(trade)
        direction = trade.get('direction', 'LONG')
        raw_pct   = (price - entry) / entry * 100
        pnl_pct   = raw_pct if direction == 'LONG' else -raw_pct
        gross_pnl = round(_ps * pnl_pct / 100, 2)
        with trades_lock:
            if trade in active_trades:
                active_trades.remove(trade)
        wallet_credit(gross_pnl, trade.get('margin', MARGIN))
        record = _log_closed_trade(trade, 'Manual', gross_pnl, price)
        save_active_trades()
        send_msg(f"🚪 *סגירה ידנית* — `{symbol}` @ `{price:.6g}`\nNet: `{record['net_pnl_usd']:+.2f}$` | fees: `${record['fees_usd']:.2f}`")
        print(f"[API/close] {symbol} @ {price} dir={direction} net={record['net_pnl_usd']:+.2f}", flush=True)
        return flask_jsonify({'ok': True, 'symbol': symbol, 'price': price, 'pnl': record['net_pnl_usd']})
    except Exception as e:
        return flask_jsonify({'ok': False, 'error': str(e)}), 500

@flask_app.route('/api/tg_hook', methods=['POST'])
def api_tg_hook():
    """Telegram webhook endpoint — used in production instead of polling."""
    if _TG_WEBHOOK_SECRET:
        incoming = flask_request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if incoming != _TG_WEBHOOK_SECRET:
            print(f"[WEBHOOK] Rejected request with invalid secret token", flush=True)
            return flask_jsonify({'ok': False, 'error': 'Forbidden'}), 403
    try:
        json_string = flask_request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return flask_jsonify({'ok': True})
    except Exception as e:
        print(f"[WEBHOOK] Error processing update: {e}", flush=True)
        return flask_jsonify({'ok': False}), 200  # always 200 so Telegram doesn't retry

@flask_app.route('/api/slots', methods=['GET'])
def api_slots_get():
    n_open = len(active_trades)
    return flask_jsonify({
        'max_trades':   MAX_TRADES,
        'active_trades': n_open,
        'open_slots':   max(0, MAX_TRADES - n_open),
        'min': 1,
        'max': 5,
    })

@flask_app.route('/api/slots', methods=['POST'])
def api_slots_post():
    if not _require_internal_token():
        return flask_jsonify({'ok': False, 'error': 'Forbidden'}), 403
    global MAX_TRADES
    data = flask_request.get_json(force=True, silent=True) or {}
    try:
        v = int(data.get('max_trades', MAX_TRADES))
    except (ValueError, TypeError):
        return flask_jsonify({'ok': False, 'error': 'max_trades חייב להיות מספר שלם 0–5'}), 400
    if not (0 <= v <= 5):
        return flask_jsonify({'ok': False, 'error': f'max_trades חייב להיות 0–5 (קיבלתי {v})'}), 400
    old      = MAX_TRADES
    MAX_TRADES = v
    _save_config()
    n_open   = len(active_trades)
    if v < n_open:
        note = f"⚠️ {n_open} עסקאות פתוחות — לא נסגרות. עסקאות חדשות יפתחו רק לאחר ירידה ל-{v}"
        print(f"[Slots] {note}", flush=True)
    else:
        print(f"[Slots] ✅ max_trades שונה: {old}→{v}", flush=True)
    return flask_jsonify({
        'ok': True,
        'max_trades':    MAX_TRADES,
        'active_trades': n_open,
        'open_slots':    max(0, MAX_TRADES - n_open),
    })

def send_msg(text, force: bool = False):
    """שולח הודעת Telegram. force=True עוקף את מצב ה-pause (לאישורי /stop ו-/resume בלבד)."""
    if _bot_paused and not force:
        return
    try:
        bot.send_message(CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        err = str(e)
        print(f"Telegram send_msg Error: {err[:120]}")
        # Fallback: שלח ללא Markdown אם יש שגיאת parse
        if "parse" in err.lower() or "can't parse" in err.lower() or "Bad Request" in err:
            try:
                plain = text.replace('*', '').replace('_', '').replace('`', '').replace('\\', '')
                bot.send_message(CHAT_ID, plain)
            except Exception as e2:
                print(f"Telegram fallback Error: {e2}")


# ═══════════════════════════════════════════════════════════════
# ארנק וירטואלי — Virtual Wallet ($200 starting)
# ═══════════════════════════════════════════════════════════════

wallet: dict = {}

def _get_unrealized_pnl() -> float:
    """Floating P&L של כל העסקאות הפתוחות — מחיר חי מ-Bitget API."""
    if not active_trades:
        return 0.0
    total = 0.0
    # ניסיון לקבל מחירים חיים לכל הסימבולים בפעם אחת (batch)
    live_prices: dict[str, float] = {}
    try:
        symbols = list({t['symbol'] for t in active_trades})
        for sym in symbols:
            ticker = exchange.fetch_ticker(sym)
            live_prices[sym] = float(ticker['last'])
    except Exception:
        pass  # אם נכשל — נחזור ל-current_price שמור

    for t in active_trades:
        sym   = t.get('symbol', '')
        curr  = live_prices.get(sym) or t.get('current_price', t.get('entry', 0))
        entry = t.get('entry', 0)
        pos   = t.get('pos_size', POSITION_SIZE)
        if entry <= 0:
            continue
        # עדכן current_price בתוך ה-trade לסינכרון עם הדשבורד
        if sym in live_prices:
            t['current_price'] = live_prices[sym]
        # אחרי TP1 — רק 25% נשארו פתוחים; tp1_pnl כבר נרשם ב-wallet.total_pnl
        active_pos = pos * 0.25 if t.get('tp1_triggered') else pos
        if t.get('direction') == 'LONG':
            total += (curr - entry) / entry * active_pos
        else:
            total += (entry - curr) / entry * active_pos
    return round(total, 2)

def _get_equity():
    """Total equity = STARTING_BALANCE + realized_pnl + unrealized_pnl.
    Uses database_manager.calc_equity — the single immutable formula."""
    unrealized = _get_unrealized_pnl()
    realized   = wallet.get('total_pnl', 0.0)
    return db.calc_equity(realized, unrealized)

def _unrealized_cached() -> float:
    """P&L מ-current_price ששמור בתוך כל trade — ללא קריאת API.
    track_trades() מעדכן current_price כל 60 שניות — מספיק לדשבורד."""
    total = 0.0
    for t in active_trades:
        curr  = t.get('current_price', t.get('entry', 0))
        entry = t.get('entry', 0)
        pos   = t.get('pos_size', POSITION_SIZE)
        if entry <= 0:
            continue
        # אחרי TP1 — רק 25% נשארו פתוחים; tp1_pnl כבר נרשם ב-wallet.total_pnl
        active_pos = pos * 0.25 if t.get('tp1_triggered') else pos
        if t.get('direction') == 'LONG':
            total += (curr - entry) / entry * active_pos
        else:
            total += (entry - curr) / entry * active_pos
    return round(total, 2)


def _trial_default() -> dict:
    started_at = now_il().isoformat(timespec='seconds')
    return {
        'target': VALIDATION_TRIAL_TARGET,
        'status': 'active',
        'started_at': started_at,
        'id': f"{started_at}-{time.time_ns()}",
        'completed_at': None,
        'trades': [],
    }


def _validation_summary(trades: list[dict]) -> dict:
    def _bucket(rows: list[dict]) -> dict:
        net = round(sum(float(t.get('net_pnl_usd', t.get('pnl_usd', 0.0)) or 0.0) for t in rows), 2)
        gross = round(sum(float(t.get('gross_pnl_usd', t.get('pnl_usd', 0.0)) or 0.0) for t in rows), 2)
        fees = round(sum(float(t.get('fees_usd', 0.0) or 0.0) for t in rows), 2)
        wins = sum(1 for t in rows if float(t.get('net_pnl_usd', t.get('pnl_usd', 0.0)) or 0.0) > 0)
        return {
            'trades': len(rows),
            'wins': wins,
            'losses': len(rows) - wins,
            'win_rate': round(wins / len(rows) * 100, 1) if rows else 0.0,
            'net_win_rate': round(wins / len(rows) * 100, 1) if rows else 0.0,
            'gross_pnl_usd': gross,
            'fees_usd': fees,
            'net_pnl_usd': net,
            'total_gross_pnl_usd': gross,
            'total_fees_usd': fees,
            'total_net_pnl_usd': net,
        }

    def _group(key: str) -> dict:
        groups: dict[str, list[dict]] = {}
        for row in trades:
            label = str(row.get(key) or 'Unknown')
            groups.setdefault(label, []).append(row)
        return {label: _bucket(rows) for label, rows in groups.items()}

    return {
        **_bucket(trades),
        'by_direction': _group('direction'),
        'by_strategy': _group('track'),
        'by_regime': _group('market_regime'),
    }


def _save_validation_trial():
    validation_trial['summary'] = _validation_summary(validation_trial.get('trades', []))
    validation_trial['remaining'] = max(0, validation_trial.get('target', VALIDATION_TRIAL_TARGET) - len(validation_trial.get('trades', [])))
    state_store.save_state('validation_trial', validation_trial, VALIDATION_TRIAL_FILE)


def _load_validation_trial():
    global validation_trial
    loaded = state_store.load_state('validation_trial', VALIDATION_TRIAL_FILE, _trial_default())
    validation_trial = loaded if isinstance(loaded, dict) else _trial_default()
    validation_trial.setdefault('target', VALIDATION_TRIAL_TARGET)
    validation_trial.setdefault('status', 'active')
    validation_trial.setdefault('id', validation_trial.get('started_at', now_il().isoformat(timespec='seconds')))
    validation_trial.setdefault('trades', [])
    _save_validation_trial()
    print(f"[ValidationTrial] loaded: {len(validation_trial['trades'])}/{validation_trial['target']} trades", flush=True)


def _reset_validation_trial() -> dict:
    global validation_trial
    validation_trial = _trial_default()
    state_store.reset_state('validation_trial', validation_trial, VALIDATION_TRIAL_FILE)
    _save_validation_trial()
    return validation_trial

def _equity_cached() -> float:
    """Equity מהיר מ-cache — ללא קריאת API.
    Formula (immutable, from database_manager):
        Equity = STARTING_BALANCE + realized_pnl + floating_pnl
    Falls back to last persisted equity during the post-restart window
    (when trades exist but current_price hasn't been refreshed yet)."""
    unrealized = _unrealized_cached()
    realized   = wallet.get('total_pnl', 0.0)
    # Post-restart window: active trades exist but current_price not yet refreshed
    if unrealized == 0 and active_trades and all('current_price' not in t for t in active_trades):
        # Use last-persisted equity until the price-tracking thread catches up (~60s)
        return wallet.get('equity', db.calc_equity(realized, 0.0))
    return db.calc_equity(realized, unrealized)

def _append_equity_point():
    hist = wallet.setdefault('equity_history', [])
    hist.append({'t': now_il().strftime('%m/%d %H:%M'), 'eq': _get_equity()})
    if len(hist) > 120:          # שמירת 120 נקודות (≈10 ימים בסריקה שעתית)
        wallet['equity_history'] = hist[-120:]

def load_wallet():
    global wallet
    default = {
        'balance':        STARTING_BALANCE,
        'starting':       STARTING_BALANCE,
        'total_pnl':      0.0,
        'gross_total_pnl': 0.0,
        'total_fees_usd': 0.0,
        'trades_opened':  0,
        'equity_history': [{'t': now_il().strftime('%m/%d %H:%M'), 'eq': STARTING_BALANCE}],
    }
    wallet = state_store.load_state('wallet', WALLET_FILE, default)
    # Reconcile on load: if no open trades, balance MUST equal equity
    locked = round(sum(t.get('margin', MARGIN) for t in active_trades), 2)
    if locked == 0:
        wallet['unrealized_pnl'] = 0.0
        equity = db.calc_equity(wallet.get('total_pnl', 0.0), 0.0)
        corrected = db.reconcile_balance(wallet.get('balance', STARTING_BALANCE), equity, 0.0)
        if corrected != wallet.get('balance'):
            print(f"[RECONCILE] balance drift fixed: ${wallet.get('balance', 0):.2f} → ${corrected:.2f}", flush=True)
            wallet['balance'] = corrected
            wallet['available_balance'] = corrected
            save_wallet()   # persist corrected values to OS immediately
    print(f"Wallet loaded: balance=${wallet.get('balance', 0):.2f} equity=${_get_equity():.2f}", flush=True)
    return wallet

def load_active_trades():
    """טוען עסקאות פעילות מ-Replit DB / קובץ JSON לאחר הפעלה מחדש של הבוט."""
    global active_trades
    data = state_store.load_state(
        'active_trades',
        ACTIVE_TRADES_FILE,
        {'updated': None, 'count': 0, 'trades': []},
    )
    loaded = data.get('trades', []) if isinstance(data, dict) else []
    if loaded:
        with trades_lock:
            active_trades = loaded
        print(f"Active trades loaded: {len(loaded)} trade(s) restored", flush=True)
    else:
        print("Active trades loaded: none on record", flush=True)

def save_wallet():
    """Persist wallet. Equity and balance are ALWAYS recomputed using immutable rules."""
    try:
        realized   = wallet.get('total_pnl', 0.0)
        # If no active trades, unrealized MUST be 0 — stale values cause equity drift
        unrealized = 0.0 if not active_trades else wallet.get('unrealized_pnl', 0.0)
        locked     = round(sum(t.get('margin', MARGIN) for t in active_trades), 2)
        equity     = db.calc_equity(realized, unrealized)
        # Reconcile: when no trades are open, available balance MUST equal equity
        balance    = db.reconcile_balance(wallet.get('balance', STARTING_BALANCE), equity, locked)
        snapshot = {
            **wallet,
            'starting':          db.STARTING_BALANCE,
            'balance':           balance,
            'locked_balance':    locked,
            'available_balance': balance,
            'unrealized_pnl':    round(unrealized, 2),
            # Equity recomputed every save — never trusted from in-memory value
            'equity':            equity,
        }
        state_store.save_state('wallet', snapshot, WALLET_FILE)
    except Exception as e:
        print(f"Wallet save error: {e}", flush=True)

def wallet_deduct(amount: float = MARGIN):
    """קיזוז מרג'ין בפתיחת עסקה. amount=MARGIN (ברירת מחדל $20). אטומי תחת trades_lock."""
    with trades_lock:
        wallet['balance']       = round(wallet.get('balance', STARTING_BALANCE) - amount, 2)
        wallet['trades_opened'] = wallet.get('trades_opened', 0) + 1
        _append_equity_point()
        save_wallet()


def wallet_credit(pnl_usd: float, amount: float = MARGIN, count_result: bool = True):
    """זיכוי מרג'ין + P&L בסגירת עסקה. amount צריך להתאים ל-wallet_deduct. אטומי תחת trades_lock."""
    with trades_lock:
        wallet['balance']   = round(wallet.get('balance', STARTING_BALANCE) + amount + pnl_usd, 2)
        wallet['total_pnl'] = round(wallet.get('total_pnl', 0.0) + pnl_usd, 2)
        wallet['gross_total_pnl'] = round(wallet.get('gross_total_pnl', 0.0) + pnl_usd, 2)
        if count_result and pnl_usd >= 0:
            wallet['total_wins']   = wallet.get('total_wins', 0) + 1
        elif count_result:
            wallet['total_losses'] = wallet.get('total_losses', 0) + 1
        _append_equity_point()
        save_wallet()


def _charge_estimated_fee(fee_usd: float):
    """Deduct an estimated paper-trading fee without treating it as a separate trade."""
    fee = round(max(0.0, float(fee_usd)), 4)
    if fee == 0:
        return
    with trades_lock:
        wallet['balance'] = round(wallet.get('balance', STARTING_BALANCE) - fee, 2)
        wallet['total_pnl'] = round(wallet.get('total_pnl', 0.0) - fee, 2)
        wallet['total_fees_usd'] = round(wallet.get('total_fees_usd', 0.0) + fee, 4)
        _append_equity_point()
        save_wallet()
    add_daily_pnl(-fee)


def _ensure_fee_accounting(trade: dict, charge_entry_fee: bool = False) -> dict:
    """Return a trade's fee ledger; existing positions remain legacy fee-free."""
    accounting = trade.get('fee_accounting')
    if isinstance(accounting, dict):
        return accounting

    notional = float(trade.get('pos_size', POSITION_SIZE) or POSITION_SIZE)
    entry_fee = round(notional * TAKER_FEE_RATE, 4) if charge_entry_fee else 0.0
    accounting = {
        'fee_rate': TAKER_FEE_RATE,
        'entry_fee_usd': entry_fee,
        'exit_fee_usd': 0.0,
        'fees_usd': entry_fee,
        'gross_pnl_usd': 0.0,
    }
    trade['fee_accounting'] = accounting
    trade['entry_fee_usd'] = entry_fee
    trade['fees_paid_usd'] = entry_fee
    trade['gross_pnl_usd'] = 0.0
    trade['net_pnl_usd'] = round(-entry_fee, 4)
    trade['fee_rate_pct'] = round(TAKER_FEE_RATE * 100, 4)
    return accounting


def _record_exit_leg(trade: dict, gross_pnl_usd: float, closed_notional: float) -> float:
    """Record one filled exit leg and charge exactly one estimated taker fee for it."""
    accounting = _ensure_fee_accounting(trade)
    fee = round(max(0.0, float(closed_notional)) * TAKER_FEE_RATE, 4)
    accounting['gross_pnl_usd'] = round(accounting.get('gross_pnl_usd', 0.0) + gross_pnl_usd, 4)
    accounting['exit_fee_usd'] = round(accounting.get('exit_fee_usd', 0.0) + fee, 4)
    accounting['fees_usd'] = round(accounting.get('fees_usd', 0.0) + fee, 4)
    trade['gross_pnl_usd'] = accounting['gross_pnl_usd']
    trade['fees_paid_usd'] = accounting['fees_usd']
    trade['net_pnl_usd'] = round(accounting['gross_pnl_usd'] - accounting['fees_usd'], 4)
    _charge_estimated_fee(fee)
    return fee


def _remaining_notional(trade: dict) -> float:
    """Actual still-open notional used for the final exit fee."""
    position = float(trade.get('pos_size', POSITION_SIZE) or POSITION_SIZE)
    return position * 0.25 if trade.get('phase') == 'trailing' else position


# ─────────────────────────────────────────────────────────────────
#  Make.com Webhook — שליחת עדכון לכל פתיחת עסקה
# ─────────────────────────────────────────────────────────────────
MAKE_WEBHOOK_URL = "https://hook.eu1.make.com/14l99wxa67quwoh9q9n042238mjsuars"

def _fire_make_webhook(trade: dict):
    """שולח POST ל-Make webhook בthread נפרד (fire-and-forget)."""
    import threading as _threading
    import requests as _req

    def _send():
        try:
            payload = {
                "symbol":      trade.get('symbol', '').replace('/USDT', ''),
                "direction":   trade.get('direction', 'LONG'),
                "entry_price": trade.get('entry', 0),
                "timestamp":   now_il().isoformat(timespec='seconds'),
                "strategy":    trade.get('strategy', 'Swing'),
                "score":       trade.get('score', 0),
                "sl":          trade.get('sl'),
                "tp":          trade.get('tp'),
            }
            resp = _req.post(MAKE_WEBHOOK_URL, json=payload, timeout=8)
            print(f"[Make] Webhook ✅ {payload['symbol']} {payload['direction']} → {resp.status_code}")
        except Exception as _e:
            print(f"[Make] Webhook ❌ {_e}")

    _threading.Thread(target=_send, daemon=True).start()


# ─────────────────────────────────────────────────────────────────
#  place_order() — פונקציה מאוחדת לרישום עסקה
#  כל נתיבי הפתיחה (Auto / Manual / Scalp / SOL) מדווחים דרכה.
# ─────────────────────────────────────────────────────────────────
def _entry_restraint_reason(trade: dict) -> str | None:
    """Returns a reason when recent results require pausing this entry."""
    symbol = trade.get('symbol', '')
    track = trade.get('track') or trade.get('strategy') or 'Swing'
    now_ts = now_il().timestamp()

    # Persistent per-symbol cooldown derived from the audit ledger, so a bot
    # restart cannot accidentally clear the restraint.
    symbol_closes = [
        r for r in trade_audit_log
        if r.get('symbol') == symbol and r.get('closed_at')
    ]
    if symbol_closes:
        latest = max(symbol_closes, key=lambda r: r.get('closed_at', ''))
        try:
            age_h = (now_ts - datetime.fromisoformat(latest['closed_at']).timestamp()) / 3600
            latest_net = float(latest.get('net_pnl_usd', latest.get('pnl_usd', 0)) or 0)
            wait_h = SYMBOL_LOSS_REENTRY_HOURS if latest_net < 0 else SYMBOL_REENTRY_HOURS
            if age_h < wait_h:
                remaining = max(1, math.ceil(wait_h - age_h))
                outcome = 'הפסד' if latest_net < 0 else 'סגירה'
                return f'{symbol} בקולדאון עוד כ-{remaining} שעות אחרי {outcome}'
        except (TypeError, ValueError):
            pass

    # Swing-only circuit breaker: three consecutive losing Swing closes pause
    # that track for two hours while Breakout remains available.
    if track == 'Swing':
        swing_closes = [
            r for r in trade_audit_log
            if (r.get('track') or r.get('strategy') or 'Swing') == 'Swing'
            and r.get('closed_at')
        ]
        swing_closes.sort(key=lambda r: r.get('closed_at', ''), reverse=True)
        recent = swing_closes[:SWING_LOSS_STREAK_LIMIT]
        if len(recent) == SWING_LOSS_STREAK_LIMIT and all(
            float(r.get('net_pnl_usd', r.get('pnl_usd', 0)) or 0) < 0
            for r in recent
        ):
            try:
                age_h = (now_ts - datetime.fromisoformat(recent[0]['closed_at']).timestamp()) / 3600
                if age_h < SWING_LOSS_PAUSE_HOURS:
                    remaining_min = max(1, math.ceil((SWING_LOSS_PAUSE_HOURS - age_h) * 60))
                    return (
                        f'Swing מושהה לעוד {remaining_min} דקות אחרי '
                        f'{SWING_LOSS_STREAK_LIMIT} הפסדים רצופים'
                    )
            except (TypeError, ValueError):
                pass

    return None


def _entry_spread_reason(symbol: str) -> str | None:
    """Rejects a market whose live bid/ask spread is too wide."""
    try:
        ticker = exchange.fetch_ticker(symbol)
        bid = float(ticker.get('bid') or 0)
        ask = float(ticker.get('ask') or 0)
        midpoint = (bid + ask) / 2
        if bid > 0 and ask > 0 and midpoint > 0:
            spread_pct = (ask - bid) / midpoint * 100
            if spread_pct > MAX_ENTRY_SPREAD_PCT:
                return (
                    f'spread {spread_pct:.2f}% גבוה מהמקסימום '
                    f'{MAX_ENTRY_SPREAD_PCT:.2f}%'
                )
    except Exception as exc:
        print(f"[EntrySpread] ⚠️ {symbol} spread check unavailable: {exc}", flush=True)
    return None


def place_order(trade: dict, margin: float = MARGIN) -> bool:
    """
    רושם עסקה חדשה:
      • בדיקת כפילות גלובלית — One Trade Per Symbol (חסין לכל סקאנר)
      • בדיקת יתרה + מוסיף ל-active_trades + מנכה מרג'ין — הכל אטומי תחת trades_lock
      • שומר active_trades ל-disk (save_active_trades)
    מחזיר True בהצלחה, False אם נחסם (כפילות / MAX_TRADES / יתרה).
    """
    symbol = trade.get('symbol', '')
    direction = trade.get('direction', 'LONG')
    allowed, reason = is_direction_allowed(direction, 'ValidationTrial')
    regime, _, _, _ = get_market_regime()
    if not allowed:
        print(f"[place_order] 🚫 {symbol} {direction} blocked: {reason}", flush=True)
        return False
    if VALIDATION_TRIAL_ENABLED and regime == 'BULLISH' and direction == 'LONG':
        score = float(trade.get('score', 0) or 0)
        if score < VALIDATION_STRONG_LONG_MIN_SCORE:
            print(
                f"[place_order] 🚫 {symbol} LONG blocked — validation requires score "
                f"≥{VALIDATION_STRONG_LONG_MIN_SCORE} in BULLISH market (got {score:.0f})",
                flush=True,
            )
            return False

    restraint_reason = _entry_restraint_reason(trade)
    if restraint_reason:
        print(f"[place_order] 🧊 {symbol} blocked — {restraint_reason}", flush=True)
        return False

    spread_reason = _entry_spread_reason(symbol)
    if spread_reason:
        print(f"[place_order] 🚫 {symbol} blocked — {spread_reason}", flush=True)
        return False

    # ══ GLOBAL GUARDS — בדיקה + רישום + ניכוי, הכל אטומי תחת trades_lock ═
    # (מונע race בין threads: שתי עסקאות שנפתחות במקביל לא יכולות לקרוא
    #  את אותה יתרה "לפני" הניכוי ושתיהן לעבור את הבדיקה)
    with trades_lock:
        # 1) One Trade Per Symbol
        existing_symbols = [t['symbol'] for t in active_trades]
        if symbol in existing_symbols:
            print(f"[place_order] 🚫 Signal detected for {symbol}, but skipped - Position already exists.")
            return False
        # 2) MAX_TRADES hard cap — מונע race-condition בין סקאנרים
        if len(active_trades) >= MAX_TRADES:
            print(f"[place_order] 🚫 {symbol} skipped — MAX_TRADES ({MAX_TRADES}) reached (atomic check).")
            return False
        # 3) Balance check — אטומי יחד עם ההוספה והניכוי (מונע over-deduct)
        entry_notional = float(trade.get('pos_size', margin * trade.get('leverage', LEVERAGE)) or 0)
        entry_fee = round(entry_notional * TAKER_FEE_RATE, 4)
        if wallet.get('balance', STARTING_BALANCE) < margin + entry_fee:
            print(f"[place_order] 🚫 {symbol} skipped — insufficient balance (atomic check).")
            return False
        # ✅ Passed all guards — add to list + deduct atomically
        trade['market_regime'] = regime
        trial_open = (
            VALIDATION_TRIAL_ENABLED
            and validation_trial.get('status') == 'active'
            and len(validation_trial.get('trades', [])) < validation_trial.get('target', VALIDATION_TRIAL_TARGET)
        )
        trade['validation_trial'] = trial_open
        trade['validation_trial_id'] = validation_trial.get('id') if trial_open else None
        _ensure_fee_accounting(trade, charge_entry_fee=True)
        active_trades.append(trade)
        wallet_deduct(margin)
        _charge_estimated_fee(entry_fee)
    # ═══════════════════════════════════════════════════════════════════
    save_active_trades()
    _fire_make_webhook(trade)   # Make.com webhook — fire-and-forget
    available = wallet.get('balance', STARTING_BALANCE)
    locked    = sum(t.get('margin', MARGIN) for t in active_trades)
    equity    = _get_equity()
    print(
        f"[place_order] ✅ {trade['symbol']} {trade['direction']} "
        f"@ {trade.get('entry', 0):.6g} | margin=${margin:.0f} "
        f"| available=${available:.2f} locked=${locked:.2f} equity=${equity:.2f}"
    )
    return True


def _wallet_opened_summary() -> str:
    """שורת ארנק אחידה לכל הודעת אישור פתיחת עסקה."""
    available  = wallet.get('balance', STARTING_BALANCE)
    locked     = sum(t.get('margin', MARGIN) for t in active_trades)
    unrealized = _get_unrealized_pnl()
    upnl_icon  = "📈" if unrealized >= 0 else "📉"
    return (
        f"📊 *ארנק לאחר פתיחה:*\n"
        f"💰 פנוי: `${available:.2f}`\n"
        f"🔒 נעול: `${locked:.2f}`\n"
        f"{upnl_icon} Unrealized: `${unrealized:+.2f}`"
    )

def _generate_lesson(close_reason: str, pnl_usd: float, duration_min: float,
                     direction: str, score: int) -> str:
    """
    מייצר לקח אוטומטי לכל עסקה סגורה בהתבסס על סיבת היציאה ותוצאה.
    משמש ל-Post-Trade Audit Log.
    """
    win = pnl_usd > 0
    fast = duration_min < 60

    if close_reason == 'TP':
        if fast:
            return (f"⚡ High-Velocity Win — TP הושג תוך {duration_min:.0f} דקות. "
                    f"איתות מומנטום מהיר אומת. שקול entry מוקדם יותר בסטאפים דומים.")
        return (f"✅ Setup Confirmed — תזה טכנית הצליחה לאחר {duration_min:.0f} דקות. "
                f"סבלנות הוכיחה את עצמה. Score={score} היה נכון.")
    if close_reason == 'Trailing':
        if win:
            return (f"📍 Momentum Captured — Trailing Stop נעל רווח של ${pnl_usd:+.2f}. "
                    f"יציאה דינמית עבדה היטב. בדוק אם ATR Trail היה מהודק מדי.")
        return (f"⚠️ Early Reversal — Trailing נגע בהפסד (${pnl_usd:.2f}). "
                f"כניסה הייתה מוקדמת מדי או המומנטום התהפך חדות. "
                f"בדוק Volume Ratio בכניסה.")
    if close_reason == 'SL':
        return (f"❌ SL Hit — תזת ה-{direction} התבטלה. הפסד ${pnl_usd:.2f} לאחר {duration_min:.0f} דקות. "
                f"Score={score}. בדוק: האם ה-BTC Compass הצביע לאותו כיוון? "
                f"האם הייתה התנגדות שקרובה לכניסה?")
    if close_reason == 'BE':
        return (f"🔒 Capital Preserved — עסקה יצאה ב-Break Even. "
                f"תזה לא המשיכה אך הון לא אבד. "
                f"{'מהלך מהיר ללא המשך — חסר נפח.' if fast else 'עסקה הבשילה לאט — שוק לא שיתף פעולה.'}")
    if close_reason in ('Scalp-TP', 'Scalp-SL', 'Scalp-Time'):
        if win:
            return (f"⚡ Scalp Win — {close_reason} | ${pnl_usd:+.2f} תוך {duration_min:.0f} דקות. "
                    f"Mean-Reversion עבד. Score={score}.")
        return (f"⚡ Scalp Loss — {close_reason} | ${pnl_usd:.2f} לאחר {duration_min:.0f} דקות. "
                f"בדוק: האם ה-RSI היה בקיצוניות מספקת? Volume Spike אמיתי?")
    if close_reason == 'Partial25':
        return (f"💰 Partial Take — 25% נסגרו בשיא עם drop חזרה. "
                f"טריגר P&L: ${pnl_usd:+.2f}. Trailing מופעל על השאר.")
    # Generic
    if win:
        return f"✅ {close_reason} — עסקה רווחית ${pnl_usd:+.2f} · {duration_min:.0f} דקות · Score={score}."
    return f"📉 {close_reason} — עסקה הפסידה ${pnl_usd:.2f} · {duration_min:.0f} דקות · Score={score}. חזור על ניתוח הכניסה."


def _save_audit_log():
    """שומר את trade_audit_log ל-Replit DB + disk cache (100 עסקאות אחרונות)."""
    global trade_audit_log
    trade_audit_log = trade_audit_log[-100:]
    try:
        payload = {
            'updated': now_il().isoformat(timespec='seconds'),
            'count':   len(trade_audit_log),
            'trades':  trade_audit_log,
        }
        state_store.save_state('trade_audit', payload, AUDIT_LOG_FILE)
    except Exception as e:
        print(f"[AuditLog] שגיאה בשמירה: {e}", flush=True)


def _load_audit_log():
    """טוען audit log מ-Replit DB / מהדיסק."""
    global trade_audit_log
    data = state_store.load_state(
        'trade_audit',
        AUDIT_LOG_FILE,
        {'updated': None, 'count': 0, 'trades': []},
    )
    trade_audit_log = data.get('trades', []) if isinstance(data, dict) else []
    print(f"[AuditLog] נטען: {len(trade_audit_log)} עסקאות", flush=True)


def _record_validation_trade(record: dict):
    """Append only post-trial trades to the fixed 100-trade validation sample."""
    if (
        not record.get('validation_trial')
        or record.get('validation_trial_id') != validation_trial.get('id')
        or validation_trial.get('status') != 'active'
    ):
        return
    trades = validation_trial.setdefault('trades', [])
    if len(trades) >= validation_trial.get('target', VALIDATION_TRIAL_TARGET):
        validation_trial['status'] = 'completed'
        validation_trial['completed_at'] = validation_trial.get('completed_at') or now_il().isoformat(timespec='seconds')
        _save_validation_trial()
        return

    trades.append(record)
    if len(trades) >= validation_trial.get('target', VALIDATION_TRIAL_TARGET):
        validation_trial['status'] = 'completed'
        validation_trial['completed_at'] = now_il().isoformat(timespec='seconds')
        send_msg(
            f"🏁 *ניסוי 100 העסקאות הושלם*\n\n"
            f"נסגרו {len(trades)}/{validation_trial['target']} עסקאות נייר.\n"
            f"פתח את הדשבורד לתוצאות נטו אחרי עמלות."
        )
    _save_validation_trial()


def _extract_prebreakout(breakdown: str) -> str:
    """מחלץ מ-score_breakdown אילו סיגנלים Pre-Breakout זוהו בכניסה."""
    signals = []
    if 'Squeeze=+' in breakdown:
        signals.append('BB Squeeze')
    if 'VolBuild=+' in breakdown:
        signals.append('Vol Buildup')
    if 'RSIDiv=+' in breakdown:
        signals.append('RSI Divergence')
    if 'FVG=+' in breakdown:
        signals.append('FVG (ICT)')
    return ' + '.join(signals) if signals else 'None'


class TradeLogger:
    CSV_PATH = os.path.join(os.path.dirname(__file__), "trade_history.csv")
    COLUMNS  = ["timestamp", "symbol", "side", "entry_price", "exit_price", "pnl_usd", "exit_reason"]

    @classmethod
    def log(cls, symbol: str, side: str, entry_price: float,
            exit_price: float, pnl_usd: float, exit_reason: str) -> None:
        file_exists = os.path.isfile(cls.CSV_PATH)
        try:
            with open(cls.CSV_PATH, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=cls.COLUMNS)
                if not file_exists:
                    writer.writeheader()
                writer.writerow({
                    "timestamp":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol":      symbol,
                    "side":        side,
                    "entry_price": entry_price,
                    "exit_price":  exit_price,
                    "pnl_usd":     round(pnl_usd, 2),
                    "exit_reason": exit_reason,
                })
        except Exception as e:
            print(f"[TradeLogger] שגיאה בכתיבה ל-CSV: {e}", flush=True)


def _log_closed_trade(trade: dict, close_reason: str, pnl_usd: float, close_price: float = None):
    """מוסיף עסקה סגורה ל-closed_trades_log + Audit Log מתמיד."""
    global closed_trades_log, trade_audit_log
    _record_exit_leg(trade, pnl_usd, _remaining_notional(trade))
    accounting = _ensure_fee_accounting(trade)
    gross_pnl = round(accounting.get('gross_pnl_usd', pnl_usd), 2)
    fees_usd = round(accounting.get('fees_usd', 0.0), 2)
    net_pnl = round(gross_pnl - fees_usd, 2)
    entry_p    = trade['entry']
    close_p    = close_price or trade.get('current_price', entry_p)
    sl_at_open = trade.get('sl')
    tp_at_open = trade.get('tp')
    closed_at  = now_il().isoformat(timespec='seconds')

    # ── R:R מחושב מ-SL/TP בפתיחה ────────────────────────────────────────
    try:
        dist_tp  = abs(tp_at_open - entry_p) / entry_p * 100 if tp_at_open else None
        dist_sl  = abs(sl_at_open - entry_p) / entry_p * 100 if sl_at_open else None
        rr_ratio = round(dist_tp / dist_sl, 2) if (dist_tp and dist_sl and dist_sl > 0) else None
    except Exception:
        dist_tp = dist_sl = rr_ratio = None

    # ── duration בדקות ───────────────────────────────────────────────────
    try:
        opened_dt  = datetime.fromisoformat(trade.get('opened_at', closed_at))
        closed_dt  = datetime.fromisoformat(closed_at)
        duration_m = round((closed_dt - opened_dt).total_seconds() / 60, 1)
    except Exception:
        duration_m = 0.0

    # ── R:R שהושג בפועל ──────────────────────────────────────────────────
    try:
        actual_move_pct = abs(close_p - entry_p) / entry_p * 100
        rr_achieved = round(actual_move_pct / dist_sl, 2) if dist_sl else None
    except Exception:
        rr_achieved = None

    record = {
        'symbol':          trade['symbol'],
        'direction':       trade['direction'],
        'timeframe':       trade.get('timeframe', '4H'),
        'entry_price':     entry_p,
        'close_price':     close_p,
        'sl_at_open':      sl_at_open,
        'tp_at_open':      tp_at_open,
        'dist_sl_pct':     round(dist_sl, 2) if dist_sl is not None else None,
        'dist_tp_pct':     round(dist_tp, 2) if dist_tp is not None else None,
        'rr_ratio':        rr_ratio,
        'rr_achieved':     rr_achieved,
        'score':           trade.get('score', 0),
        'rsi':             trade.get('rsi'),
        'ema200':          trade.get('ema200'),
        'score_breakdown': trade.get('score_breakdown', ''),
        'strategy':        trade.get('strategy', ''),
        'track':           trade.get('track', 'Swing'),
        'slippage_pct':    trade.get('slippage_pct', 0.0),
        'close_reason':    close_reason,
        # pnl_usd remains for backwards compatibility and is now always net.
        'pnl_usd':         net_pnl,
        'gross_pnl_usd':   gross_pnl,
        'fees_usd':        fees_usd,
        'net_pnl_usd':     net_pnl,
        'fee_rate_pct':    round(TAKER_FEE_RATE * 100, 4),
        'opened_at':       trade.get('opened_at', ''),
        'closed_at':       closed_at,
        # ── Entry Context (Audit) ────────────────────────────────────────
        'fng_at_entry':    trade.get('fng_at_entry'),
        'atr':             trade.get('atr', 0.0),
        'duration_min':    duration_m,
        'scalp':           trade.get('scalp', False),
        'volume_ratio':    trade.get('volume_ratio'),
        'market_regime':   trade.get('market_regime', 'Unknown'),
        'validation_trial': bool(trade.get('validation_trial')),
        'validation_trial_id': trade.get('validation_trial_id'),
        # ── Auto-generated lesson ────────────────────────────────────────
        'lesson': _generate_lesson(
            close_reason, net_pnl, duration_m,
            trade['direction'], trade.get('score', 0)
        ),
        # ── Pre-Breakout Signals שהובילו לכניסה ─────────────────────────
        'prebreakout_signals': _extract_prebreakout(trade.get('score_breakdown', '')),
    }

    # ── closed_trades_log (in-memory, 48h) ───────────────────────────────
    closed_trades_log.append(record)
    cutoff = now_il().timestamp() - 48 * 3600
    closed_trades_log = [
        t for t in closed_trades_log
        if datetime.fromisoformat(t['closed_at']).timestamp() >= cutoff
    ]

    # ── Audit Log (persistent, 100 עסקאות) ───────────────────────────────
    trade_audit_log.append(record)
    _save_audit_log()
    _record_validation_trade(record)

    # ── CSV Trade History (trade_history.csv בשורש הפרויקט) ──────────────
    TradeLogger.log(
        symbol      = record['symbol'],
        side        = record['direction'],
        entry_price = record['entry_price'],
        exit_price  = record['close_price'],
        pnl_usd     = record['net_pnl_usd'],
        exit_reason = record['close_reason'],
    )

    # ── Web Push Notification ─────────────────────────────────────────────
    sym_short  = record['symbol'].replace('/USDT', '')
    pnl_sign   = '+' if net_pnl >= 0 else ''
    win_emoji  = '✅' if net_pnl >= 0 else '❌'
    send_push(
        title=f"{win_emoji} {sym_short} {record['direction']} נסגרה",
        body=f"{pnl_sign}${net_pnl:.2f} net | {close_reason}",
        tag=f"close-{record['symbol']}",
    )
    return record

def wallet_status_text() -> str:
    """מחזיר מחרוזת סטטוס ארנק לטלגרם — Available Balance ראשי."""
    available  = wallet.get('balance', STARTING_BALANCE)
    start      = wallet.get('starting', STARTING_BALANCE)
    realized   = wallet.get('total_pnl', 0.0)
    locked     = sum(t.get('margin', MARGIN) for t in active_trades)
    unrealized = _get_unrealized_pnl()
    equity     = _get_equity()   # available + locked + unrealized
    eq_pct     = round((equity - start) / start * 100, 1)
    r_icon     = "📈" if realized  >= 0 else "📉"
    u_icon     = "📈" if unrealized >= 0 else "📉"
    return (
        f"💼 *ארנק וירטואלי*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *יתרה פנויה: `${available:.2f}`*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔒 נעול בעסקאות: `${locked:.2f}`\n"
        f"{u_icon} Unrealized P&L: `${unrealized:+.2f}`\n"
        f"{r_icon} Realized P&L: `${realized:+.2f}`"
    )


def get_data(symbol, timeframe='1h', limit=250):
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    return df


# ── Async OHLCV Pre-fetcher ────────────────────────────────────────────────────
# Cache for async-fetched DataFrames: {(symbol, timeframe): DataFrame}
_ohlcv_cache: dict = {}
# Cache for async-fetched Open Interest: {symbol: oi_data_dict}
_oi_cache: dict = {}
_async_exchange_instance = None


async def _get_async_exchange():
    """Returns (or creates) the shared ccxt.pro.bitget async instance."""
    global _async_exchange_instance
    if _async_exchange_instance is None:
        _async_exchange_instance = ccxt_async.bitget({
            'apiKey':          os.environ.get('BITGET_KEY', ''),
            'secret':          os.environ.get('BITGET_SECRET', ''),
            'password':        os.environ.get('BITGET_PW', ''),
            'enableRateLimit': True,
            'options':         {'defaultType': 'swap'},
        })
    return _async_exchange_instance


async def _fetch_ohlcv_async(symbol: str, timeframe: str, limit: int = 250) -> pd.DataFrame:
    """Async wrapper around ccxt fetch_ohlcv."""
    ex   = await _get_async_exchange()
    bars = await ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    return pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])


async def _fetch_oi_async(symbol: str) -> dict:
    """Async fetch of current Open Interest for a single swap-market symbol.
    Returns the raw ccxt OI dict (keys: openInterest, openInterestValue, ...)
    or an empty dict on failure — callers must handle missing keys gracefully.
    """
    ex = await _get_async_exchange()
    return await ex.fetch_open_interest(symbol)


async def _prefetch_ohlcv(candidates: list, timeframes: list = None):
    """
    Pre-fetches OHLCV data + Open Interest for all candidates in parallel.
    OHLCV results stored in _ohlcv_cache[(symbol, timeframe)].
    OI results stored in _oi_cache[symbol].
    Speeds up _scan_batch by eliminating sequential API latency.
    """
    global _ohlcv_cache, _oi_cache, _async_exchange_instance
    if timeframes is None:
        timeframes = ['4h', '1h', '15m']

    # ── OHLCV tasks (skip already-cached keys) ────────────────────────────────
    ohlcv_tasks = []
    ohlcv_keys  = []
    for c in candidates:
        sym = c['symbol'] if isinstance(c, dict) else c
        for tf in timeframes:
            key = (sym, tf)
            if key not in _ohlcv_cache:
                ohlcv_tasks.append(_fetch_ohlcv_async(sym, tf, 250))
                ohlcv_keys.append(key)

    # ── OI tasks — always refresh; OI changes between scans ──────────────────
    oi_tasks = []
    oi_syms  = []
    for c in candidates:
        sym = c['symbol'] if isinstance(c, dict) else c
        oi_tasks.append(_fetch_oi_async(sym))
        oi_syms.append(sym)

    all_tasks = ohlcv_tasks + oi_tasks
    if not all_tasks:
        return

    all_results = await asyncio.gather(*all_tasks, return_exceptions=True)

    # ── Store OHLCV results ───────────────────────────────────────────────────
    ohlcv_ok = 0
    for key, result in zip(ohlcv_keys, all_results[:len(ohlcv_tasks)]):
        if isinstance(result, Exception):
            print(f"[ASYNC] OHLCV fetch failed {key}: {result}")
        else:
            _ohlcv_cache[key] = result
            ohlcv_ok += 1

    # ── Store OI results ──────────────────────────────────────────────────────
    oi_ok = 0
    for sym, result in zip(oi_syms, all_results[len(ohlcv_tasks):]):
        if isinstance(result, Exception):
            print(f"[ASYNC] OI fetch failed {sym}: {result}")
        else:
            _oi_cache[sym] = result
            oi_ok += 1

    # Close and reset the async exchange to free connections
    try:
        if _async_exchange_instance is not None:
            await _async_exchange_instance.close()
            _async_exchange_instance = None
    except Exception:
        pass
    print(f"[ASYNC] pre-fetched {ohlcv_ok}/{len(ohlcv_tasks)} OHLCV | "
          f"{oi_ok}/{len(oi_tasks)} OI tasks in parallel")


def get_data_cached(symbol: str, timeframe: str = '1h', limit: int = 250) -> pd.DataFrame:
    """
    Returns pre-fetched OHLCV DataFrame from _ohlcv_cache if available.
    Falls back to synchronous get_data() on cache miss.
    Used inside _scan_batch to transparently benefit from async pre-fetch.
    """
    key = (symbol, timeframe)
    if key in _ohlcv_cache:
        return _ohlcv_cache[key]
    return get_data(symbol, timeframe, limit)

def generate_chart(df, symbol, entry, sl, tp, direction='LONG',
                   fvg_top=None, fvg_bot=None):
    """מייצר גרף נרות עם EMA200, Bollinger Bands, RSI, ווליום וקווי SL/Entry/TP.
       fvg_top / fvg_bot — אם מסופקים, מצייר אזור FVG (ICT Fair Value Gap).
       direction='LONG' → ירוק | 'SHORT' → אדום.
       מחזיר BytesIO או None אם נכשל."""
    global CHARTS_ENABLED, _mpl_imported, plt, mpf
    if not CHARTS_ENABLED:
        return None
    # Lazy import — מייבא matplotlib רק בפעם הראשונה שנדרש גרף
    if not _mpl_imported:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as _plt
            import mplfinance as _mpf
            plt = _plt
            mpf = _mpf
            _mpl_imported = True
            print("[Chart] matplotlib + mplfinance imported successfully (lazy)", flush=True)
        except ImportError as _ie:
            CHARTS_ENABLED = False
            print(f"[Chart] mplfinance not available — charts disabled: {_ie}", flush=True)
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
        _ema200 = ta.ema(df['close'], length=200)
        ema200_vals = _ema200.tail(72).values if _ema200 is not None else [None] * 72

        _rsi = ta.rsi(df['close'], length=14)
        rsi_vals = _rsi.tail(72).values if _rsi is not None else [50.0] * 72

        # ── Bollinger Bands (20, 2σ) ──
        try:
            bb_raw = ta.bbands(df['close'], length=20, std=2)
            bbu_col = next((c for c in bb_raw.columns if 'BBU_' in c), None)
            bbl_col = next((c for c in bb_raw.columns if 'BBL_' in c), None)
            bbm_col = next((c for c in bb_raw.columns if 'BBM_' in c), None)
            bb_upper = bb_raw[bbu_col].tail(72).values if bbu_col else None
            bb_lower = bb_raw[bbl_col].tail(72).values if bbl_col else None
            bb_mid   = bb_raw[bbm_col].tail(72).values if bbm_col else None
            bb_ok    = bb_upper is not None
        except Exception:
            bb_ok = False

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

        # הוסף BB לגרף
        if bb_ok:
            apds += [
                mpf.make_addplot(bb_upper, color='#546e7a', width=0.8,
                                 linestyle='--', alpha=0.7),
                mpf.make_addplot(bb_mid,   color='#546e7a', width=0.6,
                                 linestyle=':', alpha=0.5),
                mpf.make_addplot(bb_lower, color='#546e7a', width=0.8,
                                 linestyle='--', alpha=0.7),
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

        # הרחבת ציר Y כך ש-SL ו-TP תמיד גלויים (עם מרווח 2%)
        y_min, y_max = ax.get_ylim()
        pad = (y_max - y_min) * 0.02
        new_ymin = min(y_min, sl - pad, tp - pad)
        new_ymax = max(y_max, sl + pad, tp + pad)
        ax.set_ylim(new_ymin, new_ymax)

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

        # ── FVG Zone (ICT Fair Value Gap) ──
        if fvg_top is not None and fvg_bot is not None:
            try:
                y_min, y_max = ax.get_ylim()
                clamp_top = min(fvg_top, y_max)
                clamp_bot = max(fvg_bot, y_min)
                if clamp_top > clamp_bot:
                    ax.axhspan(clamp_bot, clamp_top,
                               alpha=0.18, color='#ff9800', zorder=1,
                               label=f'FVG {fvg_bot:.4f}–{fvg_top:.4f}')
                    ax.axhline(clamp_top, color='#ff9800', linewidth=0.6, linestyle=':')
                    ax.axhline(clamp_bot, color='#ff9800', linewidth=0.6, linestyle=':')
                    mid_fvg = (clamp_top + clamp_bot) / 2
                    ax.text(ax.get_xlim()[0] * 1.01, mid_fvg, ' FVG',
                            color='#ff9800', fontsize=7, va='center',
                            fontweight='bold')
            except Exception:
                pass

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


def _make_close_markup(symbol: str):
    """מחזיר InlineKeyboardMarkup עם כפתור 'Close Position' לסימבול נתון."""
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton(
        "❌ Close Position",
        callback_data=f"close_{symbol}"
    ))
    return markup


def send_chart_alert(chart_buf, symbol, caption):
    """שולח גרף עם כיתוב קצר, ואז את ההודעה המלאה בנפרד + כפתור Close."""
    try:
        if chart_buf:
            short = f"📊 *{symbol}* — גרף 1H עם BB / SL / Entry / TP"
            bot.send_photo(CHAT_ID, chart_buf, caption=short,
                           parse_mode='Markdown')
        markup = _make_close_markup(symbol)
        bot.send_message(CHAT_ID, caption, parse_mode='Markdown',
                         reply_markup=markup)
    except Exception as e:
        print(f"Send chart error: {e}")
        try:
            send_msg(caption)
        except Exception:
            pass

# --- שלב 1+2: משפך — מועמדים חמים ---

def get_hot_candidates():
    """
    שלב 1: שליפת כל זוגות USDT מ-Bitget swap (ווליום $1M+)
    שלב 2: Top 15 Gainers (LONG) + Top 15 Losers (SHORT)
    מחזיר: (gainers_list, losers_list)

    שימוש ב-exchange_md (ללא paper flag) כי Bitget Paper mode חוסם
    fetch_tickers() — מחזיר 0 תוצאות ללא רשימת סימבולים מפורשת.
    """
    try:
        # Bitget swap: fetch_tickers() without args returns 0 results.
        # Must load markets first, then pass explicit symbol list.
        # exchange_md = real market data instance (no paper flag).
        if not exchange_md.markets:
            exchange_md.load_markets()
        usdt_symbols = [
            s for s, m in exchange_md.markets.items()
            if s.endswith('/USDT') and m.get('active')
        ]
        if VERBOSE_LOG:
            print(f"[Scan] Fetching {len(usdt_symbols)} USDT swap tickers...")
        tickers = exchange_md.fetch_tickers(usdt_symbols)

        gainers, losers = [], []
        low_vol = 0

        for symbol, ticker in tickers.items():
            change_pct = ticker.get('percentage', None)
            volume_usd = ticker.get('quoteVolume', 0) or 0
            last_price = ticker.get('last', 0) or 0

            if change_pct is None or last_price <= 0:
                continue

            if volume_usd < 1_000_000:
                low_vol += 1
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

        print(f"[Scan] {len(usdt_symbols)} pairs → {len(gainers)}↑ gainers "
              f"{len(losers)}↓ losers ({low_vol} low-vol filtered)", flush=True)

        gainers.sort(key=lambda x: x['change_pct'], reverse=True)
        losers.sort(key=lambda x: x['change_pct'])   # שלילי ביותר קודם

        top_gainers = gainers[:15]
        top_losers  = losers[:15]

        # שמירה לדאשבורד (גיינרים)
        data = {
            'updated':    now_il().strftime('%H:%M:%S'),
            'count':      len(top_gainers),
            'candidates': top_gainers
        }
        os.makedirs(os.path.dirname(HOT_CANDIDATES_FILE), exist_ok=True)
        with open(HOT_CANDIDATES_FILE, 'w') as f:
            json.dump(data, f)

        if VERBOSE_LOG:
            print(f"[Scan] Gainers: {[c['symbol'] for c in top_gainers]}")
            print(f"[Scan] Losers:  {[c['symbol'] for c in top_losers]}")
        return top_gainers, top_losers

    except Exception as e:
        print(f"[Scan] Hot candidates error: {e}", flush=True)
        return [], []


def save_active_trades():
    """שומר עסקאות פעילות ל-Replit DB + cache בדיסק לדאשבורד."""
    try:
        with trades_lock:
            snapshot = list(active_trades)
        data = {
            'updated': now_il().strftime('%H:%M:%S'),
            'count':   len(snapshot),
            'trades':  snapshot,
        }
        state_store.save_state('active_trades', data, ACTIVE_TRADES_FILE)
    except Exception as e:
        print(f"save_active_trades error: {e}", flush=True)


def get_btc_regime():
    """
    מחזיר את מצב השוק לפי BTC/USDT ו-EMA20 (4H).
    'BULL' — BTC מעל EMA20(4H) → מאפשר LONG
    'BEAR' — BTC מתחת EMA20(4H) → מאפשר SHORT
    'NEUTRAL' — שגיאה בשליפה → מאפשר הכל (safe fallback)
    """
    try:
        df    = get_data('BTC/USDT', timeframe='4h', limit=60)
        ema20 = ta.ema(df['close'], length=20).iloc[-1]
        price = df['close'].iloc[-1]
        regime = 'BULL' if price > ema20 else 'BEAR'
        pct = round((price - ema20) / ema20 * 100, 2)
        if VERBOSE_LOG:
            print(f"[BTC] Regime={regime} price={price:.0f} EMA20(4H)={ema20:.0f} ({pct:+.2f}%)")
        return regime
    except Exception as e:
        print(f"BTC regime check failed: {e} — defaulting to NEUTRAL")
        return 'NEUTRAL'


# ── Market Regime Cache (TTL 5 min) ───────────────────────────────────────────
_market_regime_cache: dict = {'ts': 0.0, 'regime': 'NEUTRAL', 'fng_v': 50, 'btc_above': True, 'ema20': 0.0}
_MARKET_REGIME_TTL = 300   # seconds


def get_market_regime() -> tuple[str, int, bool, float]:
    """
    מחזיר (regime, fng_v, btc_above_ema20, ema20_4h) עם cache של 5 דקות.

    BEARISH: FNG < REGIME_BEARISH_FNG(38) AND BTC < EMA20(4H) → חוסם LONGs
    BULLISH: FNG > REGIME_BULLISH_FNG(60) AND BTC > EMA20(4H) → חוסם SHORTs
    NEUTRAL: אחרת → שני הכיוונים מותרים, max_trades מוגבל ל-2
    """
    global _market_regime_cache
    now_ts = time.time()
    if now_ts - _market_regime_cache['ts'] < _MARKET_REGIME_TTL:
        c = _market_regime_cache
        return c['regime'], c['fng_v'], c['btc_above'], c['ema20']

    # Fail-SAFE defaults — API failure → assume worst case to protect capital
    fng_v     = 0      # treat unknown FNG as extreme fear → triggers BEARISH
    btc_above = False  # treat unknown BTC position as below EMA → triggers BEARISH
    ema20_4h  = _market_regime_cache.get('ema20', 0.0)
    _fng_ok   = False
    _btc_ok   = False
    _df_btc   = None

    # ── BTC קודם — נדרש גם לסינתטי FNG ──────────────────────────────────────
    try:
        _df_btc   = get_data('BTC/USDT', timeframe='4h', limit=60)
        ema20_4h  = float(ta.ema(_df_btc['close'], length=20).iloc[-1])
        btc_price = float(_df_btc['close'].iloc[-1])
        btc_above = btc_price > ema20_4h
        _btc_ok   = True
    except Exception as _be:
        print(f"[MarketRegime] BTC EMA20 fetch failed: {_be} — defaulting btc_above=False (BEARISH safe)", flush=True)

    # ── FNG לregime: רשמי בלבד (API זמין) — סינתטי רק כגיבוי כשהAPI נפל ────
    try:
        fng_v, _ = get_fng_regime(_df_btc)
        _fng_ok  = True
    except Exception as _fe:
        print(f"[MarketRegime] FNG fetch failed: {_fe} — defaulting fng_v=0 (BEARISH safe)", flush=True)

    if fng_v < REGIME_BEARISH_FNG or not btc_above:
        regime = 'BEARISH'
    elif fng_v > REGIME_BULLISH_FNG and btc_above:
        regime = 'BULLISH'
    else:
        regime = 'NEUTRAL'

    # Both directions can be blocked at once: BEARISH blocks LONG while the
    # BTC Compass blocks SHORT on a strong BTC. A legitimate sit-this-out
    # state, but silence makes it look identical to a fault.
    if regime == 'BEARISH' and btc_above:
        print(f"[MarketRegime] DUAL BLOCK - FNG={fng_v} < {REGIME_BEARISH_FNG} "
              f"blocks LONG while BTC above EMA20 may block SHORT. Scanner "
              f"may return zero signals; a decision, not a fault.", flush=True)
    _market_regime_cache = {'ts': now_ts, 'regime': regime, 'fng_v': fng_v,
                             'btc_above': btc_above, 'ema20': ema20_4h}
    _data_src = f"{'FNG✓' if _fng_ok else 'FNG✗(safe)'} {'BTC✓' if _btc_ok else 'BTC✗(safe)'}"
    print(
        f"[MarketRegime] {regime} | FNG={fng_v} | "
        f"BTC {'above' if btc_above else 'below'} EMA20(4H)={ema20_4h:.0f} | {_data_src}",
        flush=True
    )
    return regime, fng_v, btc_above, ema20_4h


def is_direction_allowed(direction: str, context: str = '') -> tuple[bool, str]:
    """
    Gate מרכזי — מחזיר (allowed, reason).
    BEARISH → LONGs חסומים | BULLISH → SHORTs חסומים | NEUTRAL → max 2 עסקאות.
    """
    regime, fng_v, btc_above, ema20 = get_market_regime()
    btc_lbl = f"BTC {'מעל' if btc_above else 'מתחת'} EMA20(4H)={ema20:.0f}"

    if VALIDATION_TRIAL_ENABLED and regime == 'NEUTRAL':
        reason = "Validation Trial — NEUTRAL/sideways market, no new entries"
        if context:
            print(f"[RegimeGate/{context}] {reason}", flush=True)
        return False, reason

    if regime == 'BEARISH' and direction == 'LONG':
        # הדגש את הסיבה הספציפית: BTC מתחת ל-EMA20 vs FNG
        if not btc_above:
            reason = f"BTC מתחת EMA20(4H)={ema20:.0f} — LONG חסום ישירות (FNG={fng_v})"
        else:
            reason = f"BEARISH Regime — FNG={fng_v} < {REGIME_BEARISH_FNG} {btc_lbl} → LONGs חסומים"
        if context:
            print(f"[RegimeGate/{context}] {reason}", flush=True)
        return False, reason

    if regime == 'BULLISH' and direction == 'SHORT':
        reason = f"BULLISH Regime — FNG={fng_v} {btc_lbl} → SHORTs חסומים"
        if context:
            print(f"[RegimeGate/{context}] {reason}", flush=True)
        return False, reason

    if regime == 'NEUTRAL':
        # ── BTC Strong Uptrend — חוסם SHORTs כשBTC מעל EMA200 בשני גרפים ──────
        if direction == 'SHORT':
            _strong_up, _ema200_1h, _ema200_4h = check_btc_strong_uptrend()
            if _strong_up:
                reason = (
                    f"NEUTRAL + BTC STRONG UPTREND "
                    f"(מעל EMA200_1H={_ema200_1h:,.0f} & EMA200_4H={_ema200_4h:,.0f}) "
                    f"→ SHORT נגד מגמה מאקרו — חסום"
                )
                if context:
                    print(f"[RegimeGate/{context}] 🚫 {reason}", flush=True)
                return False, reason

        # ── BTC Intraday Bias — חוסם SHORTs/LONGs נגד המגמה בשוק NEUTRAL ──────
        _intraday_bias, _btc_p, _btc_ema15m = get_btc_intraday_bias()
        if direction == 'SHORT' and _intraday_bias == 'BULLISH':
            reason = (
                f"NEUTRAL + BTC Intraday BULLISH "
                f"({_btc_p:.0f} > EMA20(15m)={_btc_ema15m:.0f} & DailyOpen) "
                f"→ SHORT נגד המגמה — חסום"
            )
            if context:
                print(f"[RegimeGate/{context}] 🚫 {reason}", flush=True)
            return False, reason
        if direction == 'LONG' and _intraday_bias == 'BEARISH':
            reason = (
                f"NEUTRAL + BTC Intraday BEARISH "
                f"({_btc_p:.0f} < EMA20(15m)={_btc_ema15m:.0f} & DailyOpen) "
                f"→ LONG נגד המגמה — חסום"
            )
            if context:
                print(f"[RegimeGate/{context}] 🚫 {reason}", flush=True)
            return False, reason

        n_active = len(active_trades)
        if n_active >= REGIME_NEUTRAL_MAX_TRADES:
            reason = (f"NEUTRAL Regime — שוק צדדי, מקסימום {REGIME_NEUTRAL_MAX_TRADES} "
                      f"עסקאות (פעיל={n_active})")
            if context:
                print(f"[RegimeGate/{context}] {reason}", flush=True)
            return False, reason

    return True, ''


# Cache ל-BTC Parabolic Bull check (15 דקות TTL)
_btc_parabolic_cache: dict = {'ts': 0.0, 'result': False, 'rsi': 0.0, 'ema': 0.0}

def is_btc_parabolic_bull() -> tuple[bool, float, float]:
    """
    האם BTC נמצא בעלייה פרבולית?
    תנאי: BTC 4H מעל EMA200 AND BTC RSI(1H) > 65
    אם כן — חוסם את כל האיתותים SHORT על אלטקוין.

    מחזיר: (is_parabolic: bool, rsi_1h: float, ema200_4h: float)
    Cache: 15 דקות — לא מבצע API call בכל סריקה.
    """
    global _btc_parabolic_cache
    now_ts = time.time()
    if now_ts - _btc_parabolic_cache['ts'] < 900:   # 15 min cache
        return (_btc_parabolic_cache['result'],
                _btc_parabolic_cache['rsi'],
                _btc_parabolic_cache['ema'])
    try:
        df_4h    = get_data('BTC/USDT', timeframe='4h', limit=210)
        df_1h    = get_data('BTC/USDT', timeframe='1h', limit=30)
        ema200   = float(ta.ema(df_4h['close'], length=200).iloc[-1])
        rsi_1h   = float(ta.rsi(df_1h['close'], length=14).iloc[-1])
        price_4h = float(df_4h['close'].iloc[-1])
        result   = (price_4h > ema200) and (rsi_1h > 65)
        _btc_parabolic_cache = {'ts': now_ts, 'result': result, 'rsi': rsi_1h, 'ema': ema200}
        if result:
            print(f"[BTC Compass] 🐂 PARABOLIC BULL — {price_4h:.0f} > EMA200={ema200:.0f} + RSI1H={rsi_1h:.1f}>65 → SHORTs חסומים")
        else:
            print(f"[BTC Compass] no parabolic — RSI1H={rsi_1h:.1f} | price vs EMA200: {price_4h:.0f}/{ema200:.0f}")
        return result, rsi_1h, ema200
    except Exception as e:
        print(f"[BTC Compass] check failed: {e}")
        return False, 0.0, 0.0


# Cache ל-BTC Strong Uptrend check (10 דקות TTL)
_btc_strong_uptrend_cache: dict = {'ts': 0.0, 'result': False, 'ema200_1h': 0.0, 'ema200_4h': 0.0, 'price': 0.0}

def is_btc_strong_uptrend() -> tuple[bool, float, float]:
    """
    Strong Uptrend: מחיר BTC מעל EMA200 בשני גרפים — 1H וגם 4H.

    כש-True → SHORTs על אלטקוינים מסוכנים ביותר (מנוגדים לטרנד המאקרו).
    הבוט ידרוש ציון > STRONG_UPTREND_SHORT_MIN_SCORE לכל SHORT.

    Cache: 10 דקות — לא מבצע API calls מיותרים.
    מחזיר: (is_strong_uptrend: bool, ema200_1h: float, ema200_4h: float)
    """
    global _btc_strong_uptrend_cache
    now_ts = time.time()
    if now_ts - _btc_strong_uptrend_cache['ts'] < 600:   # 10 min cache
        return (_btc_strong_uptrend_cache['result'],
                _btc_strong_uptrend_cache['ema200_1h'],
                _btc_strong_uptrend_cache['ema200_4h'])
    try:
        df_1h    = get_data('BTC/USDT', timeframe='1h', limit=210)
        df_4h    = get_data('BTC/USDT', timeframe='4h', limit=210)
        ema200_1h = float(ta.ema(df_1h['close'], length=200).iloc[-1])
        ema200_4h = float(ta.ema(df_4h['close'], length=200).iloc[-1])
        price     = float(df_1h['close'].iloc[-1])
        result    = (price > ema200_1h) and (price > ema200_4h)
        _btc_strong_uptrend_cache = {
            'ts': now_ts, 'result': result,
            'ema200_1h': ema200_1h, 'ema200_4h': ema200_4h, 'price': price
        }
        if result:
            print(f"[BTC Trend] 🟢 STRONG UPTREND — ${price:,.0f} > EMA200_1H={ema200_1h:,.0f} & EMA200_4H={ema200_4h:,.0f}")
        else:
            above_1h = price > ema200_1h
            above_4h = price > ema200_4h
            print(f"[BTC Trend] ⚪ No strong uptrend — 1H={'✓' if above_1h else '✗'} 4H={'✓' if above_4h else '✗'}")
        return result, ema200_1h, ema200_4h
    except Exception as e:
        print(f"[BTC Trend] check failed: {e}")
        return False, 0.0, 0.0


# ═══════════════════════════════════════════════════════════════
# Strategy Parameters — all constants → config.py (from config import *)
# Only MAX_TRADES is initialised here (dynamic runtime value).
# ═══════════════════════════════════════════════════════════════
MAX_TRADES = _config_loaded.get('max_trades', 3)   # נטען מ-config.json · ניתן לשינוי דינמי (/slots)

# All other strategy constants (SL params, scalp, cliff, track, BB squeeze, etc.)
# → config.py (from config import *)


# get_fng_mode, score_candles, detect_*, score_symbol, calc_risk_position,
# get_dynamic_sl, and all strategy constants → moved to market_logic.py / config.py

_major_watch_state: dict = {}   # {symbol: {decision, trend_ok, breakout}}



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
    """P&L ($) על 75% פוזיציה ($187.50) לפי % מרחק מהכניסה (TP1 close)."""
    return round(POSITION_SIZE * 0.75 * dist_pct / 100, 2)


def fetch_symbol_volume_usd(symbol: str) -> float:
    """מחזיר נפח מסחר 24h ($) עבור מטבע נתון. מחזיר 0 בשגיאה."""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker.get('quoteVolume') or 0)
    except Exception as e:
        print(f"[VOLUME_CHECK] שגיאה בשליפת נפח עבור {symbol}: {e}")
        return 0.0


def fetch_symbol_ticker_info(symbol: str) -> tuple:
    """
    מחזיר (vol_usd_24h, change_pct_24h) עבור מטבע נתון.
    מחזיר (0, 0) בשגיאה. קריאה אחת לאחסון נפח + שינוי יחד.
    """
    try:
        ticker = exchange.fetch_ticker(symbol)
        vol    = float(ticker.get('quoteVolume') or 0)
        chg    = float(ticker.get('percentage')  or 0)
        return vol, chg
    except Exception as e:
        print(f"[TICKER_INFO] שגיאה עבור {symbol}: {e}")
        return 0.0, 0.0




def track_badge(track: str) -> str:
    """אמוג'י + תווית מסלול לטלגרם."""
    return "⚡ Scalp" if track == 'Scalp' else "🌊 Swing"


def open_demo_trade(symbol, price, reason, df_3h=None,
                    direction='LONG', score=0, atr=0, timeframe='4H', tf_reason='',
                    rsi=None, ema200=None, fng_v=None,
                    ob_found=None, ob_high=None, ob_low=None, df_ob=None,
                    df_1h=None) -> bool:
    """
    פותח עסקת Swing — גודל קבוע: $20 מרג'ין, 10x, $200 נשלט.
    SL=2% | TP1=2% (→ BE אוטומטי) | TP2=4% (RR 1:2).
    מחזיר True רק אם העסקה נרשמה בפועל בארנק וברשימת העסקאות הפעילות.
    """
    # ── Daily Circuit Breaker ────────────────────────────────────────────────
    if check_daily_circuit_breaker():
        print(f"[SWING] ⛔ Circuit Breaker — לא פותחים {symbol} (הפסד יומי ≤ ${DAILY_LOSS_LIMIT})")
        return False

    # ── RegimeClose Cooldown — מניעת פינג-פונג ───────────────────────────────
    _rc_ts = regime_close_cooldown.get(symbol, 0)
    if time.time() - _rc_ts < REGIME_CLOSE_COOLDOWN_SEC:
        _rc_min = int((REGIME_CLOSE_COOLDOWN_SEC - (time.time() - _rc_ts)) / 60)
        print(f"[RegimeCooldown] {symbol} בקולדאון {_rc_min}min — skip Swing", flush=True)
        return False

    # ── נפח + שינוי 24h ───────────────────────────────────────────────────────
    vol_usd, change_24h = fetch_symbol_ticker_info(symbol)
    if vol_usd > 0 and vol_usd < SWING_TRACK_VOL_MIN:
        print(f"[SWING] נפח נמוך עבור {symbol}: ${vol_usd/1e6:.1f}M < $10M — מדלג")
        send_msg(
            f"⚠️ *נפח נמוך מדי — {symbol.replace('/USDT','')}*\n"
            f"נפח 24h: ${vol_usd/1e6:.1f}M | מינימום Swing: ${SWING_TRACK_VOL_MIN/1e6:.0f}M\n"
            f"_העסקה נדחתה — נזילות לא מספקת_"
        )
        return False

    if fng_v is None:
        fng_v, _, _ = sentiment_check("open_trade")

    # ── Market Regime Gate ────────────────────────────────────────────────────
    _allowed, _reason = is_direction_allowed(direction, context='Swing')
    if not _allowed:
        print(f"[Swing] {symbol} {direction} נדחה — {_reason}", flush=True)
        return False

    # ── Claude AI Gate ────────────────────────────────────────────────────────
    _cl_regime_sw, _, _cl_btc_sw, _ = get_market_regime()
    _cl_ok_sw, _cl_score_sw, _cl_reason_sw, _cl_risk_sw = claude_gate.claude_trade_gate(
        symbol=symbol, direction=direction, strategy='Swing',
        price=price, bot_score=int(score or 0),
        regime=_cl_regime_sw, fng=int(fng_v or 50), btc_above_ema=_cl_btc_sw,
        rsi=rsi, reason=str(reason or ''),
        daily_pnl=daily_stats.get('total_pnl', 0.0),
        open_trades=len(active_trades),
    )
    _cl_combined_sw = claude_gate.combined_score(int(score or 0), _cl_score_sw)
    # Use the same adaptive threshold as the scoring gate (e.g. 69 in BULL+Greed, not hardcoded 75)
    _eff_min_sw, _, _, _ = adaptive_threshold(fng_v or 50, _cl_regime_sw, direction)
    print(f"[ClaudeGate/Swing] {symbol} | bot={score} claude={_cl_score_sw} combined={_cl_combined_sw} eff_min={_eff_min_sw} | {_cl_reason_sw}", flush=True)
    # combined ≥ 80: strong technical signal overrides Claude's binary veto (advisory only)
    # combined eff_min–79: still require Claude approval
    # combined < eff_min: always reject
    if _cl_combined_sw < _eff_min_sw or (not _cl_ok_sw and _cl_combined_sw < 80):
        print(f"[ClaudeGate] ⛔ {symbol} Swing נדחה — combined={_cl_combined_sw} eff_min={_eff_min_sw} | {_cl_reason_sw}", flush=True)
        return False

    # ── Adaptive Exit Parameters (FNG + BTC Regime) ───────────────────────────
    _btc_regime_now          = get_btc_regime()
    _dyn_tp1, _dyn_dur, _exit_lbl = adaptive_exit_params(fng_v, _btc_regime_now)
    print(f"[AdaptiveExit] {symbol}: {_exit_lbl}", flush=True)

    # ── Fixed Sizing ──────────────────────────────────────────────────────────
    effective_margin = MARGIN        # $50
    pos_size         = POSITION_SIZE  # $500 (MARGIN × LEVERAGE)
    leverage         = LEVERAGE       # 10x

    # ── Dynamic SL/TP Targets (TP1 adapts to market conditions) ──────────────
    tgt       = se.calc_targets(price, direction, tp1_pct=_dyn_tp1)
    sl_price  = tgt['sl_price']
    tp1_price = tgt['tp1_price']
    tp_price  = tgt['tp_price']
    be_price  = tgt['be_price']   # = entry (SL moves here when TP1 hit)
    sl_pct    = tgt['sl_pct']
    tp1_pct   = tgt['tp1_pct']
    tp_pct    = tgt['tp_pct']

    # ── Wallet balance check ───────────────────────────────────────────────────
    balance_snap = wallet.get('balance', STARTING_BALANCE)
    if balance_snap < effective_margin:
        print(f"WALLET: insufficient balance (${balance_snap:.2f}) — skipping {symbol}")
        send_msg(f"⚠️ *יתרה נמוכה* — נדרש: ${effective_margin:.0f} | יש: ${balance_snap:.2f}")
        return False

    # ── Order Block ───────────────────────────────────────────────────────────
    if ob_found is None:
        ob_found = False
        _ob_df = df_ob if df_ob is not None else df_3h
        if _ob_df is not None:
            try:
                _ob_f, _ob_h, _ob_l, _ob_desc = detect_order_blocks(_ob_df, direction, lookback=50)
                if _ob_f and _ob_h > 0 and _ob_l > 0:
                    ob_found, ob_high, ob_low = True, round(_ob_h, 8), round(_ob_l, 8)
                    print(f"  [OB] {symbol} {direction}: zone [{_ob_l:.4g}–{_ob_h:.4g}] — {_ob_desc}")
            except Exception as _ob_e:
                print(f"  [OB] detection error: {_ob_e}")
    else:
        if ob_high is not None: ob_high = round(ob_high, 8)
        if ob_low  is not None: ob_low  = round(ob_low,  8)

    trade = {
        'symbol':               symbol,
        'entry':                price,
        'sl':                   sl_price,
        'tp':                   tp_price,
        'tp1':                  tp1_price,
        'be_lvl':               be_price,
        'sl_pct':               sl_pct,
        'tp_pct':               tp_pct,
        'direction':            direction,
        'phase':                'initial',
        'be_triggered':         False,
        'tp1_triggered':        False,
        'partial_25_triggered': False,
        'tp1_pnl':              0.0,
        'peak_price':           price,
        'trailing_sl':          None,
        'score':                score,
        'claude_score':         _cl_score_sw,
        'claude_reason':        _cl_reason_sw,
        'claude_key_risk':      _cl_risk_sw,
        'atr':                  round(atr, 6),
        'atr_1h':               0.0,
        'timeframe':            timeframe,
        'rsi':                  round(rsi, 2) if rsi is not None else None,
        'ema200':               round(ema200, 6) if ema200 is not None else None,
        'score_breakdown':      reason,
        'opened_at':            now_il().isoformat(timespec='seconds'),
        'pos_size':             pos_size,
        'margin':               effective_margin,
        'leverage':             leverage,
        'fng_at_entry':         fng_v,
        'fng_components':       get_fng_components(),
        'max_duration_min':     _dyn_dur,
        'track':                'Swing',
        'vol_usd':              round(vol_usd),
        'change_24h':           round(change_24h, 2),
        'slippage_pct':         0.0,
        'ob_found':             ob_found,
        'ob_high':              ob_high,
        'ob_low':               ob_low,
        'ob_type':              ('Bullish' if direction == 'LONG' else 'Bearish') if ob_found else None,
    }

    est_profit_tp  = round(abs(tp_price  - price) / price * pos_size, 2)
    est_profit_tp1 = round(abs(tp1_price - price) / price * pos_size, 2)
    est_loss_sl    = round(abs(sl_price  - price) / price * pos_size, 2)

    if not place_order(trade, effective_margin):
        print(f"[SWING] {symbol} was not opened by place_order", flush=True)
        return False

    # ── Telegram Notification ─────────────────────────────────────────────────
    ticker_base   = symbol.split('/')[0].upper()
    symbol_spaced = " ".join(list(ticker_base))
    dir_icon      = "🟢 L O N G" if direction == 'LONG' else "🔴 S H O R T"
    free_cash     = round(wallet.get('balance', 0) - effective_margin, 2)
    asset_class   = "Major" if ticker_base in MAJOR_COINS else "Altcoin"

    if not tf_reason:
        tf_reason = f"טרנד חזק ב-{timeframe}" if timeframe == '4H' else f"פריצה ב-{timeframe}"

    msg = (
        f"*{dir_icon}  |  {symbol_spaced}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 *ניקוד:* `{score}/100` | {asset_class} | FNG={fng_v} | {_btc_regime_now}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 כניסה:  `{price:.6g}`\n"
        f"🛑 SL:     `{sl_price:.6g}` (-{sl_pct:.1f}%)\n"
        f"🎯 TP1:    `{tp1_price:.6g}` (+{tp1_pct:.1f}%) → 🔒 BE auto ⚡ אדפטיבי\n"
        f"🎯 TP2:    `{tp_price:.6g}` (+{tp_pct:.1f}%)\n"
        f"⏱ MaxDur: *{_dyn_dur}min*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ סיכון: `${est_loss_sl}` | 💰 רווח(TP1): `${est_profit_tp1}` | (TP2): `${est_profit_tp}`\n"
        f"💼 {leverage}x · ${effective_margin:.0f} מרג'ין · ${pos_size:.0f} נשלט\n"
        f"💵 פנוי בארנק: `${free_cash:.2f}`"
        + (f"\n🤖 *Dr\\. Sniper:* _{_cl_reason_sw}_"
           + (f" · ⚠️ _{_cl_risk_sw}_" if _cl_risk_sw else "")
           if _cl_reason_sw else "")
    )

    if df_3h is None:
        try:
            df_3h = get_data(symbol, timeframe='1h', limit=250)
        except Exception as _fe:
            print(f"[SWING] chart fetch fallback: {_fe}")
            df_3h = None

    chart_buf = generate_chart(df_3h, symbol, price, sl_price, tp_price, direction) \
                if df_3h is not None else None
    send_chart_alert(chart_buf, symbol, msg)
    sym_short = symbol.replace('/USDT', '')
    dir_emoji = '🟢' if direction == 'LONG' else '🔴'
    send_push(
        title=f"{dir_emoji} עסקה נפתחה — {sym_short} {direction}",
        body=f"כניסה @ {price:.6g} | SL {sl_pct:.1f}% | TP {tp_pct:.1f}%",
        tag=f"open-{symbol}",
    )
    print(f"[SWING] Trade opened: {symbol} {direction} @ {price:.6g} | "
          f"SL={sl_pct:.1f}% TP1={tp1_pct:.1f}% TP2={tp_pct:.1f}% | "
          f"{leverage}x margin=${effective_margin:.0f} pos=${pos_size:.0f}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# ─── Dr. Sniper Research — execute callback (bypasses regime gate) ────────────
# ─────────────────────────────────────────────────────────────────────────────

def _open_research_trade(
    symbol:      str,
    direction:   str,
    claude_score: int,
    reason:      str,
    key_risk:    str,
) -> bool:
    """
    Execute a trade found by Dr. Sniper Research.
    Bypasses regime gate — Claude evaluated macro context holistically.
    Uses adaptive TP1/duration (same as Swing), standard sizing.
    """
    # ── Deterministic input guards ────────────────────────────────────────────
    direction = str(direction).strip().upper()
    if direction not in ("LONG", "SHORT"):
        print(f"[Research] ⛔ Invalid direction '{direction}' — abort", flush=True)
        return False
    try:
        claude_score = int(claude_score)
    except (TypeError, ValueError):
        print(f"[Research] ⛔ Non-integer score — abort", flush=True)
        return False
    if not (0 <= claude_score <= 100):
        print(f"[Research] ⛔ Score {claude_score} out of range — abort", flush=True)
        return False
    symbol = str(symbol).strip().upper()
    if not symbol.endswith("/USDT") or len(symbol) < 6:
        print(f"[Research] ⛔ Bad symbol '{symbol}' — abort", flush=True)
        return False
    reason   = str(reason).strip()[:200]
    key_risk = str(key_risk).strip()[:200]

    print(f"[Research] 🤖 Executing: {symbol} {direction} score={claude_score} | {reason}", flush=True)

    # ── Bot paused? ───────────────────────────────────────────────────────────
    if _bot_paused:
        print(f"[Research] ⛔ Bot paused — skipping execution of {symbol}", flush=True)
        return False

    # ── Circuit breakers ──────────────────────────────────────────────────────
    if check_daily_circuit_breaker():
        print(f"[Research] ⛔ Circuit Breaker — skipping {symbol}", flush=True)
        return False
    if len(active_trades) >= MAX_TRADES:
        print(f"[Research] ⛔ MAX_TRADES reached — skipping {symbol}", flush=True)
        return False

    # NOTE: No regime gate here — Claude evaluated macro context holistically
    # (FNG, BTC trend, news, TA) inside its prompt. Blocking by regime would
    # eliminate the core advantage of autonomous research: finding opportunities
    # in ALL market conditions including Bearish Regime and Extreme Fear.
    # MAX_TRADES, _bot_paused, and Circuit Breaker remain active for capital protection.
    _regime_str, _, _btc_above_ema, _ema20_val = get_market_regime()
    _fng_now = _fng_cache.get('value', 50)
    print(
        f"[Research] 🌐 Regime-free execution: {symbol} {direction} "
        f"| BTC {'above' if _btc_above_ema else 'below'} EMA20={_ema20_val:.0f} "
        f"| FNG={_fng_now} — Claude evaluated macro independently",
        flush=True,
    )

    # ── Verify symbol is an active Bitget perpetual market ────────────────────
    try:
        mkts = exchange.markets or exchange.load_markets()
        if symbol not in mkts:
            print(f"[Research] ⛔ {symbol} not in exchange markets — abort", flush=True)
            return False
        m = mkts[symbol]
        if not (m.get('active', True) and m.get('quote') == 'USDT'):
            print(f"[Research] ⛔ {symbol} not active USDT market — abort", flush=True)
            return False
    except Exception as _me:
        print(f"[Research] ⚠️  Market check failed for {symbol}: {_me} — proceeding", flush=True)

    # ── Balance ───────────────────────────────────────────────────────────────
    if wallet.get('balance', STARTING_BALANCE) < MARGIN:
        print(f"[Research] ⚠️  Insufficient balance — skipping {symbol}", flush=True)
        return False

    # ── Live price ────────────────────────────────────────────────────────────
    try:
        ticker = exchange.fetch_ticker(symbol)
        price  = ticker.get('last') or ticker.get('close')
        if not price:
            print(f"[Research] ⚠️  No price for {symbol} — abort", flush=True)
            return False
    except Exception as e:
        print(f"[Research] ⚠️  Price fetch error {symbol}: {e}", flush=True)
        return False

    fng_v = _fng_cache.get('value', 50)

    # ── Adaptive exit parameters — same as Swing ──────────────────────────────
    _btc_regime_now = get_btc_regime()
    _dyn_tp1, _dyn_dur, _exit_lbl = adaptive_exit_params(fng_v, _btc_regime_now)
    print(f"[Research/AdaptiveExit] {symbol}: {_exit_lbl}", flush=True)

    tgt       = se.calc_targets(price, direction, tp1_pct=_dyn_tp1)
    sl_price  = tgt['sl_price']
    tp1_price = tgt['tp1_price']
    tp_price  = tgt['tp_price']
    be_price  = tgt['be_price']
    sl_pct    = tgt['sl_pct']
    tp1_pct   = tgt['tp1_pct']
    tp_pct    = tgt['tp_pct']

    trade = {
        'symbol':               symbol,
        'entry':                price,
        'sl':                   sl_price,
        'tp':                   tp_price,
        'tp1':                  tp1_price,
        'be_lvl':               be_price,
        'sl_pct':               sl_pct,
        'tp_pct':               tp_pct,
        'direction':            direction,
        'phase':                'initial',
        'be_triggered':         False,
        'tp1_triggered':        False,
        'partial_25_triggered': False,
        'tp1_pnl':              0.0,
        'peak_price':           price,
        'trailing_sl':          None,
        'score':                0,
        'claude_score':         claude_score,
        'claude_reason':        reason,
        'claude_key_risk':      key_risk,
        'atr':                  0.0,
        'atr_1h':               0.0,
        'timeframe':            'Research',
        'rsi':                  None,
        'ema200':               None,
        'score_breakdown':      reason,
        'opened_at':            now_il().isoformat(timespec='seconds'),
        'pos_size':             POSITION_SIZE,
        'margin':               MARGIN,
        'leverage':             LEVERAGE,
        'fng_at_entry':         fng_v,
        'max_duration_min':     _dyn_dur,
        'track':                'Research',
        'vol_usd':              0,
        'change_24h':           0.0,
        'slippage_pct':         0.0,
        'ob_found':             False,
        'ob_high':              None,
        'ob_low':               None,
        'ob_type':              None,
    }

    success = place_order(trade, MARGIN)
    if not success:
        return False

    # Telegram notification
    dir_icon  = "🟢 L O N G" if direction == 'LONG' else "🔴 S H O R T"
    name      = symbol.replace('/USDT', '')
    spaced    = " ".join(list(name))
    free_cash = round(wallet.get('balance', 0), 2)
    est_loss  = round(abs(sl_price  - price) / price * POSITION_SIZE, 2)
    est_tp1   = round(abs(tp1_price - price) / price * POSITION_SIZE, 2)
    est_tp2   = round(abs(tp_price  - price) / price * POSITION_SIZE, 2)

    msg = (
        f"*{dir_icon}  |  {spaced}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔬 *Dr\\. Sniper Research* — score `{claude_score}/100`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 כניסה:  `{price:.6g}`\n"
        f"🛑 SL:     `{sl_price:.6g}` \\(-{sl_pct:.1f}%\\)\n"
        f"🎯 TP1:    `{tp1_price:.6g}` \\(+{tp1_pct:.1f}%\\) → 🔒 BE auto\n"
        f"🎯 TP2:    `{tp_price:.6g}` \\(+{tp_pct:.1f}%\\)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ סיכון: `${est_loss}` | 💰 TP1: `${est_tp1}` | TP2: `${est_tp2}`\n"
        f"💼 {LEVERAGE}x · ${MARGIN:.0f} מרג'ין · ${POSITION_SIZE:.0f} נשלט\n"
        f"💵 פנוי: `${free_cash:.2f}`\n"
        f"🤖 _{reason}_"
        + (f"\n⚠️ _{key_risk}_" if key_risk else "")
    )
    send_msg(msg)
    dir_emoji = '🟢' if direction == 'LONG' else '🔴'
    send_push(
        title=f"{dir_emoji} Research Trade — {name} {direction}",
        body=f"Dr. Sniper | @ {price:.6g} | score={claude_score}",
        tag=f"open-{symbol}",
    )
    return True


# ─────────────────────────────────────────────────────────────────────────────
# ─── RSI Reversal Guard — cache + helper ─────────────────────────────────────
_rsi15_cache: dict = {}   # {sym: {'rsi': float, 'ts': float}}

def _get_rsi15m(sym: str) -> float | None:
    """RSI(14) על TF 15m עם cache לפי RSI_REVERSAL_CACHE_TTL שניות."""
    now_ts = time.time()
    cached = _rsi15_cache.get(sym)
    if cached and now_ts - cached['ts'] < RSI_REVERSAL_CACHE_TTL:
        return cached['rsi']
    try:
        df      = get_data(sym, timeframe='15m', limit=20)
        closes  = df['close'].tolist()
        deltas  = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains   = [max(d, 0) for d in deltas]
        losses  = [abs(min(d, 0)) for d in deltas]
        period  = 14
        avg_g   = sum(gains[:period]) / period
        avg_l   = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        rsi = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
        rsi = round(rsi, 1)
        _rsi15_cache[sym] = {'rsi': rsi, 'ts': now_ts}
        return rsi
    except Exception as _e:
        print(f"[RSIGuard] fetch error {sym}: {_e}", flush=True)
        return None

# Momentum Exhaustion Exit
# ─────────────────────────────────────────────────────────────────────────────
EXHAUSTION_MIN_PROFIT_USD = 15.0   # מינימום רווח צף לפני בדיקה
EXHAUSTION_RSI_PERIOD     = 14     # תקופת RSI
EXHAUSTION_VOL_BARS       = 15     # בארים לממוצע נפח
EXHAUSTION_VOL_SPIKE_X    = 2.2    # כפולה של ממוצע = spike מוסדי


def check_momentum_exhaustion(trade: dict, current_price: float) -> tuple[bool, str]:
    """
    בודק סימני תשישות מומנטום על צ'ארט 5M לנעילת רווח מקסימלי.
    מופעל רק כאשר רווח צף ≥ $15 — מונע יציאה מוקדמת על תנועות קטנות.

    SHORT: RSI קפץ מ-oversold (prev<22 → curr>24.5) + volume spike = תחתית
    LONG:  RSI נפל מ-overbought (prev>78 → curr<75.5) + volume spike = פסגה

    Returns: (should_close: bool, reason_str: str)
    """
    sym       = trade['symbol']
    direction = trade.get('direction', 'LONG')
    entry     = trade['entry']
    pos_size  = trade.get('pos_size', POSITION_SIZE)

    # ── שער: מינימום רווח ────────────────────────────────────────────────
    raw_pct      = (current_price - entry) / entry * 100
    floating_pnl = pos_size * (raw_pct if direction == 'LONG' else -raw_pct) / 100
    if floating_pnl < EXHAUSTION_MIN_PROFIT_USD:
        return False, ''

    # ── משיכת 5M OHLCV ───────────────────────────────────────────────────
    try:
        ohlcv = exchange.fetch_ohlcv(sym, '5m', limit=42)
        if not ohlcv or len(ohlcv) < EXHAUSTION_RSI_PERIOD + 3:
            return False, ''
    except Exception as _exh_e:
        print(f"[Exhaustion] OHLCV fetch error {sym}: {_exh_e}")
        return False, ''

    closes  = [float(c[4]) for c in ohlcv]
    volumes = [float(c[5]) for c in ohlcv]

    # ── Wilder RSI ────────────────────────────────────────────────────────
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_g  = sum(gains[:EXHAUSTION_RSI_PERIOD]) / EXHAUSTION_RSI_PERIOD
    avg_l  = sum(losses[:EXHAUSTION_RSI_PERIOD]) / EXHAUSTION_RSI_PERIOD
    rsi_series: list[float] = []
    for _i in range(EXHAUSTION_RSI_PERIOD, len(deltas)):
        avg_g = (avg_g * (EXHAUSTION_RSI_PERIOD - 1) + gains[_i]) / EXHAUSTION_RSI_PERIOD
        avg_l = (avg_l * (EXHAUSTION_RSI_PERIOD - 1) + losses[_i]) / EXHAUSTION_RSI_PERIOD
        rsi_series.append(100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l))
    if len(rsi_series) < 2:
        return False, ''
    curr_rsi = rsi_series[-1]
    prev_rsi = rsi_series[-2]

    # ── Volume Spike (רק נרות סגורים) ────────────────────────────────────
    completed_vols = volumes[:-1]          # מוציא את הנר הנוכחי שנסגר חלקית
    if len(completed_vols) < EXHAUSTION_VOL_BARS + 1:
        return False, ''
    avg_vol   = sum(completed_vols[-(EXHAUSTION_VOL_BARS + 1):-1]) / EXHAUSTION_VOL_BARS
    last_vol  = completed_vols[-1]
    vol_ratio = (last_vol / avg_vol) if avg_vol > 0 else 0.0
    vol_spike = vol_ratio >= EXHAUSTION_VOL_SPIKE_X

    # ── סיגנל ────────────────────────────────────────────────────────────
    if direction == 'SHORT':
        triggered = prev_rsi < 22 and curr_rsi > 24.5 and vol_spike
        reason    = (f"SHORT bottom — RSI {prev_rsi:.1f}→{curr_rsi:.1f} "
                     f"vol×{vol_ratio:.1f} | P&L≈${floating_pnl:.1f}")
    else:
        triggered = prev_rsi > 78 and curr_rsi < 75.5 and vol_spike
        reason    = (f"LONG top — RSI {prev_rsi:.1f}→{curr_rsi:.1f} "
                     f"vol×{vol_ratio:.1f} | P&L≈${floating_pnl:.1f}")

    if triggered:
        print(f"[Exhaustion] 🔄 {sym} {direction} reversal: {reason}", flush=True)
    return triggered, reason


def track_trades():
    """
    בודק כל עסקה פעילה כל 60 שניות.
    תומך ב-LONG וב-SHORT.
    """
    global active_trades, daily_stats, _daily_circuit_notified

    if daily_stats['date'] != now_il().date():
        daily_stats = {
            'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'date': now_il().date(),
            'close_reasons': {'TP': 0, 'TP1+Trail': 0, 'Trailing': 0, 'SL': 0, 'BE': 0, 'Manual': 0},
        }
        _daily_circuit_notified = False  # איפוס Circuit Breaker עם פתיחת יום חדש

    # FNG once per manage cycle (avoid spamming API/log)
    fng_v_mgr, _, _ = sentiment_check("manage_risk")

    # ── Pre-fetch all prices in ONE batch call (outside any lock) ──────────
    # Replaces N sequential fetch_ticker() calls (2-3s each) with a single
    # fetch_tickers() batch request — drastically shorter loop duration and
    # far less time where trades_lock branches can block Flask/Telegram threads.
    _tracked_syms   = list({t['symbol'] for t in active_trades[:]})
    _batch_prices: dict[str, float] = {}
    if _tracked_syms:
        try:
            _bt = exchange.fetch_tickers(_tracked_syms)
            for _s, _tk in _bt.items():
                if _tk.get('last'):
                    _batch_prices[_s] = float(_tk['last'])
        except Exception as _bpe:
            print(f"[TRACK] batch fetch_tickers failed ({_bpe}), will fallback per-symbol", flush=True)

    # ── Emergency Regime Protection — auto-close LONGs when market turns BEARISH ──
    # Fires every 60 s; get_market_regime() is cached (5 min TTL) — essentially free.
    # CLOSE uses AND logic: both FNG *and* BTC must be bearish to force-close open positions.
    # ENTRY gate (is_direction_allowed) keeps the strict OR logic for blocking new entries.
    try:
        _emrg_regime, _emrg_fng, _emrg_btc_above, _emrg_ema = get_market_regime()
        _close_bearish = _emrg_fng < REGIME_BEARISH_FNG and not _emrg_btc_above
        _close_bullish = _emrg_fng > REGIME_BULLISH_FNG and _emrg_btc_above
        if _close_bearish:
            with trades_lock:
                _longs_to_close = [t for t in active_trades if t.get('direction', 'LONG') == 'LONG']
            for _et in _longs_to_close:
                try:
                    _esym  = _et['symbol']
                    # ── Grace Period — מגן על עסקות שנפתחו לפני פחות מ-3 דקות ──────
                    try:
                        _grace_opened = datetime.fromisoformat(_et.get('opened_at', '')).timestamp()
                    except Exception:
                        _grace_opened = 0
                    if time.time() - _grace_opened < 3 * 60:
                        print(f"[RegimeClose] {_esym} בgrace period ({(time.time()-_grace_opened)/60:.1f}min < 3min) — skip", flush=True)
                        continue
                    _ep    = (_batch_prices.get(_esym)
                              or float(exchange.fetch_ticker(_esym)['last']))
                    _eps   = _remaining_notional(_et)
                    _eraw  = (_ep - _et['entry']) / _et['entry'] * 100
                    _epnl  = round(_eps * _eraw / 100, 2)
                    _elev  = _et.get('leverage', LEVERAGE)
                    _eret  = round(_eraw * _elev, 1)
                    _eicon = "📈" if _epnl >= 0 else "📉"
                    _ebtc  = f"{'מעל' if _emrg_btc_above else 'מתחת'} EMA20={_emrg_ema:.0f}"
                    wallet_credit(_epnl, _et.get('margin', MARGIN))
                    _erecord = _log_closed_trade(_et, 'RegimeClose', _epnl, _ep)
                    add_daily_pnl(_epnl)
                    if _epnl >= 0:
                        daily_stats['wins'] += 1
                    else:
                        daily_stats['losses'] += 1
                    daily_stats['close_reasons']['RegimeClose'] = (
                        daily_stats['close_reasons'].get('RegimeClose', 0) + 1)
                    with trades_lock:
                        if _et in active_trades:
                            active_trades.remove(_et)
                    regime_close_cooldown[_esym] = time.time()   # 15 דקות קולדאון
                    save_active_trades()
                    _eeq = _get_equity()
                    send_msg(
                        f"🛡 *Emergency Close — {_esym.replace('/USDT','')}* 🔴\n"
                        f"_שוק הפך BEARISH — סגירה אוטומטית להגנה על הון_\n"
                        f"FNG={_emrg_fng} | BTC {_ebtc}\n\n"
                        f"כניסה: `{_et['entry']:.6g}` → יציאה: `{_ep:.6g}`\n"
                        f"ברוטו: `${_erecord['gross_pnl_usd']:+.2f}` | עמלה: `${_erecord['fees_usd']:.2f}`\n"
                        f"{_eicon} *נטו: ${_erecord['net_pnl_usd']:+.2f}* ({_eret:+.1f}% מרג'ין)\n"
                        f"💼 Equity: `${_eeq:.2f}`"
                    )
                    print(
                        f"[EmergencyClose] 🛡 {_esym} LONG → closed at {_ep:.6g} "
                        f"pnl={_epnl:+.2f} | BEARISH FNG={_emrg_fng} BTC {_ebtc}",
                        flush=True
                    )
                except Exception as _ece:
                    print(f"[EmergencyClose] error closing {_et.get('symbol')}: {_ece}", flush=True)

        elif _close_bullish:
            with trades_lock:
                _shorts_to_close = [t for t in active_trades if t.get('direction') == 'SHORT']
            for _et in _shorts_to_close:
                try:
                    _esym  = _et['symbol']
                    # ── Grace Period — מגן על עסקות שנפתחו לפני פחות מ-3 דקות ──────
                    try:
                        _grace_opened = datetime.fromisoformat(_et.get('opened_at', '')).timestamp()
                    except Exception:
                        _grace_opened = 0
                    if time.time() - _grace_opened < 3 * 60:
                        print(f"[RegimeClose] {_esym} בgrace period ({(time.time()-_grace_opened)/60:.1f}min < 3min) — skip", flush=True)
                        continue
                    _ep    = (_batch_prices.get(_esym)
                              or float(exchange.fetch_ticker(_esym)['last']))
                    _eps   = _remaining_notional(_et)
                    _eraw  = (_et['entry'] - _ep) / _et['entry'] * 100   # SHORT P&L
                    _epnl  = round(_eps * _eraw / 100, 2)
                    _elev  = _et.get('leverage', LEVERAGE)
                    _eret  = round(_eraw * _elev, 1)
                    _eicon = "📈" if _epnl >= 0 else "📉"
                    _ebtc  = f"מעל EMA20={_emrg_ema:.0f}"
                    wallet_credit(_epnl, _et.get('margin', MARGIN))
                    _erecord = _log_closed_trade(_et, 'RegimeClose', _epnl, _ep)
                    add_daily_pnl(_epnl)
                    if _epnl >= 0:
                        daily_stats['wins'] += 1
                    else:
                        daily_stats['losses'] += 1
                    daily_stats['close_reasons']['RegimeClose'] = (
                        daily_stats['close_reasons'].get('RegimeClose', 0) + 1)
                    with trades_lock:
                        if _et in active_trades:
                            active_trades.remove(_et)
                    regime_close_cooldown[_esym] = time.time()   # 15 דקות קולדאון
                    save_active_trades()
                    _eeq = _get_equity()
                    send_msg(
                        f"🛡 *Emergency Close — {_esym.replace('/USDT','')}* 🟢\n"
                        f"_שוק הפך BULLISH — סגירה אוטומטית להגנה על הון_\n"
                        f"FNG={_emrg_fng} | BTC {_ebtc}\n\n"
                        f"כניסה: `{_et['entry']:.6g}` → יציאה: `{_ep:.6g}`\n"
                        f"ברוטו: `${_erecord['gross_pnl_usd']:+.2f}` | עמלה: `${_erecord['fees_usd']:.2f}`\n"
                        f"{_eicon} *נטו: ${_erecord['net_pnl_usd']:+.2f}* ({_eret:+.1f}% מרג'ין)\n"
                        f"💼 Equity: `${_eeq:.2f}`"
                    )
                    print(
                        f"[EmergencyClose] 🛡 {_esym} SHORT → closed at {_ep:.6g} "
                        f"pnl={_epnl:+.2f} | BULLISH FNG={_emrg_fng} BTC {_ebtc}",
                        flush=True
                    )
                except Exception as _ece:
                    print(f"[EmergencyClose] error closing {_et.get('symbol')}: {_ece}", flush=True)
    except Exception as _emrg_err:
        print(f"[EmergencyClose] regime check error: {_emrg_err}", flush=True)

    for trade in active_trades[:]:
        try:
            # Older/Breakout trade records may not contain lifecycle metadata.
            # Treat them as a normal initial-phase position so SL/TP monitoring
            # remains active instead of skipping the trade with KeyError.
            trade.setdefault('phase', 'initial')
            sym_key = trade['symbol']
            if sym_key in _batch_prices:
                current_price = _batch_prices[sym_key]
            else:
                # Fallback: individual call for symbols not in batch result
                current_price = exchange.fetch_ticker(sym_key)['last']
            trade['current_price'] = current_price   # שמור לדאשבורד (Floating P&L)
            entry         = trade['entry']
            sym           = trade['symbol']
            direction     = trade.get('direction', 'LONG')
            pos_size      = trade.get('pos_size', POSITION_SIZE)  # per-trade position size
            tp1_close     = pos_size * 0.75                        # 75% נסגר ב-TP1
            remaining     = pos_size * 0.25                        # 25% נשאר לאחר TP1
            tbadge        = track_badge(trade.get('track', 'Swing'))  # ⚡ Scalp / 🌊 Swing
            t_leverage    = trade.get('leverage', LEVERAGE)

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
            # SCALP PHASE — SL / TP / Time exit
            # ════════════════════════════════════════════
            if trade.get('phase') == 'scalp':
                scalp_pos   = trade.get('pos_size', POSITION_SIZE)   # גודל מהעסקה ($500)
                scalp_mar   = trade.get('margin',   MARGIN)           # מרג'ין ($50)
                scalp_lev   = trade.get('leverage', LEVERAGE)         # מינוף (10x)
                scalp_sl_p  = trade.get('sl_pct',   SCALP_SL_PCT)
                scalp_tp_p  = trade.get('tp_pct',   SCALP_TP_PCT)

                raw_pnl_pct = (current_price - entry) / entry * 100
                scalp_pnl_pct = raw_pnl_pct if direction == 'LONG' else -raw_pnl_pct
                scalp_pnl_usd = round(scalp_pos * scalp_pnl_pct / 100, 2)

                elapsed_min = (time.time() - trade.get('scalp_opened_ts', time.time())) / 60

                tp_hit_s = (direction == 'LONG' and current_price >= trade['tp']) or \
                           (direction == 'SHORT' and current_price <= trade['tp'])
                sl_hit_s = (direction == 'LONG' and current_price <= trade['sl']) or \
                           (direction == 'SHORT' and current_price >= trade['sl'])
                time_exp = elapsed_min >= SCALP_MAX_DURATION_MIN

                # ── Scalp BE: 50% of way to TP1 ──────────────────────────
                if not trade.get('be_triggered') and trade.get('be_lvl'):
                    be_reached_s = ((direction == 'LONG' and current_price >= trade['be_lvl']) or
                                    (direction == 'SHORT' and current_price <= trade['be_lvl']))
                    if be_reached_s:
                        trade['sl']          = entry   # SL → Breakeven
                        trade['be_triggered'] = True
                        be_trigger_pct = round(scalp_tp_p * SCALP_TRACK_BE_TRIGGER, 2)
                        print(f"  [SCALP BE] {sym}: SL→BE @ {current_price:.6g} (+{be_trigger_pct}% = 50% to TP1)")
                        send_msg(
                            f"🔒 *Scalp BE הופעל — {sym.replace('/USDT','')}*\n"
                            f"מחיר: `{current_price:.6g}` (+{be_trigger_pct}% — 50% of way to TP1)\n"
                            f"SL הועבר לכניסה: `{entry:.6g}` | {tbadge}\n"
                            f"💼 {scalp_lev}x · ההון מוגן!"
                        )

                if tp_hit_s or sl_hit_s or time_exp:
                    if tp_hit_s:
                        close_reason = 'Scalp-TP'
                        icon  = "✅"
                        label = f"🎯 TP נגע (+{scalp_tp_p}%)"
                        daily_stats['wins'] += 1
                    elif sl_hit_s:
                        close_reason = 'Scalp-SL'
                        icon  = "❌"
                        be_note = " (BE)" if trade.get('be_triggered') else ""
                        label = f"🛑 SL נגע (-{scalp_sl_p}%){be_note}"
                        daily_stats['losses'] += 1
                    else:
                        close_reason = 'Scalp-Time'
                        icon  = "⏱"
                        label = f"⏱ פג תוקף ({elapsed_min:.0f} דקות)"
                        if scalp_pnl_usd >= 0:
                            daily_stats['wins'] += 1
                        else:
                            daily_stats['losses'] += 1

                    slip = trade.get('slippage_pct', 0.0)
                    add_daily_pnl(scalp_pnl_usd)
                    wallet_credit(scalp_pnl_usd, scalp_mar)
                    _log_closed_trade(trade, close_reason, scalp_pnl_usd, current_price)
                    eq    = _get_equity()
                    emoji = "🟢" if direction == 'LONG' else "🔴"
                    pnl_icon = "📈" if scalp_pnl_usd >= 0 else "📉"
                    send_msg(
                        f"⚡ *Scalp סגור — {sym.replace('/USDT','')} {emoji}*\n"
                        f"{label} | {tbadge}\n\n"
                        f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
                        f"{pnl_icon} *P&L: ${scalp_pnl_usd:+.2f}* ({scalp_pnl_pct:+.2f}%)\n"
                        f"💼 {scalp_lev}x · ${scalp_mar:.0f} מרג'ין · ${scalp_pos:.0f} נשלט\n"
                        f"📊 Slippage: {slip:.2f}% (Demo)\n"
                        f"📊 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance', 0):.2f}`\n"
                        f"{pnl_icon} סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()
                continue   # skip all normal phase logic for scalp trades

            # ════════════════════════════════════════════
            # CLIFF PHASE — High-Velocity (Rocket LONG / Cliff SHORT)
            # 1% Trailing Stop · BE@1.5% · TP 3% · Max 30 min
            # ════════════════════════════════════════════
            if trade.get('phase') == 'cliff':
                raw_pnl_pct   = (current_price - entry) / entry * 100
                cliff_pnl_pct = raw_pnl_pct if direction == 'LONG' else -raw_pnl_pct
                cliff_pnl_usd = round(CLIFF_POS_SIZE * cliff_pnl_pct / 100, 2)
                elapsed_min   = (time.time() - trade.get('cliff_opened_ts', time.time())) / 60

                # ── 1. עדכון Trailing SL (1% מהשיא/שפל) ─────────────────
                if direction == 'LONG':
                    if current_price > trade.get('peak_price', entry):
                        trade['peak_price'] = current_price
                    new_trail = round(trade['peak_price'] * (1 - CLIFF_TRAIL_PCT / 100), 8)
                    if new_trail > trade['sl']:
                        trade['sl'] = new_trail
                else:  # SHORT
                    if current_price < trade.get('peak_price', entry):
                        trade['peak_price'] = current_price
                    new_trail = round(trade['peak_price'] * (1 + CLIFF_TRAIL_PCT / 100), 8)
                    if new_trail < trade['sl']:
                        trade['sl'] = new_trail

                # ── 2. Break-Even trigger (1.5% רווח) ────────────────────
                be_reached = (direction == 'LONG' and current_price >= trade['be_lvl']) or \
                             (direction == 'SHORT' and current_price <= trade['be_lvl'])
                if not trade.get('be_triggered') and be_reached:
                    trade['be_triggered'] = True
                    send_msg(
                        f"📍 *Velocity BE הופעל — {sym.replace('/USDT','')}*\n"
                        f"רווח {CLIFF_BE_TRIGGER_PCT}% הושג — Trailing SL פעיל\n"
                        f"_מסחר ללא סיכון מעכשיו_"
                    )

                # ── 3. בדיקת יציאה ───────────────────────────────────────
                tp_hit_c  = (direction == 'LONG' and current_price >= trade['tp']) or \
                             (direction == 'SHORT' and current_price <= trade['tp'])
                sl_hit_c  = (direction == 'LONG' and current_price <= trade['sl']) or \
                             (direction == 'SHORT' and current_price >= trade['sl'])
                time_exp_c = elapsed_min >= CLIFF_MAX_DURATION_MIN

                if tp_hit_c or sl_hit_c or time_exp_c:
                    dir_sign = '+' if direction == 'LONG' else '-'
                    if tp_hit_c:
                        close_reason = 'Velocity-TP'
                        icon  = "✅"
                        label = f"🎯 TP נגע \\({dir_sign}{CLIFF_TP_PCT}%\\)"
                        daily_stats['wins'] += 1
                    elif sl_hit_c:
                        be_txt = " \\(Trailing\\-BE\\)" if trade.get('be_triggered') else " \\(Trailing\\-SL\\)"
                        close_reason = 'Velocity-SL'
                        icon  = "🔁" if trade.get('be_triggered') else "❌"
                        label = f"🛑 Trailing SL נגע{be_txt}"
                        daily_stats['losses'] += 1
                    else:
                        close_reason = 'Velocity-Time'
                        icon  = "⏱"
                        label = f"⏱ פג תוקף \\({elapsed_min:.0f} דקות\\)"
                        if cliff_pnl_usd >= 0:
                            daily_stats['wins'] += 1
                        else:
                            daily_stats['losses'] += 1

                    cliff_mar_t = trade.get('margin', CLIFF_MARGIN)
                    slip = trade.get('slippage_pct', 0.0)
                    add_daily_pnl(cliff_pnl_usd)
                    wallet_credit(cliff_pnl_usd, cliff_mar_t)
                    _log_closed_trade(trade, close_reason, cliff_pnl_usd, current_price)
                    eq       = _get_equity()
                    emoji_d  = "🟢" if direction == 'LONG' else "🔴"
                    trade_lbl = "Rocket 🚀" if direction == 'LONG' else "Cliff 🪂"
                    pnl_icon  = "📈" if cliff_pnl_usd >= 0 else "📉"
                    send_msg(
                        f"⚡ *Velocity {trade_lbl} סגור — {sym.replace('/USDT','')} {emoji_d}*\n"
                        f"{label} | {tbadge}\n\n"
                        f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
                        f"{pnl_icon} *P&L: ${cliff_pnl_usd:+.2f}* ({cliff_pnl_pct:+.2f}%)\n"
                        f"💼 {CLIFF_LEVERAGE}x · ${cliff_mar_t:.0f} מרג'ין\n"
                        f"📊 Slippage: {slip:.2f}% (Demo)\n"
                        f"📊 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance', 0):.2f}`\n"
                        f"{pnl_icon} סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()
                continue   # skip normal phase logic for cliff trades

            # ════════════════════════════════════════════
            # שלב INITIAL — פוזיציה מלאה $500
            # ════════════════════════════════════════════
            if trade['phase'] == 'initial':

                # ── Momentum Exhaustion Exit ─────────────────────────────────────────
                # בדיקה ראשונה — לפני Stagnation/SL/TP. מנעל רווח כשיש היפוך 5M מאושר.
                try:
                    _exh_close, _exh_reason = check_momentum_exhaustion(trade, current_price)
                    if _exh_close:
                        _exh_raw_pct = (current_price - entry) / entry * 100
                        _exh_pnl_pct = _exh_raw_pct if direction == 'LONG' else -_exh_raw_pct
                        _exh_pnl     = round(pos_size * _exh_pnl_pct / 100, 2)
                        _exh_ret     = round(_exh_pnl_pct * t_leverage, 1)
                        daily_stats['wins'] += 1
                        add_daily_pnl(_exh_pnl)
                        daily_stats['close_reasons']['Exhaustion'] = (
                            daily_stats['close_reasons'].get('Exhaustion', 0) + 1)
                        wallet_credit(_exh_pnl, trade.get('margin', MARGIN))
                        _log_closed_trade(trade, 'Exhaustion', _exh_pnl, current_price)
                        slip = trade.get('slippage_pct', 0.0)
                        eq   = _get_equity()
                        send_msg(
                            f"🔄 *Exhaustion Exit — {sym.replace('/USDT', '')}* "
                            f"{'🟢' if direction == 'LONG' else '🔴'}\n"
                            f"_היפוך מגמה זוהה — ננעל הרווח המקסימלי_\n\n"
                            f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
                            f"📈 *P&L: ${_exh_pnl:+.2f}* ({_exh_ret:+.1f}% מרג'ין)\n"
                            f"🔍 {_exh_reason}\n"
                            f"💼 {t_leverage}x · ${trade.get('margin', MARGIN):.0f} מרג'ין | {tbadge}\n"
                            f"📊 Slippage: {slip:.2f}% (Demo)\n"
                            f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance', 0):.2f}`\n"
                            f"📈 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                        )
                        with trades_lock:
                            active_trades.remove(trade)
                        save_active_trades()
                        continue
                except Exception as _exh_err:
                    print(f"[Exhaustion] check error {sym}: {_exh_err}")

                # ── 0a. STAGNATION EXIT — אם תזת המומנטום לא התממשה ב-4 שעות ──────
                # רלוונטי רק ל-Breakout/SOL/Main (לא Scalp/Cliff שיש להם timeout משלהם)
                if not trade.get('scalp') and not trade.get('cliff'):
                    try:
                        opened_dt   = datetime.fromisoformat(trade.get('opened_at', now_il().isoformat()))
                        elapsed_h   = (now_il() - opened_dt).total_seconds() / 3600
                        price_drift = abs(current_price - entry) / entry * 100  # % תנועה מהכניסה
                        # עסקה ברווח — סף זמן גבוה יותר (24h) כדי לא לחתוך זוכים מוקדם
                        _raw_pnl_sign = (current_price > entry) if direction == 'LONG' else (current_price < entry)
                        stag_min_h = STAGNATION_PROFIT_MIN_HOURS if _raw_pnl_sign else STAGNATION_MIN_HOURS
                        is_stagnant = (
                            elapsed_h  >= stag_min_h and
                            price_drift <= STAGNATION_RANGE_PCT
                        )
                        if is_stagnant:
                            # חישוב P&L אמיתי
                            sign       = 1 if profit_dir(current_price) else -1
                            stag_pnl   = round(sign * pos_size * price_drift / 100, 2)
                            stag_pnl_r = round(sign * price_drift * t_leverage, 1)
                            pnl_icon   = "📈" if stag_pnl >= 0 else "📉"
                            add_daily_pnl(stag_pnl)
                            if stag_pnl >= 0:
                                daily_stats['wins'] += 1
                            else:
                                daily_stats['losses'] += 1
                            daily_stats['close_reasons']['Stagnation'] = \
                                daily_stats['close_reasons'].get('Stagnation', 0) + 1
                            wallet_credit(stag_pnl, trade.get('margin', MARGIN))
                            _log_closed_trade(trade, 'Stagnation', stag_pnl, current_price)
                            eq   = _get_equity()
                            slip = trade.get('slippage_pct', 0.0)
                            send_msg(
                                f"⏳ *Stagnation Exit — {sym}* {('🟢' if direction=='LONG' else '🔴')}\n"
                                f"_תזת המומנטום לא התממשה — יוצאים אוטומטית_\n\n"
                                f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
                                f"⏱ פתוח: *{elapsed_h:.1f} שעות* | תנועה: *{price_drift:.2f}%* (< {STAGNATION_RANGE_PCT}%)\n"
                                f"{pnl_icon} *P&L: ${stag_pnl:+.2f}* ({stag_pnl_r:+.1f}% על מרג'ין)\n"
                                f"💼 {t_leverage}x · ${trade.get('margin', MARGIN):.0f} מרג'ין | {tbadge}\n"
                                f"📊 Slippage: {slip:.2f}% (Demo)\n"
                                f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance', 0):.2f}`\n"
                                f"📉 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                            )
                            print(f"[Stagnation] ⏳ {sym} {direction} — {elapsed_h:.1f}h, drift {price_drift:.2f}% → exit P&L=${stag_pnl:+.2f}")
                            with trades_lock:
                                active_trades.remove(trade)
                            save_active_trades()
                            continue
                    except Exception as _e:
                        print(f"[Stagnation] error {sym}: {_e}")

                # ── 0a-2. FAST-LOSS PROTECTION — 5% הפסד מרג'ין בתוך 15 דקות ───────
                # מזהה setup שנכשל מהר: יוצאים ב-~$2.50 במקום לחכות ל-SL מלא ($10)
                if not trade.get('scalp') and not trade.get('cliff'):
                    try:
                        _fl_opened  = datetime.fromisoformat(trade.get('opened_at', now_il().isoformat()))
                        _fl_elapsed = (now_il() - _fl_opened).total_seconds() / 60
                        _fl_raw_pct = (current_price - entry) / entry * 100 \
                                      if direction == 'LONG' \
                                      else (entry - current_price) / entry * 100
                        _fl_loss_margin = -_fl_raw_pct * t_leverage  # % הפסד על מרג'ין
                        # FastLoss פועל רק כל עוד SL עדיין לא נחצה —
                        # pump חד שחוצה SL ב-60 שניות יטופל ע"י SL הרגיל ($10), לא FastLoss ($24)
                        _fl_sl_ok = (current_price > trade['sl'] if direction == 'LONG'
                                     else current_price < trade['sl'])
                        if _fl_elapsed < FAST_LOSS_MINUTES and _fl_loss_margin >= FAST_LOSS_MARGIN_PCT and _fl_sl_ok:
                            _fl_pnl  = round(pos_size * _fl_raw_pct / 100, 2)
                            _fl_ret  = round(_fl_raw_pct * t_leverage, 1)
                            add_daily_pnl(_fl_pnl)
                            daily_stats['losses'] += 1
                            daily_stats['close_reasons']['FastLoss'] = \
                                daily_stats['close_reasons'].get('FastLoss', 0) + 1
                            wallet_credit(_fl_pnl, trade.get('margin', MARGIN))
                            _log_closed_trade(trade, 'FastLoss', _fl_pnl, current_price)
                            eq   = _get_equity()
                            slip = trade.get('slippage_pct', 0.0)
                            send_msg(
                                f"⚡ *Fast-Loss Exit — {sym.replace('/USDT','')}* "
                                f"{'🟢' if direction=='LONG' else '🔴'}\n"
                                f"_Setup נכשל: -{_fl_loss_margin:.1f}% מרג'ין בתוך {_fl_elapsed:.0f} דקות_\n\n"
                                f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
                                f"📉 *P&L: ${_fl_pnl:+.2f}* ({_fl_ret:+.1f}% על מרג'ין)\n"
                                f"💼 {t_leverage}x · ${trade.get('margin', MARGIN):.0f} מרג'ין | {tbadge}\n"
                                f"📊 Slippage: {slip:.2f}% (Demo)\n"
                                f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance', 0):.2f}`\n"
                                f"📉 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                            )
                            print(
                                f"[FastLoss] ⚡ {sym} {direction} — "
                                f"{_fl_elapsed:.0f}min | margin_loss={_fl_loss_margin:.1f}% "
                                f"→ exit P&L=${_fl_pnl:+.2f}",
                                flush=True
                            )
                            trade_close_cooldown[sym] = time.time()   # 2h cooldown — מניעת כניסה מחדש
                            with trades_lock:
                                active_trades.remove(trade)
                            save_active_trades()
                            continue
                    except Exception as _fl_err:
                        print(f"[FastLoss] error {sym}: {_fl_err}", flush=True)

                # ── 0a-2b. RSI REVERSAL GUARD — היפוך מומנטום 15m ──────────────────
                # SHORT בהפסד + RSI_15m > 62 → קנייה חזקה → נסגור לפני SL ($10)
                # LONG  בהפסד + RSI_15m < 38 → מכירה חזקה → נסגור לפני SL ($10)
                # פעיל רק אחרי 15 דק׳ (FastLoss מטפל בחלון 0-15 דק׳)
                # ReversalGuard פועל רק כל עוד SL עדיין לא נחצה —
                # אם המחיר כבר עבר את ה-SL (gap/pump חד), ה-SL הרגיל יטפל בזה
                # ולא ניתן ל-ReversalGuard "לעקוף" אותו בלי תקרת הפסד.
                if not trade.get('scalp') and not trade.get('cliff') and not trade.get('be_triggered'):
                    try:
                        _rg_opened  = datetime.fromisoformat(trade.get('opened_at', now_il().isoformat()))
                        _rg_elapsed = (now_il() - _rg_opened).total_seconds() / 60
                        _rg_raw_pct = (current_price - entry) / entry * 100 \
                                      if direction == 'LONG' \
                                      else (entry - current_price) / entry * 100
                        _rg_loss_pct = -_rg_raw_pct   # חיובי = בהפסד
                        _rg_sl_ok = (current_price > trade['sl'] if direction == 'LONG'
                                     else current_price < trade['sl'])
                        if (_rg_elapsed >= RSI_REVERSAL_MIN_MIN
                                and _rg_loss_pct >= RSI_REVERSAL_MIN_LOSS_PCT
                                and _rg_sl_ok):
                            _rg_rsi = _get_rsi15m(sym)
                            _rg_trigger = (
                                _rg_rsi is not None and (
                                    (direction == 'SHORT' and _rg_rsi > RSI_REVERSAL_SHORT_THRESH) or
                                    (direction == 'LONG'  and _rg_rsi < RSI_REVERSAL_LONG_THRESH)
                                )
                            )
                            if _rg_trigger:
                                _rg_pnl  = round(pos_size * _rg_raw_pct / 100, 2)
                                _rg_ret  = round(_rg_raw_pct * t_leverage, 1)
                                add_daily_pnl(_rg_pnl)
                                daily_stats['losses'] += 1
                                daily_stats['close_reasons']['ReversalGuard'] = \
                                    daily_stats['close_reasons'].get('ReversalGuard', 0) + 1
                                wallet_credit(_rg_pnl, trade.get('margin', MARGIN))
                                _log_closed_trade(trade, 'ReversalGuard', _rg_pnl, current_price)
                                eq   = _get_equity()
                                slip = trade.get('slippage_pct', 0.0)
                                send_msg(
                                    f"🔄 *Reversal Guard — {sym.replace('/USDT','')}* "
                                    f"{'🟢' if direction=='LONG' else '🔴'}\n"
                                    f"_RSI 15m={_rg_rsi:.0f} — היפוך מומנטום, יוצאים לפני SL_\n\n"
                                    f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
                                    f"📉 *P&L: ${_rg_pnl:+.2f}* ({_rg_ret:+.1f}% על מרג'ין)\n"
                                    f"💼 {t_leverage}x · ${trade.get('margin', MARGIN):.0f} מרג'ין | {tbadge}\n"
                                    f"📊 Slippage: {slip:.2f}% (Demo)\n"
                                    f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance', 0):.2f}`\n"
                                    f"📉 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                                )
                                print(
                                    f"[ReversalGuard] 🔄 {sym} {direction} — "
                                    f"RSI_15m={_rg_rsi:.0f} | loss={_rg_loss_pct:.2f}% "
                                    f"→ exit P&L=${_rg_pnl:+.2f}",
                                    flush=True
                                )
                                trade_close_cooldown[sym] = time.time()   # 2h cooldown — מניעת כניסה מחדש
                                with trades_lock:
                                    active_trades.remove(trade)
                                save_active_trades()
                                continue
                    except Exception as _rg_err:
                        print(f"[ReversalGuard] error {sym}: {_rg_err}", flush=True)

                # ── 0a-3. MAX DURATION — 90 דקות ללא TP1/BE ────────────────────────
                # עסקה שלא הגיעה ל-TP1 אחרי 60 דקות → יוצאים ב-market
                # (BE הופעל = TP1 כבר נגע → ה-Trailing SL מטפל, לא נוגעים)
                if not trade.get('scalp') and not trade.get('cliff') and not trade.get('be_triggered'):
                    try:
                        _md_opened  = datetime.fromisoformat(trade.get('opened_at', now_il().isoformat()))
                        _md_elapsed = (now_il() - _md_opened).total_seconds() / 60
                        _md_limit   = trade.get('max_duration_min', SWING_MAX_DURATION_MIN)
                        if _md_elapsed >= _md_limit:
                            _md_raw_pct = (current_price - entry) / entry * 100 \
                                          if direction == 'LONG' \
                                          else (entry - current_price) / entry * 100
                            _md_pnl  = round(pos_size * _md_raw_pct / 100, 2)
                            _md_ret  = round(_md_raw_pct * t_leverage, 1)
                            _md_icon = "📈" if _md_pnl >= 0 else "📉"
                            add_daily_pnl(_md_pnl)
                            if _md_pnl >= 0:
                                daily_stats['wins'] += 1
                            else:
                                daily_stats['losses'] += 1
                            daily_stats['close_reasons']['MaxDuration'] = \
                                daily_stats['close_reasons'].get('MaxDuration', 0) + 1
                            wallet_credit(_md_pnl, trade.get('margin', MARGIN))
                            _log_closed_trade(trade, 'MaxDuration', _md_pnl, current_price)
                            eq   = _get_equity()
                            slip = trade.get('slippage_pct', 0.0)
                            send_msg(
                                f"⏱ *Max Duration Exit — {sym.replace('/USDT','')}* "
                                f"{'🟢' if direction=='LONG' else '🔴'}\n"
                                f"_TP1 לא הושג תוך {_md_limit:.0f} דקות — יוצאים_\n\n"
                                f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
                                f"{_md_icon} *P&L: ${_md_pnl:+.2f}* ({_md_ret:+.1f}% על מרג'ין)\n"
                                f"💼 {t_leverage}x · ${trade.get('margin', MARGIN):.0f} מרג'ין | {tbadge}\n"
                                f"📊 Slippage: {slip:.2f}% (Demo)\n"
                                f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance', 0):.2f}`\n"
                                f"{_md_icon} סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                            )
                            print(
                                f"[MaxDuration] ⏱ {sym} {direction} — "
                                f"{_md_elapsed:.0f}min ≥ {_md_limit:.0f}min (adaptive), no TP1 "
                                f"→ exit P&L=${_md_pnl:+.2f}",
                                flush=True
                            )
                            trade_close_cooldown[sym]    = time.time()   # 2h cooldown
                            max_duration_cooldown[sym]   = time.time()   # 6h cooldown — מונע SOL loop
                            with trades_lock:
                                active_trades.remove(trade)
                            save_active_trades()
                            continue
                    except Exception as _md_err:
                        print(f"[MaxDuration] error {sym}: {_md_err}", flush=True)

                # ── 0a-4. SMART TIMEOUT — 4h ללא רווח >= +1% ─────────────────
                # משלים Stagnation: מטפל בעסקאות בהפסד ריאלי (drift > 0.5%)
                # שה-Stagnation לא תפס. Scalp/Cliff מוחרגים (timeout משלהם).
                if not trade.get('scalp') and not trade.get('cliff'):
                    try:
                        _to_opened  = datetime.fromisoformat(trade.get('opened_at', now_il().isoformat()))
                        _to_elapsed = (now_il() - _to_opened).total_seconds() / 3600
                        _to_profit  = (current_price - entry) / entry * 100 \
                                      if direction == 'LONG' \
                                      else (entry - current_price) / entry * 100
                        if _to_elapsed >= SMART_TIMEOUT_HOURS and _to_profit < SMART_TIMEOUT_MIN_PROFIT_PCT:
                            _to_pnl    = round(pos_size * _to_profit / 100, 2)
                            _to_icon   = "📈" if _to_pnl >= 0 else "📉"
                            _to_ret    = round(_to_profit * t_leverage, 1)
                            add_daily_pnl(_to_pnl)
                            if _to_pnl >= 0:
                                daily_stats['wins'] += 1
                            else:
                                daily_stats['losses'] += 1
                            daily_stats['close_reasons']['Timeout'] = \
                                daily_stats['close_reasons'].get('Timeout', 0) + 1
                            wallet_credit(_to_pnl, trade.get('margin', MARGIN))
                            _log_closed_trade(trade, 'Timeout', _to_pnl, current_price)
                            eq   = _get_equity()
                            slip = trade.get('slippage_pct', 0.0)
                            send_msg(
                                f"⏱ *Smart Timeout — {sym}* "
                                f"{'🟢' if direction == 'LONG' else '🔴'}\n"
                                f"_פתוח {_to_elapsed:.1f}h | רווח {_to_profit:+.2f}% < "
                                f"+{SMART_TIMEOUT_MIN_PROFIT_PCT}% — משחרר מרג'ין_\n\n"
                                f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
                                f"{_to_icon} *P&L: ${_to_pnl:+.2f}* "
                                f"({_to_ret:+.1f}% על מרג'ין)\n"
                                f"💼 {t_leverage}x · ${trade.get('margin', MARGIN):.0f} "
                                f"מרג'ין | {tbadge}\n"
                                f"📊 Slippage: {slip:.2f}% (Demo)\n"
                                f"💼 Equity: `${eq:.2f}` | "
                                f"יתרה: `${wallet.get('balance', 0):.2f}`\n"
                                f"{_to_icon} סה\"כ היום: "
                                f"${round(daily_stats['total_pnl'], 2):+}"
                            )
                            print(
                                f"[SmartTimeout] ⏱ {sym} {direction} — "
                                f"{_to_elapsed:.1f}h | profit={_to_profit:+.2f}% "
                                f"→ exit P&L=${_to_pnl:+.2f}",
                                flush=True
                            )
                            with trades_lock:
                                active_trades.remove(trade)
                            save_active_trades()
                            continue
                    except Exception as _te:
                        print(f"[SmartTimeout] error {sym}: {_te}", flush=True)

                # 0b. Trailing SL — ATR-based (1.5× ATR מהשיא) + fallback TRAIL_PCT%
                raw_profit_pct = (current_price - entry) / entry * 100 \
                                 if direction == 'LONG' \
                                 else (entry - current_price) / entry * 100
                trailing_active = raw_profit_pct >= TRAIL_ACTIVATION_PCT

                if trailing_active:
                    # עדכון peak_price
                    if direction == 'LONG':
                        if current_price > trade['peak_price']:
                            trade['peak_price'] = current_price
                    else:
                        if current_price < trade['peak_price']:
                            trade['peak_price'] = current_price

                    # חישוב מרחק Trailing: max(1.5×ATR, TRAIL_PCT%)
                    atr_val      = trade.get('atr', 0) or 0
                    atr_dist     = ATR_TRAIL_MULT * atr_val
                    pct_dist     = trade['peak_price'] * TRAIL_PCT / 100
                    trail_dist   = max(atr_dist, pct_dist)   # הגדול = מגן יותר

                    if direction == 'LONG':
                        new_trail = round(trade['peak_price'] - trail_dist, 8)
                        if trade['trailing_sl'] is None or new_trail > trade['trailing_sl']:
                            trade['trailing_sl'] = new_trail
                    else:
                        new_trail = round(trade['peak_price'] + trail_dist, 8)
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
                    # After TP1 only the lifecycle remainder is still open.
                    # Use it for both wallet settlement and the common fee ledger.
                    settlement_notional = _remaining_notional(trade)
                    pnl_usd  = round(sign * settlement_notional * dist_pct / 100, 2)
                    pnl_pct_r = round(sign * dist_pct * LEVERAGE, 1)
                    icon     = "📈" if pnl_usd >= 0 else "📉"
                    if pnl_usd >= 0:
                        daily_stats['wins']  += 1
                    else:
                        daily_stats['losses'] += 1
                    add_daily_pnl(pnl_usd)
                    daily_stats['close_reasons']['Trailing'] += 1
                    wallet_credit(pnl_usd, trade.get('margin', MARGIN))
                    trailing_record = _log_closed_trade(trade, 'Trailing', pnl_usd, current_price)
                    ref = trade['peak_price']
                    eq  = _get_equity()
                    slip = trade.get('slippage_pct', 0.0)
                    send_msg(
                        f"📍 *Trailing Stop נגע — {sym}*\n"
                        f"{'שיא' if direction=='LONG' else 'שפל'}: `{ref:.6g}` → יציאה: `{current_price:.6g}`\n"
                        f"ברוטו: `${trailing_record['gross_pnl_usd']:+.2f}` | עמלה: `${trailing_record['fees_usd']:.2f}`\n"
                        f"{icon} *נטו: {trailing_record['net_pnl_usd']:+.2f}$ ({pnl_pct_r:+.1f}% על מרג'ין)* | {tbadge}\n"
                        f"💼 {t_leverage}x Isolated · Trailing {TRAIL_PCT}%\n"
                        f"📊 Slippage: {slip:.2f}% (Demo)\n"
                        f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
                        f"{icon} סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()
                    continue

                # ── רמת BE lock: Entry+0.1% לLONG (מתחת), Entry+0.1% לSHORT (מעל) ──
                # LONG:  SL → entry*(1-buffer) → מתחת לכניסה → sl_hit כשמחיר יורד לשם ✓
                # SHORT: SL → entry*(1+buffer) → מעל לכניסה  → sl_hit כשמחיר עולה לשם ✓
                be_lock_price = round(entry * (1 - BE_LOCK_BUFFER_PCT / 100), 8) \
                                if direction == 'LONG' \
                                else round(entry * (1 + BE_LOCK_BUFFER_PCT / 100), 8)

                # 1a. Greed Early BE — FNG≥70: BE מוקדם לפני TP1 (רק בחמדנות)
                if not trade['be_triggered'] and fng_v_mgr >= GREED_THRESHOLD:
                    greed_profit_pct = abs(current_price - entry) / entry * 100
                    if profit_dir(current_price) and greed_profit_pct >= GREED_EARLY_BE_PCT:
                        trade['sl']           = be_lock_price
                        trade['be_triggered'] = True
                        dir_label = "מעל" if direction == 'SHORT' else "מתחת"
                        print(f"  [SENTIMENT] GREED EARLY BE: {sym} SL→{be_lock_price:.6g} @ {current_price:.6g} (+{greed_profit_pct:.2f}%, FNG={fng_v_mgr})")
                        send_msg(
                            f"🔒 *Greed Early BE — {sym}*\n"
                            f"מחיר: `{current_price:.6g}` (+{greed_profit_pct:.2f}% רווח)\n"
                            f"SL הועבר ל: `{be_lock_price:.6g}` ({BE_LOCK_BUFFER_PCT}% {dir_label} כניסה) 🛡️\n"
                            f"(FNG={fng_v_mgr} — מצב חמדנות) | ההון מוגן!"
                        )

                # 1b. Break Even סטנדרטי — BE מופעל אוטומטית כשTP1 נגע (ראה בלוק TP1 למטה)
                # [הוסר] הבדיקה הישנה be_hit() השתמשה ב-be_lvl=entry כ-trigger
                # וגרמה לסגירה מיידית: לSHORT כל מחיר < entry הפעיל BE לפני TP1.

                # 1c. Partial 25% Close — הגיע ל-+3%, עכשיו יורד מהשיא ב-0.8%+
                if (not trade.get('partial_25_triggered')
                        and not trade.get('tp1_triggered')
                        and profit_dir(current_price)):
                    peak_profit_pct = (
                        (trade['peak_price'] - entry) / entry * 100
                        if direction == 'LONG'
                        else (entry - trade['peak_price']) / entry * 100
                    )
                    drop_from_peak = (
                        (trade['peak_price'] - current_price) / trade['peak_price'] * 100
                        if direction == 'LONG'
                        else (current_price - trade['peak_price']) / trade['peak_price'] * 100
                    )
                    if peak_profit_pct >= PARTIAL_25_TRIGGER and drop_from_peak >= PARTIAL_25_DROP:
                        quarter_pos    = round(pos_size * 0.25, 2)
                        quarter_margin = round(trade.get('margin', MARGIN) * 0.25, 2)
                        dist_pct       = abs(current_price - entry) / entry * 100
                        partial_pnl    = round(quarter_pos * dist_pct / 100, 2)
                        trade['partial_25_triggered'] = True
                        trade['pos_size'] = round(pos_size - quarter_pos, 2)
                        trade['margin']   = round(trade.get('margin', MARGIN) - quarter_margin, 2)
                        add_daily_pnl(partial_pnl)
                        wallet_credit(partial_pnl, quarter_margin, count_result=False)
                        partial_fee = _record_exit_leg(trade, partial_pnl, quarter_pos)
                        save_active_trades()
                        print(f"  [Partial25] {sym}: סגר 25% @ {current_price:.6g} "
                              f"(שיא={trade['peak_price']:.6g} ירד {drop_from_peak:.1f}%) "
                              f"gross={partial_pnl:+.2f}$ fee=${partial_fee:.2f}")
                        send_msg(
                            f"⚡ *Partial Close 25% — {sym}*\n\n"
                            f"המחיר הגיע ל\\+{peak_profit_pct:.1f}% ואז ירד {drop_from_peak:.1f}% מהשיא\n"
                            f"סגרנו 25% מהפוזיציה @ `{current_price:.6g}`\n"
                            f"💰 ברוטו: *{partial_pnl:+.2f}$* | עמלה: `${partial_fee:.2f}`\n"
                            f"75% נשאר פתוח · SL: `{trade['sl']:.6g}`"
                        )
                        # Recalculate sizes next monitor pass. This prevents TP1 from
                        # using the pre-partial notional and charging/double-closing it.
                        continue

                # 2. TP1 — סגור 75%, הפעל Trailing על 25% נותרים
                # Wick Detection: בדוק High/Low של נר 1m האחרון —
                # המוניטור רץ כל 60s ועלול להחמיץ שיא שהגיע ל-TP1 בין בדיקות
                _tp1_check_price = current_price
                if not trade.get('tp1_triggered'):
                    try:
                        _wicks = exchange.fetch_ohlcv(sym, '1m', limit=3)
                        # בודק 2 נרות אחרונים — מוניטור רץ כל 60s, שיא יכול להיות בנר הקודם
                        for _wc in (_wicks[-1], _wicks[-2]) if len(_wicks) >= 2 else (_wicks[-1],):
                            _wh = _wc[2]  # high
                            _wl = _wc[3]  # low
                            if direction == 'LONG' and _wh >= trade['tp1']:
                                _tp1_check_price = trade['tp1']
                                print(f"  [WickTP1] {sym} LONG wick high={_wh:.6g} ≥ TP1={trade['tp1']:.6g} → TP1 מופעל", flush=True)
                                break
                            elif direction == 'SHORT' and _wl <= trade['tp1']:
                                _tp1_check_price = trade['tp1']
                                print(f"  [WickTP1] {sym} SHORT wick low={_wl:.6g} ≤ TP1={trade['tp1']:.6g} → TP1 מופעל", flush=True)
                                break
                    except Exception as _wick_e:
                        pass  # fallback שקט — wick detection הוא שיפור, לא חובה
                if tp1_hit(_tp1_check_price):
                    dist_pct  = abs(current_price - entry) / entry * 100
                    tp1_pnl   = round(tp1_close * dist_pct / 100, 2)
                    tp1_pct_r = round(tp1_pnl / MARGIN * 100, 1)
                    trade['tp1_triggered'] = True
                    trade['tp1_pnl']       = tp1_pnl
                    trade['phase']         = 'trailing'
                    trade['peak_price']    = current_price
                    if direction == 'LONG':
                        trade['trailing_sl'] = current_price * 0.98
                    else:
                        trade['trailing_sl'] = current_price * 1.02
                    add_daily_pnl(tp1_pnl)
                    tp1_fee = _record_exit_leg(trade, tp1_pnl, tp1_close)
                    # 75% is a real filled exit. Credit its gross P&L now while
                    # keeping the released margin locked until the final 25% closes.
                    wallet_credit(tp1_pnl, 0, count_result=False)

                    # TP1 hit → always move SL to Break Even (entry price)
                    trade['sl']           = entry
                    trade['be_triggered'] = True
                    send_msg(
                        f"🎯 *TP1 הושג — {sym}!*\n"
                        f"מחיר: `{current_price:.6g}` | {direction} | {tbadge}\n"
                        f"75% נסגרו · ברוטו: *+${tp1_pnl}* | עמלה: `${tp1_fee:.2f}`\n"
                        f"🔒 *SL הועבר ל-BE אוטומטית!* `{entry:.6g}` — הון מוגן\n"
                        f"📍 Trailing SL: `{trade['trailing_sl']:.6g}` | שאר 25% ממשיכים ל-TP2\n"
                        f"📈 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    continue

                # 3. SL נגע
                if sl_hit(current_price):
                    slip = trade.get('slippage_pct', 0.0)
                    if trade['be_triggered']:
                        daily_stats['losses'] += 1
                        daily_stats['close_reasons']['BE'] += 1
                        wallet_credit(0, trade.get('margin', MARGIN))   # מרג'ין חוזר, ללא P&L
                        _log_closed_trade(trade, 'BE', 0.0, current_price)
                        eq = _get_equity()
                        send_msg(
                            f"🔒 *Break Even — יצאנו ב-{sym}*\n"
                            f"מחיר: `{current_price:.6g}` | כניסה: `{entry:.6g}`\n"
                            f"*ללא הפסד · ההון נשמר*\n"
                            f"💼 {t_leverage}x Isolated | {tbadge}\n"
                            f"📊 Slippage: {slip:.2f}% (Demo)\n"
                            f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
                            f"📊 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                        )
                    else:
                        loss     = round(pos_size * trade['sl_pct'] / 100, 2)
                        _trade_margin = trade.get('margin', MARGIN)
                        loss_pct = round(loss / _trade_margin * 100, 1)
                        daily_stats['losses']    += 1
                        add_daily_pnl(-loss)
                        daily_stats['close_reasons']['SL'] += 1
                        wallet_credit(-loss, _trade_margin)   # מרג'ין חוזר פחות ההפסד
                        _log_closed_trade(trade, 'SL', -loss, current_price)
                        if trade.get('timeframe') == 'Breakout':
                            breakout_sl_cooldown[sym] = time.time()
                        trade_close_cooldown[sym] = time.time()   # 2h cooldown — מניעת כניסה מחדש
                        eq = _get_equity()
                        send_msg(
                            f"🛑 *SL נגע — {sym}*\n"
                            f"כניסה: `{entry:.6g}` → SL: `{trade['sl']:.6g}`\n"
                            f"❌ *Loss: -${loss}* ({loss_pct}% על מרג'ין)\n"
                            f"💼 {t_leverage}x Isolated | {tbadge}\n"
                            f"📊 Slippage: {slip:.2f}% (Demo)\n"
                            f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
                            f"📉 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                        )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()

            # ════════════════════════════════════════════
            # שלב TRAILING — 25% פוזיציה נותרת ($62.50)
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

                # TP מלא — סגור שאר 25%
                if tp_full_hit(current_price):
                    dist_pct = abs(current_price - entry) / entry * 100
                    tp_pnl   = round(remaining * dist_pct / 100, 2)
                    total    = round(trade.get('tp1_pnl', 0) + tp_pnl, 2)
                    daily_stats['wins']      += 1
                    add_daily_pnl(tp_pnl)
                    daily_stats['close_reasons']['TP'] += 1
                    wallet_credit(tp_pnl, trade.get('margin', MARGIN))
                    _log_closed_trade(trade, 'TP', tp_pnl, current_price)
                    eq = _get_equity()
                    est_tp_full = round(abs(current_price - entry) / entry * remaining, 2)
                    slip = trade.get('slippage_pct', 0.0)
                    send_msg(
                        f"✅ *TP מלא הושג — {sym}!* 🎉\n"
                        f"מחיר: `{current_price:.6g}` | {direction} | {tbadge}\n"
                        f"שאר 25% נסגרו · ✅ *Profit at TP: +${est_tp_full}*\n"
                        f"TP1 + TP סה\"כ: 📈 *+${total}*\n"
                        f"💼 {t_leverage}x Isolated\n"
                        f"📊 Slippage: {slip:.2f}% (Demo)\n"
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
                    dist_pct  = abs(current_price - entry) / entry * 100
                    sign      = 1 if profit_dir(current_price) else -1
                    half_pnl  = round(sign * remaining * dist_pct / 100, 2)
                    total     = round(trade.get('tp1_pnl', 0) + half_pnl, 2)
                    icon      = "📈" if half_pnl >= 0 else "📉"
                    if half_pnl >= 0:
                        daily_stats['wins'] += 1
                    else:
                        daily_stats['losses'] += 1
                    add_daily_pnl(half_pnl)
                    daily_stats['close_reasons']['TP1+Trail'] += 1
                    wallet_credit(half_pnl, trade.get('margin', MARGIN))
                    _log_closed_trade(trade, 'TP1+Trail', half_pnl, current_price)
                    eq = _get_equity()
                    ref_price = trade['peak_price']
                    slip = trade.get('slippage_pct', 0.0)
                    send_msg(
                        f"📍 *Trailing Stop נגע — {sym}*\n"
                        f"{'שיא' if direction=='LONG' else 'שפל'}: `{ref_price:.6g}` → יציאה: `{current_price:.6g}`\n"
                        f"25% נסגרו: {icon} *{half_pnl:+}$* | {tbadge}\n"
                        f"TP1 + Trailing סה\"כ: {icon} *{total:+}$*\n"
                        f"💼 {t_leverage}x Isolated\n"
                        f"📊 Slippage: {slip:.2f}% (Demo)\n"
                        f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
                        f"{icon} סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()

        except Exception as e:
            print(f"Track error {trade.get('symbol','?')}: {e}")

    save_active_trades()   # שמור גם שינויי BE / Trailing SL

    # Persist the latest equity + unrealized P&L snapshot so the dashboard
    # always shows an accurate number — even immediately after a bot restart
    # (before the next track_trades() cycle re-populates current_price).
    _snap_unrealized = _unrealized_cached()
    _snap_equity     = _equity_cached()
    wallet['unrealized_pnl'] = _snap_unrealized
    wallet['equity']         = _snap_equity
    # Append to equity history using the already-computed value (avoids a
    # redundant live API call that _append_equity_point → _get_equity would make).
    _eq_hist = wallet.setdefault('equity_history', [])
    _eq_hist.append({'t': now_il().strftime('%m/%d %H:%M'), 'eq': _snap_equity})
    if len(_eq_hist) > 120:
        wallet['equity_history'] = _eq_hist[-120:]
    save_wallet()

# --- דוח יומי ---

def check_api_connection():
    try:
        exchange.fetch_ticker('BTC/USDT')
        return True
    except Exception:
        return False

def send_daily_report():
    now = now_il().strftime('%d/%m/%Y %H:%M')
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

    # ── Strategy Breakdown ───────────────────────────────────────────────────
    # Group ALL audit entries (not just today) by track/strategy for a full picture
    strategy_data: dict = {}
    for t in trade_audit_log:
        track = t.get('track') or t.get('strategy') or 'Swing'
        if track not in strategy_data:
            strategy_data[track] = {'count': 0, 'wins': 0, 'pnl': 0.0}
        pnl_entry = t.get('pnl_usd', 0.0)
        strategy_data[track]['count'] += 1
        if pnl_entry > 0:
            strategy_data[track]['wins'] += 1
        strategy_data[track]['pnl'] = round(strategy_data[track]['pnl'] + pnl_entry, 2)

    if strategy_data:
        strat_msg = f"📊 *ביצועים לפי אסטרטגיה* \\(100 עסקאות אחרונות\\)\n"
        strat_msg += f"{'─' * 28}\n\n"
        STRATEGY_EMOJI = {
            'Swing':     '🌊',
            'Scalp':     '⚡',
            'Breakout':  '🚀',
            'Velocity':  '💨',
            'Research':  '🔬',
        }
        for strat_name in ['Swing', 'Scalp', 'Breakout', 'Velocity', 'Research']:
            if strat_name not in strategy_data:
                continue
            d = strategy_data[strat_name]
            wr = round(d['wins'] / d['count'] * 100) if d['count'] > 0 else 0
            pnl_s = d['pnl']
            pnl_sign = '+' if pnl_s >= 0 else ''
            pnl_color = '📈' if pnl_s >= 0 else '📉'
            emoji = STRATEGY_EMOJI.get(strat_name, '📊')
            strat_msg += (
                f"{emoji} *{strat_name}*\n"
                f"   עסקאות: {d['count']}  |  Win Rate: {wr}%\n"
                f"   {pnl_color} P&L: *{pnl_sign}${pnl_s:.2f}*\n\n"
            )
        # also show any unexpected strategy names
        for strat_name, d in strategy_data.items():
            if strat_name in ('Swing','Scalp','Breakout','Velocity','Research'):
                continue
            wr = round(d['wins'] / d['count'] * 100) if d['count'] > 0 else 0
            pnl_s = d['pnl']
            pnl_sign = '+' if pnl_s >= 0 else ''
            pnl_color = '📈' if pnl_s >= 0 else '📉'
            strat_msg += (
                f"📊 *{strat_name}*\n"
                f"   עסקאות: {d['count']}  |  Win Rate: {wr}%\n"
                f"   {pnl_color} P&L: *{pnl_sign}${pnl_s:.2f}*\n\n"
            )
        send_msg(strat_msg)

    send_msg(msg)

    # ── פירוט עסקאות סגורות היום ─────────────────────────────────────────────
    # משתמשים ב-trade_audit_log (נשמר ל-object storage) כמקור ראשי,
    # כך שהדוח שורד הפעלות מחדש של הבוט. closed_trades_log כ-fallback.
    today_str = now_il().strftime('%Y-%m-%d')
    today_trades = [
        t for t in trade_audit_log
        if t.get('closed_at', '').startswith(today_str)
    ]
    if not today_trades:   # fallback לרשימת הזיכרון למקרה שה-audit log ריק
        today_trades = [
            t for t in closed_trades_log
            if t.get('closed_at', '').startswith(today_str)
        ]

    if today_trades:
        reason_emoji = {
            'TP':           '🎯',  'TP1+Trail':    '📍',
            'Trailing':     '📍',  'BE':           '🔒',
            'SL':           '🛑',  'Manual':       '✋',
            'Scalp-TP':     '✅',  'Scalp-SL':     '❌',  'Scalp-Time':    '⏱',
            'Velocity-TP':  '✅',  'Velocity-SL':  '❌',  'Velocity-Time': '⏱',
            'Cliff-TP':     '✅',  'Cliff-SL':     '❌',  'Cliff-Time':    '⏱',
        }
        reason_label = {
            'TP':           'TP מלא',       'TP1+Trail':    'TP1 + Trailing',
            'Trailing':     'Trailing SL',  'BE':           'Break-Even',
            'SL':           'Stop Loss',    'Manual':       'סגירה ידנית',
            'Scalp-TP':     'Scalp TP',     'Scalp-SL':     'Scalp SL',    'Scalp-Time':    'Scalp פג תוקף',
            'Velocity-TP':  'Velocity TP',  'Velocity-SL':  'Velocity SL', 'Velocity-Time': 'Velocity פג תוקף',
            'Cliff-TP':     'Cliff TP',     'Cliff-SL':     'Cliff SL',    'Cliff-Time':    'Cliff פג תוקף',
        }

        detail_msg = f"📋 *פירוט עסקאות היום — {now_il().strftime('%d/%m/%Y')}*\n"
        detail_msg += f"{'─' * 30}\n\n"

        total_pnl_detail = 0.0
        for i, t in enumerate(today_trades, 1):
            sym       = t['symbol'].replace('/USDT', '')
            d         = t.get('direction', 'LONG')
            d_emoji   = '🟢' if d == 'LONG' else '🔴'
            entry     = t.get('entry_price', 0)
            close_p   = t.get('close_price', 0)
            pnl       = t.get('pnl_usd', 0.0)
            reason    = t.get('close_reason', '?')
            closed_t  = t.get('closed_at', '')[-8:-3]  # HH:MM
            r_emoji   = reason_emoji.get(reason, '❓')
            r_label   = reason_label.get(reason, reason)
            pnl_icon  = '📈' if pnl >= 0 else '📉'
            total_pnl_detail += pnl

            detail_msg += (
                f"*{i}\\. {sym}* {d_emoji} {d}\n"
                f"   💵 כניסה: `{entry:.5g}` → יציאה: `{close_p:.5g}`\n"
                f"   {pnl_icon} P&L: *${pnl:+.2f}*\n"
                f"   {r_emoji} סיבה: {r_label}  _{closed_t}_\n\n"
            )

        pnl_total_icon = '📈' if total_pnl_detail >= 0 else '📉'
        detail_msg += f"{'─' * 30}\n"
        detail_msg += f"{pnl_total_icon} *סה\"כ P&L: ${total_pnl_detail:+.2f}*  \\({len(today_trades)} עסקאות\\)"

        send_msg(detail_msg)
    else:
        send_msg("📋 _אין עסקאות סגורות היום_")

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

    now_str   = now_il().strftime('%H:%M')
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

        # ── זמן פתיחה + Duration ──
        try:
            opened_dt  = datetime.fromisoformat(t.get('opened_at', now_il().isoformat()))
            elapsed_s  = (now_il() - opened_dt).total_seconds()
            elapsed_h  = int(elapsed_s // 3600)
            elapsed_m  = int((elapsed_s % 3600) // 60)
            open_time  = opened_dt.strftime('%H:%M')
            duration_str = f"{elapsed_h}h {elapsed_m:02d}m" if elapsed_h > 0 else f"{elapsed_m}m"
        except Exception:
            open_time    = "?"
            duration_str = "?"

        # ── P&L ──
        _ps     = t.get('pos_size', POSITION_SIZE)   # per-trade position size
        raw_pct = (price - entry) / entry * 100
        pnl_pct = raw_pct if direction == 'LONG' else -raw_pct
        if t.get('tp1_triggered'):
            half_pnl = round(_ps * 0.25 * pnl_pct / 100, 2)
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
            f"   ⏰ נפתח: `{open_time}` · ⏱ פעיל: *{duration_str}*\n"
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
        # אחרי TP1 — רק 25% נשארו פתוחים; tp1_pnl כבר ב-realized (wallet.total_pnl)
        _active_ps = _ps * 0.25 if t.get('tp1_triggered') else _ps
        total_floating += round(_active_ps * p_pct / 100, 2)

    float_icon = "📈" if total_floating >= 0 else "📉"
    realized   = round(wallet.get('total_pnl', 0.0), 2)
    total_bal  = round(wallet.get('starting', STARTING_BALANCE) + realized + total_floating, 2)

    # ── FNG Sentiment summary ──
    try:
        _fng_hb, _lbl_hb = get_fear_greed()
        mode_line = (
            f"🧭 Sentiment: *{_lbl_hb}* ({_fng_hb}) — "
            f"Fixed: SL {SL_PCT}% · TP1 {TP1_PCT}% · TP2 {TP2_PCT}%"
        )
    except Exception:
        mode_line = ""

    footer = (
        f"\n{'─' * 24}\n"
        f"{mode_line}\n"
        f"{'─' * 24}\n"
        f"{pnl_icon} Realized P&L: *${realized:+.2f}*\n"
        f"{float_icon} Floating P&L: *${total_floating:+.2f}*\n"
        f"💼 Total Balance: *${total_bal:.2f}*\n"
        f"📊 P&L היום: *${pnl_today:+}* · {len(trades_snapshot)} עסקה פעילה"
    )

    # שלח כל עסקה עם כפתור Close משלה
    per_trade_blocks = msg.split('\n\n')
    # השורה הראשונה היא ה-header
    header_block = per_trade_blocks[0] if per_trade_blocks else msg

    try:
        send_msg(header_block)   # header ללא כפתור
    except Exception:
        pass

    for t_s in trades_snapshot:
        sym_s = t_s['symbol']
        # מצא את הבלוק הרלוונטי לעסקה זו ב-msg
        trade_block = None
        for block in per_trade_blocks[1:]:
            if sym_s.replace('/USDT', '') in block or sym_s in block:
                trade_block = block.strip()
                break
        if not trade_block:
            continue
        try:
            markup_s = _make_close_markup(sym_s)
            bot.send_message(CHAT_ID, trade_block, parse_mode='Markdown',
                             reply_markup=markup_s)
        except Exception as _e:
            print(f"[Heartbeat] button send error {sym_s}: {_e}")

    try:
        send_msg(footer)
    except Exception:
        pass

    if VERBOSE_LOG:
        print(f"[Heartbeat] sent — {len(trades_snapshot)} trade(s) @ {now_str}")


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
    now = now_il()
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

@bot.message_handler(commands=['wallet'])
def handle_wallet(message):
    """מציג סיכום מלא של הארנק הווירטואלי."""
    with trades_lock:
        n_open      = len(active_trades)
        locked      = sum(t.get('margin', MARGIN) for t in active_trades)
    available   = wallet.get('balance', STARTING_BALANCE)
    start       = wallet.get('starting', STARTING_BALANCE)
    realized    = wallet.get('total_pnl', 0.0)
    unrealized  = _get_unrealized_pnl()
    equity      = _get_equity()
    eq_pct      = round((equity - start) / start * 100, 1)
    today_pnl   = round(daily_stats.get('total_pnl', 0.0), 2)
    wins        = daily_stats.get('wins', 0)
    losses      = daily_stats.get('losses', 0)
    total_trades= wallet.get('trades_opened', 0)
    total_wins  = wallet.get('total_wins', 0)
    total_losses= wallet.get('total_losses', 0)
    wr          = round(total_wins / (total_wins + total_losses) * 100) if (total_wins + total_losses) > 0 else 0

    eq_arrow    = "📈" if equity >= start else "📉"
    r_icon      = "📈" if realized  >= 0 else "📉"
    u_icon      = "📈" if unrealized >= 0 else "📉"
    t_icon      = "📈" if today_pnl >= 0 else "📉"
    eq_color    = "🟢" if equity >= start else "🔴"

    msg  = f"💼 *ארנק וירטואלי — סיכום מלא*\n"
    msg += f"{'━' * 22}\n"
    msg += f"💰 *יתרה פנויה:* `${available:.2f}`\n"
    msg += f"🔒 *נעול בעסקאות:* `${locked:.2f}` ({n_open} עסקאות)\n"
    msg += f"{'━' * 22}\n"
    msg += f"{u_icon} *Unrealized P&L:* `${unrealized:+.2f}`\n"
    msg += f"{r_icon} *Realized P&L כולל:* `${realized:+.2f}`\n"
    msg += f"{t_icon} *Realized היום:* `${today_pnl:+.2f}`\n"
    msg += f"{'━' * 22}\n"
    msg += f"{eq_color} *Equity:* `${equity:.2f}` ({eq_arrow} {eq_pct:+.1f}% מ-${start:.0f})\n"
    msg += f"{'━' * 22}\n"
    msg += f"📊 *סטטיסטיקה היום:* ✅{wins} / ❌{losses}\n"
    msg += f"📈 *Win Rate כולל:* {wr}% ({total_wins}W / {total_losses}L)\n"
    msg += f"🔢 *סה\"כ עסקאות שנפתחו:* {total_trades}\n"

    send_msg(msg)


@bot.message_handler(commands=['status'])
def handle_status(message):
    # רענן מחירים חיים לפני הצגת הסטטוס
    try:
        total_unrealized = _get_unrealized_pnl()
    except Exception:
        total_unrealized = 0.0

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
        p25_label   = " · ⚡25%" if t.get('partial_25_triggered') else ""
        tp1_label   = " · TP1✅" if t.get('tp1_triggered') else ""
        dirlab      = "🟢 LONG" if t.get('direction', 'LONG') == 'LONG' else "🔴 SHORT"
        direction   = t.get('direction', 'LONG')
        score       = t.get('score', 0)
        tf          = t.get('timeframe', '4H')
        entry       = t['entry']
        curr_p      = t.get('current_price', entry)
        pos_size    = t.get('pos_size', POSITION_SIZE)

        # P&L חי לעסקה זו
        if direction == 'LONG':
            trade_pnl = round(pos_size * (curr_p - entry) / entry, 2)
        else:
            trade_pnl = round(pos_size * (entry - curr_p) / entry, 2)
        dist_pct  = round(abs(curr_p - entry) / entry * 100, 2)
        pnl_icon  = "📈" if trade_pnl >= 0 else "📉"
        tp1_bonus = f" \\+TP1: \\+${t.get('tp1_pnl', 0):.2f}" if t.get('tp1_triggered') else ""

        msg += (
            f"*{i}. {t['symbol']}* {dirlab} [{tf}] · {phase_label}{be_label}{p25_label}{tp1_label}\n"
            f"   ניקוד: *{score}/100* | ATR: `{t.get('atr', 0):.6g}`\n"
            f"   כניסה: `{entry:.6g}` → נוכחי: `{curr_p:.6g}` ({'+' if curr_p>=entry else ''}{dist_pct:.2f}%)\n"
            f"   {pnl_icon} *P&L: `{trade_pnl:+.2f}$`*{tp1_bonus}\n"
            f"   🛑 SL: `{t['sl']:.6g}` | 🎯 TP1 \\(BE\\): `{t.get('tp1', t['tp']):.6g}` | TP2: `{t['tp']:.6g}`\n"
        )
        if t.get('trailing_sl'):
            ref = "שיא" if direction == 'LONG' else "שפל"
            msg += f"   📍 Trailing SL: `{t['trailing_sl']:.6g}` | {ref}: `{t['peak_price']:.6g}`\n"
        msg += "\n"

    # סיכום unrealized כולל
    total_icon = "📈" if total_unrealized >= 0 else "📉"
    realized   = round(wallet.get('total_pnl', 0.0), 2)
    real_icon  = "📈" if realized >= 0 else "📉"
    msg += (
        f"━━━━━━━━━━━━━━━━\n"
        f"{total_icon} *Unrealized כולל: `{total_unrealized:+.2f}$`*\n"
        f"{real_icon} *Realized היום: `{round(daily_stats.get('total_pnl', 0), 2):+.2f}$`*\n\n"
        f"_לעדכון SL/TP: /update SYMBOL SL TP_\n"
        f"_לסגירה ידנית: /close SYMBOL_"
    )
    send_msg(msg)

@bot.message_handler(commands=['gate'])
def handle_gate(message):
    """סטטיסטיקות Claude AI Gate — /gate"""
    if not _is_authorized(message.chat.id):
        return
    send_msg(claude_gate.gate_summary())


@bot.message_handler(commands=['research'])
def handle_research(message):
    """Dr. Sniper Research — /research | /research off | /research on"""
    if not _is_authorized(message.chat.id):
        return
    parts = message.text.strip().split()
    if len(parts) > 1:
        sub = parts[1].lower()
        if sub == 'off':
            claude_research.set_auto_execute(False)
            send_msg(
                "🟡 *Research mode: ALERT\\-ONLY*\n"
                "קלוד ישלח התראות אבל לא יפתח עסקאות אוטומטית\\.\n"
                "הפעל שוב עם `/research on`"
            )
            return
        if sub == 'on':
            claude_research.set_auto_execute(True)
            send_msg(
                "🟢 *Research mode: AUTO\\-EXECUTE*\n"
                "קלוד יפתח עסקאות כשסקור ≥ 78\\."
            )
            return
        if sub == 'status':
            send_msg(claude_research.research_summary())
            return
    # Run research now (manual trigger — works in DEV and PROD)
    # When the bot is paused, run in alert-only mode so no trades are opened.
    paused_now = _bot_paused
    if paused_now:
        send_msg(
            "⏸ *בוט מושהה — Research יפעל במצב התראות בלבד*\n"
            "ממצאים ישלחו כהתראה, ללא ביצוע עסקאות\\."
        )
    else:
        send_msg(
            "🔬 *Dr\\. Sniper Research מתחיל\\.\\.\\.*\n"
            "אוסף נתונים מ\\-CoinGecko, exchange, DeFiLlama ועוד\\.\n"
            "_עשוי לקחת כ\\-60 שניות_"
        )
    def _run(_paused=paused_now):
        # Temporarily force alert-only if bot is paused; restore original setting after
        _orig_auto = claude_research.is_auto_execute()
        if _paused:
            claude_research.set_auto_execute(False)
        try:
            claude_research.run_claude_research(
                open_trades_count=len(active_trades),
                daily_pnl=daily_stats.get('total_pnl', 0.0),
                fng=_fng_cache.get('value', 50),
                max_trades=MAX_TRADES,
            )
        except Exception as _re:
            send_msg(f"⚠️ Research error: {_re}")
        finally:
            if _paused:
                claude_research.set_auto_execute(_orig_auto)
    threading.Thread(target=_run, daemon=True).start()

@bot.message_handler(commands=['update'])
def handle_update(message):
    """
    שימוש: /update BTC 84000 95000
    או:    /update BTC sl=84000
    או:    /update BTC tp=95000
    """
    if not _is_authorized(message.chat.id):
        return
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

        # ולידציה: תלוי כיוון (LONG / SHORT)
        entry     = trade['entry']
        direction = trade.get('direction', 'LONG').upper()
        is_short  = direction == 'SHORT'

        if is_short:
            if not (new_tp < entry):
                send_msg(f"⚠️ בשורט, TP ({new_tp}) חייב להיות *מתחת* למחיר הכניסה ({entry:.4f})")
                return
            if not (new_sl > entry):
                send_msg(f"⚠️ בשורט, SL ({new_sl}) חייב להיות *מעל* למחיר הכניסה ({entry:.4f})")
                return
        else:  # LONG
            if not (new_tp > entry):
                send_msg(f"⚠️ בלונג, TP ({new_tp}) חייב להיות *מעל* למחיר הכניסה ({entry:.4f})")
                return
            if not (new_sl < entry):
                send_msg(f"⚠️ בלונג, SL ({new_sl}) חייב להיות *מתחת* למחיר הכניסה ({entry:.4f})")
                return

        # עדכון
        old_sl, old_tp = trade['sl'], trade['tp']
        trade['sl'] = new_sl
        trade['tp'] = new_tp
        if is_short:
            trade['sl_pct'] = round((new_sl - entry) / entry * 100, 2)
            trade['tp_pct'] = round((entry - new_tp) / entry * 100, 2)
        else:
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
    if not _is_authorized(message.chat.id):
        return
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

        # TP1 was already realized when hit; settle only the remaining filled size.
        _ps_m       = _remaining_notional(trade)
        direction_m = trade.get('direction', 'LONG')
        raw_pct     = (current_price - entry) / entry * 100
        pnl_pct_m   = raw_pct if direction_m == 'LONG' else -raw_pct
        gross_pnl   = round(_ps_m * pnl_pct_m / 100, 2)

        with trades_lock:
            active_trades.remove(trade)
        wallet_credit(gross_pnl, trade.get('margin', MARGIN))
        add_daily_pnl(gross_pnl)
        record = _log_closed_trade(trade, 'Manual', gross_pnl, current_price)
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
            f"ברוטו: `${record['gross_pnl_usd']:+.2f}` | עמלה: `${record['fees_usd']:.2f}`\n"
            f"*נטו: `${record['net_pnl_usd']:+.2f}`*\n"
            f"💼 {LEVERAGE}x Isolated\n"
            f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
            f"סה\"כ היום: ${round(daily_stats.get('total_pnl', 0), 2):+}"
        )
        print(f"Manual close: {symbol} at {current_price}")

    except Exception as e:
        send_msg(f"❌ שגיאה בסגירה: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('close_'))
def handle_close_button(call):
    """מטפל בלחיצה על כפתור '❌ Close Position' — סוגר את העסקה מיד."""
    if not _is_authorized(call.from_user.id):
        return
    try:
        symbol = call.data[len('close_'):]  # e.g. 'BTC/USDT'
        bot.answer_callback_query(call.id, f"🔄 סוגר {symbol}...")

        trade = next((t for t in active_trades if t['symbol'] == symbol), None)
        if not trade:
            bot.answer_callback_query(call.id, f"⚠️ {symbol} כבר נסגר", show_alert=True)
            try:
                bot.edit_message_reply_markup(call.message.chat.id,
                                              call.message.message_id,
                                              reply_markup=None)
            except Exception:
                pass
            return

        ticker        = exchange.fetch_ticker(symbol)
        current_price = ticker['last']
        entry         = trade['entry']
        direction_cb  = trade.get('direction', 'LONG')
        _ps_cb        = trade.get('pos_size', POSITION_SIZE)

        # TP1 was already realized when hit; settle only the remaining filled size.
        _ps_cb      = _remaining_notional(trade)
        raw_pct_cb  = (current_price - entry) / entry * 100
        pnl_pct_cb  = raw_pct_cb if direction_cb == 'LONG' else -raw_pct_cb
        gross_pnl_cb = round(_ps_cb * pnl_pct_cb / 100, 2)

        with trades_lock:
            active_trades.remove(trade)
        wallet_credit(gross_pnl_cb, trade.get('margin', MARGIN))
        record = _log_closed_trade(trade, 'Manual', gross_pnl_cb, current_price)
        daily_stats['close_reasons']['Manual'] += 1
        if record['net_pnl_usd'] >= 0:
            daily_stats['wins'] += 1
        else:
            daily_stats['losses'] += 1
        add_daily_pnl(gross_pnl_cb)
        save_active_trades()
        eq = _get_equity()

        pnl_icon = "📈" if record['net_pnl_usd'] >= 0 else "📉"
        send_msg(
            f"🚪 *Button Close — {symbol}*\n"
            f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
            f"ברוטו: `${record['gross_pnl_usd']:+.2f}` | עמלה: `${record['fees_usd']:.2f}`\n"
            f"{pnl_icon} Net: *${record['net_pnl_usd']:+.2f}*\n"
            f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`"
        )
        # מחק את הכפתור מההודעה המקורית
        try:
            bot.edit_message_reply_markup(call.message.chat.id,
                                          call.message.message_id,
                                          reply_markup=None)
        except Exception:
            pass
        print(f"[ButtonClose] {symbol} closed via button at {current_price}, net={record['net_pnl_usd']:+.2f}")

    except Exception as e:
        print(f"[ButtonClose] Error: {e}")
        try:
            bot.answer_callback_query(call.id, f"❌ שגיאה: {e}"[:200], show_alert=True)
        except Exception:
            pass


@bot.message_handler(commands=['addtrade'])
def handle_addtrade(message):
    """
    /addtrade SOL LONG 83.66   — רושם עסקה ידנית לפי מחיר כניסה שנתן המשתמש.
    /addtrade SOL LONG          — כניסה = מחיר חי מ-Bitget.
    /addtrade SOL               — LONG + מחיר חי (ברירת מחדל).
    """
    if not _is_authorized(message.chat.id):
        return
    try:
        parts = message.text.strip().split()
        # פענוח: /addtrade SYMBOL [DIRECTION] [PRICE]
        if len(parts) < 2:
            send_msg(
                "⚠️ *שימוש:*\n"
                "`/addtrade SOL LONG 83.66` — מחיר ידני\n"
                "`/addtrade SOL LONG` — מחיר חי\n"
                "`/addtrade SOL` — LONG + מחיר חי"
            )
            return

        raw_sym   = parts[1].upper()
        symbol    = raw_sym if '/' in raw_sym else f"{raw_sym}/USDT"
        direction = parts[2].upper() if len(parts) >= 3 and parts[2].upper() in ('LONG', 'SHORT') else 'LONG'

        # מחיר כניסה — ידני או חי
        if len(parts) >= 4 and parts[3].replace('.', '').isdigit():
            entry_price = float(parts[3])
        elif len(parts) == 3 and parts[2].replace('.', '').isdigit():
            entry_price = float(parts[2])
            direction   = 'LONG'
        else:
            ticker      = exchange.fetch_ticker(symbol)
            entry_price = float(ticker['last'])

        # בדיקה: כבר קיים?
        with trades_lock:
            if any(t['symbol'] == symbol for t in active_trades):
                send_msg(f"ℹ️ `{symbol}` כבר קיים בעסקאות פעילות")
                return

        # בדיקת יתרה
        if wallet.get('balance', 0) < MARGIN:
            send_msg(f"⚠️ יתרה נמוכה — נדרש ${MARGIN:.0f} | יש ${wallet.get('balance',0):.2f}")
            return

        # SL / TP — קבועים מ-config
        tgt_m     = se.calc_targets(entry_price, direction)
        sl_pct    = tgt_m['sl_pct']
        tp_pct    = tgt_m['tp_pct']
        sl_price  = tgt_m['sl_price']
        tp_price  = tgt_m['tp_price']
        tp1_price = tgt_m['tp1_price']
        be_price  = tgt_m['be_price']

        trade = {
            'symbol':          symbol,
            'entry':           entry_price,
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
            'peak_price':      entry_price,
            'trailing_sl':     None,
            'score':           0,
            'atr':             0.0,
            'timeframe':       'Manual',
            'rsi':             None,
            'ema200':          None,
            'score_breakdown': 'Manual entry via /addtrade',
            'opened_at':       now_il().isoformat(timespec='seconds'),
            'pos_size':        POSITION_SIZE,
            'margin':          MARGIN,
            'fng_at_entry':    None,
            'scalp':           False,
            'manual':          True,
        }
        place_order(trade, MARGIN)

        dir_label = "🟢 LONG" if direction == 'LONG' else "🔴 SHORT"
        send_msg(
            f"✅ *עסקה נרשמה ידנית*\n"
            f"*{symbol} — {dir_label}*\n\n"
            f"💵 כניסה: `{entry_price:.6g}`\n"
            f"🛑 SL: `{sl_price:.6g}` (-{sl_pct}%)\n"
            f"🎯 TP: `{tp_price:.6g}` (+{tp_pct}%)\n"
            f"💼 {LEVERAGE}x · ${MARGIN:.0f} מרג'ין · ${POSITION_SIZE:.0f} נשלט\n\n"
            + _wallet_opened_summary() + "\n\n"
            + "_מעקב SL/TP/Trailing פעיל_"
        )
        print(f"[/addtrade] Registered: {symbol} {direction} @ {entry_price}")

    except Exception as e:
        send_msg(f"❌ שגיאה ברישום: `{str(e)[:80]}`")
        print(f"[/addtrade] Error: {e}")


@bot.message_handler(commands=['report'])
def handle_report(message):
    send_daily_report()


@bot.message_handler(commands=['exporttrades'])
def handle_export_trades(message):
    """שולח את trade_history.csv ישירות לטלגרם."""
    csv_path = TradeLogger.CSV_PATH
    if not os.path.isfile(csv_path):
        send_msg("📭 אין עדיין קובץ עסקאות — הקובץ נוצר אוטומטית עם סגירת העסקה הראשונה.")
        return
    try:
        with open(csv_path, "rb") as f:
            row_count = sum(1 for _ in open(csv_path, encoding="utf-8")) - 1  # minus header
            bot.send_document(
                CHAT_ID,
                f,
                caption=f"📊 Trade History — {row_count} עסקאות (trade_history.csv)",
            )
    except Exception as e:
        send_msg(f"❌ שגיאה בשליחת הקובץ: {e}")


@bot.message_handler(commands=['rejects'])
def handle_rejects(message):
    """
    Send the rejection log as a Telegram attachment.

    WHY TELEGRAM AND NOT A URL
        The log is written on the deployment's own disk. The dashboard is a
        static Vite build, so a file created at runtime is never part of the
        served bundle, and there is no shell into the deployment. Telegram is
        the only channel that reaches out of that container, and the bot
        already uses it for trade_history.csv.
    """
    try:
        # The log was written as .jsonl until 2026-08-17; keep reading the
        # legacy name so the days collected under it are not orphaned.
        cands = [REJECT_LOG_PATH, REJECT_LOG_PATH + 'l']
        avail = [c for c in cands if os.path.isfile(c)]
        if not avail:
            send_msg(f"No rejection log yet at {REJECT_LOG_PATH}")
            return
        p = max(avail, key=lambda c: os.path.getsize(c))
        n = sum(1 for _ in open(p, encoding="utf-8"))
        scored = 0
        for line in open(p, encoding="utf-8"):
            try:
                if (json.loads(line).get("score") or 0) > 0:
                    scored += 1
            except Exception:
                pass
        with open(p, "rb") as f:
            bot.send_document(
                CHAT_ID, f,
                caption=f"rejected_log - {n} rows, {scored} with a real score"
            )
    except Exception as e:
        send_msg(f"rejects export failed: {e}")


@bot.message_handler(commands=['audit'])
def handle_audit(message):
    """שולח דוח ניתוח AI מלא (Gemini) — פירוט עסקאות + המלצות.

    שימוש:
      /audit           — 24 שעות אחרונות (ברירת מחדל)
      /audit DD.MM     — יום ספציפי, שנה נוכחית  (למשל /audit 21.5)
      /audit DD.MM.YYYY — יום + שנה מלאה         (למשל /audit 21.5.2026)
    """
    date_filter = None
    date_label  = "24 שעות אחרונות"

    parts = message.text.strip().split()
    if len(parts) > 1:
        raw = parts[1]
        try:
            segments = raw.split('.')
            day   = int(segments[0])
            month = int(segments[1])
            year  = int(segments[2]) if len(segments) > 2 else now_il().year
            from datetime import date as _date
            date_filter = _date(year, month, day).strftime('%Y-%m-%d')
            date_label  = f"{day:02d}/{month:02d}/{year}"
        except Exception:
            send_msg(
                f"⚠️ תאריך לא תקין: `{raw}`\n"
                f"שימוש: `/audit DD.MM` — למשל `/audit 21.5`"
            )
            return

    send_msg(f"🤖 _מריץ ניתוח Gemini עבור {date_label}\\.\\.\\. עד 30 שניות_")
    try:
        from gdrive_reporter import run_audit_upload
        # trade_audit_log — נשמר ל-object storage, שורד הפעלות מחדש (עד 100 עסקאות).
        # closed_trades_log — זיכרון בלבד, מתאפס בהפעלה מחדש. לכן תמיד נשתמש ב-audit.
        source_trades = trade_audit_log
        run_audit_upload(
            active_trades, wallet, source_trades,
            send_telegram=send_msg,
            date_filter=date_filter,
            date_label=date_label,
        )
    except Exception as e:
        send_msg(f"⚠️ שגיאה בדוח AI: `{str(e)[:100]}`")

def _claude_news_analysis(news_text: str, active_symbols: list) -> dict | None:
    """
    Primary news analyzer — Claude Haiku.
    מחזיר dict בפורמט זהה ל-Gemini: {coins, sentiment, confidence, affected_trades, action, summary}
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return None
    active_str = ', '.join(active_symbols) if active_symbols else 'none'
    prompt = (
        "You are a senior crypto trading analyst with deep geopolitical/macro context. "
        "Analyze the news and respond with ONLY a single JSON object on ONE line — no markdown, no commentary.\n\n"
        f"NEWS:\n{news_text[:1800]}\n\n"
        f"ACTIVE BOT POSITIONS: {active_str}\n\n"
        "Required JSON format (one line, valid JSON only):\n"
        '{"coins":["BTC","ETH"],"sentiment":"bullish","confidence":85,'
        '"affected_trades":["BTC/USDT"],"action":"hold","summary":"<Hebrew summary, 1-2 sentences>"}\n\n'
        "Rules:\n"
        "- coins: array of crypto symbols (BTC, ETH, SOL, etc.) directly impacted\n"
        "- sentiment: bullish | bearish | neutral\n"
        "- confidence: integer 0-100 — how strongly the news affects crypto markets\n"
        "- affected_trades: ONLY symbols from ACTIVE BOT POSITIONS list above (or empty array)\n"
        "- action: hold | tighten_sl | consider_exit | consider_entry\n"
        "- summary: short Hebrew explanation of trading impact (max 200 chars)\n\n"
        "Output the JSON object only — nothing else."
    )
    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        raw = raw.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        s, e = raw.find('{'), raw.rfind('}')
        if s == -1 or e == -1:
            print(f"[News/Claude] no JSON found: {raw[:120]}")
            return None
        data = json.loads(raw[s:e+1])
        print(f"[News/Claude] OK: coins={data.get('coins')} sentiment={data.get('sentiment')} conf={data.get('confidence')}")
        return data
    except Exception as e:
        print(f"[News/Claude] Exception: {e}")
        return None


def _analyze_news(news_text: str, active_symbols: list) -> tuple[dict | None, str]:
    """
    Hybrid news analysis: Claude → Gemini fallback.
    מחזיר (data_dict, source) — source הוא 'Claude' / 'Gemini' / '' אם נכשל.
    """
    data = _claude_news_analysis(news_text, active_symbols)
    if data:
        return data, 'Claude'
    data = _gemini_news_analysis(news_text, active_symbols)
    if data:
        return data, 'Gemini'
    return None, ''


def _gemini_news_analysis(news_text: str, active_symbols: list) -> dict | None:
    """
    Fallback news analyzer — Gemini Flash (חינמי דרך Replit AI Integrations).
    מחזיר dict מפוענח, או None בשגיאה.
    """
    if not GEMINI_URL or not GEMINI_KEY:
        return None

    active_str = ', '.join(active_symbols) if active_symbols else 'none'
    # פרומפט קומפקטי — JSON שורה אחת בלי הערות
    prompt = (
        "You are a crypto trading analyst. Analyze the following news and respond with "
        "ONLY a single compact JSON object on ONE line, no markdown, no comments.\n\n"
        f"News: {news_text[:1500]}\n\n"
        f"Active bot trades: {active_str}\n\n"
        'Required JSON format (one line): '
        '{"coins":["BTC"],"sentiment":"bullish","confidence":70,'
        '"affected_trades":["BTC/USDT"],"action":"hold",'
        '"summary":"brief Hebrew summary of trading impact"}'
        '\n\nsentiment must be: bullish / bearish / neutral\n'
        'action must be: hold / tighten_sl / consider_exit / consider_entry\n'
        'affected_trades: only from the active trades list above, or empty array\n'
        'Respond ONLY with the JSON line. Nothing else.'
    )

    try:
        url  = f'{GEMINI_URL}/models/gemini-2.5-flash:generateContent'
        hdrs = {'x-goog-api-key': GEMINI_KEY, 'Content-Type': 'application/json'}
        body = {
            'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
            'generationConfig': {
                'maxOutputTokens': 2048,
                'temperature': 0.2,
                'thinkingConfig': {'thinkingBudget': 0}  # מנטרל thinking — חוסך טוקנים, מהיר יותר
            }
        }
        resp = requests.post(url, headers=hdrs, json=body, timeout=20)
        if not resp.ok:
            print(f"[News/Gemini] HTTP {resp.status_code}: {resp.text[:100]}")
            return None
        parts = resp.json().get('candidates', [{}])[0].get('content', {}).get('parts', [])
        raw   = ''.join(p.get('text', '') for p in parts).strip()
        # נקה markdown אם קיים
        raw   = raw.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        # מצא את ה-JSON אפילו אם יש טקסט לפניו/אחריו
        start = raw.find('{')
        end   = raw.rfind('}')
        if start != -1 and end != -1:
            raw = raw[start:end+1]
        data = json.loads(raw)
        print(f"[News/Gemini] OK: coins={data.get('coins')} sentiment={data.get('sentiment')}")
        return data
    except Exception as e:
        print(f"[News/Gemini] Exception: {e} | raw={locals().get('raw','?')[:80]}")
        return None


@bot.message_handler(commands=['news'])
def handle_news(message):
    """
    /news <טקסט מהערוץ שלך>
    הבוט מנתח את החדשות עם Gemini ומחזיר:
      - אילו מטבעות מושפעים + סנטימנט
      - האם יש השפעה על עסקאות פעילות
      - המלצת פעולה (hold / הידק SL / שקול יציאה)
    """
    text = message.text.partition('/news')[2].strip()
    if not text:
        send_msg(
            "📰 *שימוש:*\n"
            "`/news <הדבק כאן את הפוסט מהערוץ>`\n\n"
            "_דוגמה:_\n"
            "`/news BlackRock files for Ethereum ETF, market expects approval within weeks`"
        )
        return

    send_msg("🤖 _מנתח חדשות עם Claude AI... שנייה_")

    with trades_lock:
        active_syms = [t['symbol'] for t in active_trades]

    data, source = _analyze_news(text, active_syms)

    if not data:
        send_msg(
            f"📰 עדכון נרשם\n"
            f"{text[:300]}\n\n"
            f"ניתוח AI לא זמין כרגע (Claude + Gemini נכשלו)"
        )
        return

    coins      = data.get('coins', [])
    sentiment  = str(data.get('sentiment', 'neutral')).lower()
    confidence = int(data.get('confidence', 50))
    affected   = data.get('affected_trades', [])
    action     = str(data.get('action', 'hold')).lower()
    summary    = data.get('summary', '')

    sent_icon = '🟢' if sentiment == 'bullish' else ('🔴' if sentiment == 'bearish' else '⚪')
    conf_bar  = '█' * (confidence // 10) + '░' * (10 - confidence // 10)
    coins_str = ', '.join(coins) if coins else 'לא זוהו'

    action_map = {
        'hold':           'המשך להחזיק — החדשות לא משנות את התמונה',
        'tighten_sl':     'שקול להדק SL בעסקאות המושפעות',
        'consider_exit':  'שקול יציאה מוקדמת — חדשות שליליות!',
        'consider_entry': 'הזדמנות כניסה פוטנציאלית — חכה לאישור טכני',
    }
    action_text = action_map.get(action, action)

    msg  = f"📰 ניתוח חדשות — {source} AI\n"
    msg += f"{'━' * 18}\n"
    msg += f"{sent_icon} סנטימנט: {sentiment.upper()} | ביטחון: {confidence}%\n"
    msg += f"{conf_bar}\n"
    msg += f"🪙 מטבעות: {coins_str}\n"
    msg += f"{'━' * 18}\n"
    msg += f"📝 {summary}\n"
    msg += f"{'━' * 18}\n"

    if affected:
        msg += f"🎯 עסקאות מושפעות: {', '.join(affected)}\n"
        msg += f"➡️ {action_text}\n"
    else:
        msg += f"✅ אין עסקאות פעילות מושפעות\n➡️ {action_text}\n"

    send_msg(msg)

    # אם נדרשת פעולה — הצג כפתור Close לכל עסקה מושפעת
    if action in ('tighten_sl', 'consider_exit') and affected:
        for sym in affected:
            with trades_lock:
                trade = next((t for t in active_trades if t['symbol'] == sym), None)
            if trade:
                sl_cur = trade.get('sl', 0)
                entry  = trade.get('entry', 0)
                try:
                    bot.send_message(
                        CHAT_ID,
                        f"עסקה מושפעת: {sym}\nכניסה: {entry:.6g} | SL: {sl_cur:.6g}",
                        reply_markup=_make_close_markup(sym)
                    )
                except Exception:
                    pass

    print(f"[News] coins={coins} sentiment={sentiment} action={action}")


@bot.message_handler(commands=['dashboard'])
def handle_dashboard(message):
    send_msg(
        f"🖥 *דאשבורד הבוט*\n\n"
        f"[👉 לחץ כאן לפתיחת הדאשבורד]({DASHBOARD_URL})\n\n"
        f"_תראה שם: Top 15 מועמדים חמים, גיינרים, ווליום ועוד_"
    )

def evening_sol_analysis() -> tuple:
    """
    SOL LONG Strategy — Momentum Breakout (עודכן).
    תנאי כניסה:
      1. BTC/USDT 15m: price > EMA20 (מגמה חיובית)
      2. SOL/USDT 1H close > recent 4H high (breakout מאושר)
    SL  = -3.5% | TP = +10.5% (RR 1:3) | Trailing 2.5%

    מחזיר: (msg: str, execute: bool, price: float, sl: float, tp: float, rsi: float|None)
    """
    # ── BTC Correlation Filter (15m EMA20) ────────────────────────────────
    btc_above_ema = _btc_above_ema20_15m()

    df_btc_15m = get_data('BTC/USDT', timeframe='15m', limit=25)
    ema20_series = ta.ema(df_btc_15m['close'], length=20)
    btc_close = float(df_btc_15m['close'].iloc[-1])
    btc_ema20 = round(float(ema20_series.iloc[-1]), 2) if ema20_series is not None else 0

    # ── SOL 1H Breakout above recent 4H High ──────────────────────────────
    breakout, sol_1h_close, h4_high = _sol_1h_breakout_above_4h_high()

    # current_price מנר 1H
    df_sol_1h     = get_data('SOL/USDT', timeframe='1h', limit=5)
    current_price = float(df_sol_1h['close'].iloc[-1])

    # RSI 4H — לצרכי display
    df_sol_4h = get_data('SOL/USDT', timeframe='4h', limit=20)
    rsi_s = ta.rsi(df_sol_4h['close'], length=14)
    rsi   = round(float(rsi_s.iloc[-1]), 2) if rsi_s is not None else None

    # ── SL / TP ────────────────────────────────────────────────────────────
    sl     = round(current_price * 0.965, 3)    # -3.5%
    tp     = round(current_price * 1.105, 3)    # +10.5% (RR 1:3)
    sl_usd = round(current_price - sl, 3)
    tp_usd = round(tp - current_price, 3)
    rr     = round(tp_usd / sl_usd, 2) if sl_usd > 0 else 0

    now_str = now_il().strftime('%H:%M')

    # ── לוגיקת החלטה ──────────────────────────────────────────────────────
    if not btc_above_ema:
        execute   = False
        decision  = 'ABORT'
    elif breakout:
        execute   = True
        decision  = 'EXECUTE'
    else:
        execute   = False
        decision  = 'WAIT'

    # ── אייקונים ───────────────────────────────────────────────────────────
    btc_icon      = "✅" if btc_above_ema else "❌"
    breakout_icon = "✅" if breakout      else "❌"

    btc_label = (
        f"`${btc_close:,.2f}` > EMA20 `${btc_ema20:,.2f}` {btc_icon}"
        if btc_above_ema else
        f"`${btc_close:,.2f}` < EMA20 `${btc_ema20:,.2f}` {btc_icon}"
    )
    breakout_label = (
        f"`${sol_1h_close:.3f}` > 4H High `${h4_high:.3f}` {breakout_icon}"
        if breakout else
        f"`${sol_1h_close:.3f}` ≤ 4H High `${h4_high:.3f}` {breakout_icon}"
    )

    # ── בניית הודעה ───────────────────────────────────────────────────────
    if decision == 'EXECUTE':
        dec_line = "🟢 *SOL LONG — EXECUTE* ✅"
        verdict  = "_כל תנאי הפריצה אושרו — עסקה מאושרת_"
    elif decision == 'ABORT':
        dec_line = "🔴 *SOL LONG — ABORT* ⛔"
        verdict  = "_BTC במגמה שלילית — לא נכנסים_"
    else:
        dec_line = "🟡 *SOL LONG — WAIT* ⏳"
        fails = []
        if not btc_above_ema:
            fails.append(f"BTC מתחת EMA20 \\(מגמה שלילית\\)")
        if not breakout:
            fails.append(f"SOL עוד לא פרץ מעל 4H High \\(`${h4_high:.3f}`\\)")
        verdict = "⏳ *ממתין לתנאים:*\n" + "\n".join(f"  • {f}" for f in fails)

    msg = (
        f"{dec_line}\n"
        f"⏰ {now_str} \\| SOL/USDT\n"
        f"{'─' * 28}\n\n"
        f"💰 *מחיר SOL 1H: `${current_price:.3f}`*\n"
        f"📊 RSI 4H: `{rsi}`\n\n"
        f"🔍 *תנאי כניסה \\(Breakout Strategy\\)*\n"
        f"  ₿  BTC 15m > EMA20: {btc_label}\n"
        f"  📈 1H פריצה > 4H High: {breakout_label}\n\n"
        f"{'─' * 28}\n"
        f"📐 *רמות עסקה*\n"
        f"  🟢 כניסה: `${current_price:.3f}`\n"
        f"  🛑 SL \\(\\-3\\.5%\\): `${sl:.3f}` \\(\\-${sl_usd:.3f}\\)\n"
        f"  🎯 TP \\(\\+10\\.5%\\): `${tp:.3f}` \\(\\+${tp_usd:.3f}\\)\n"
        f"  📍 Trailing: {TRAIL_PCT}% מהשיא\n"
        f"  ⚖️ RR: 1 : {rr}\n\n"
        f"{verdict}"
    )
    return msg, execute, current_price, sl, tp, rsi


def _register_sol_trade(price: float, sl: float, tp: float, rsi: float | None):
    """
    רושם עסקת SOL כ-Active Trade בארנק הוירטואלי.
    משתמש בפרמטרי SL/TP של Evening SOL Strategy (3.5% / 5%).
    מדלג אם SOL/USDT כבר בעסקאות פעילות.
    """
    sym = 'SOL/USDT'
    with trades_lock:
        if any(t['symbol'] == sym for t in active_trades):
            send_msg("ℹ️ *SOL/USDT* כבר קיים בעסקאות פעילות — לא נפתחת עסקה כפולה")
            return
    if wallet.get('balance', 0) < MARGIN:
        send_msg(f"⚠️ *יתרה נמוכה* — נדרש ${MARGIN} | יש ${wallet.get('balance', 0):.2f}")
        return

    # ── Fixed SL/TP (2% / 2% / 4%) — same rule as all other strategies ────────
    # -- Gates: this path previously bypassed every one of them --
    if check_daily_circuit_breaker():
        print("[SOL] Circuit Breaker - skipping", flush=True)
        return
    with trades_lock:
        if len(active_trades) >= MAX_TRADES:
            print("[SOL] MAX_TRADES reached - skipping", flush=True)
            return

    _sol_ok, _sol_reason = is_direction_allowed('LONG', context='SOL')
    if not _sol_ok:
        print(f"[SOL] blocked - {_sol_reason}", flush=True)
        return

    _sol_fng, _, _ = sentiment_check("sol_open")
    _sol_regime, _, _sol_btc_above, _ = get_market_regime()
    _sol_gate_ok, _sol_cscore, _sol_creason, _ = claude_gate.claude_trade_gate(
        symbol=sym, direction='LONG', strategy='SOL',
        price=price, bot_score=100,
        regime=_sol_regime, fng=int(_sol_fng or 50), btc_above_ema=_sol_btc_above,
        rsi=rsi, reason='SOL breakout',
        daily_pnl=daily_stats.get('total_pnl', 0.0),
        open_trades=len(active_trades),
    )
    _sol_combined = claude_gate.combined_score(100, _sol_cscore)
    # Use adaptive threshold to match the scoring gate (not hardcoded MIN_SCORE)
    _eff_min_sol, _, _, _ = adaptive_threshold(_sol_fng or 50, _sol_regime, 'LONG')
    print(f"[ClaudeGate/SOL] {sym} | bot=100 claude={_sol_cscore} "
          f"combined={_sol_combined} eff_min={_eff_min_sol} | {_sol_creason}", flush=True)
    # combined ≥ 80: strong technical signal overrides Claude's binary veto (advisory only)
    if _sol_combined < _eff_min_sol or (not _sol_gate_ok and _sol_combined < 80):
        print(f"[ClaudeGate] {sym} SOL rejected - combined={_sol_combined} eff_min={_eff_min_sol}", flush=True)
        return

    tgt_sol   = se.calc_targets(price, 'LONG')
    sl        = tgt_sol['sl_price']    # entry × 0.98
    tp        = tgt_sol['tp_price']    # entry × 1.04
    tp1_price = tgt_sol['tp1_price']   # entry × 1.02
    be_price  = tgt_sol['be_price']    # = entry (SL moves here on TP1)
    sl_pct    = tgt_sol['sl_pct']      # 2.0
    tp_pct    = tgt_sol['tp_pct']      # 4.0
    tp1_pct   = tgt_sol['tp1_pct']     # 2.0

    # ── ATR חישוב + נתונים לגרף ─────────────────────────────────────────
    sol_atr = 0.0
    df_sol  = None
    try:
        df_sol = get_data(sym, timeframe='1h', limit=80)
        atr_s  = ta.atr(df_sol['high'], df_sol['low'], df_sol['close'], length=14)
        if atr_s is not None and not atr_s.isna().all():
            sol_atr = round(float(atr_s.iloc[-1]), 8)
    except Exception:
        pass

    trade = {
        'symbol':          sym,
        'entry':           price,
        'sl':              sl,
        'tp':              tp,
        'tp1':             tp1_price,
        'be_lvl':          be_price,
        'sl_pct':          sl_pct,
        'tp_pct':          tp_pct,
        'direction':       'LONG',
        'phase':           'initial',
        'be_triggered':    False,
        'tp1_triggered':   False,
        'tp1_pnl':         0.0,
        'peak_price':      price,
        'trailing_sl':     None,
        'score':           100,
        'atr':             sol_atr,
        'timeframe':       '4H',
        'rsi':             round(rsi, 2) if rsi else None,
        'ema200':          None,
        'score_breakdown': 'SOL Breakout — 1H close > 4H High + BTC EMA20',
        'opened_at':       now_il().isoformat(timespec='seconds'),
        'pos_size':        POSITION_SIZE,
        'margin':          MARGIN,
        'fng_at_entry':    _sol_fng,
        'fng_components':  get_fng_components(),
        'track':           'SOL',
        'scalp':           False,
        'sol_strategy':    True,
    }
    place_order(trade, MARGIN)
    sol_msg = (
        f"✅ *SOL/USDT נרשמה כעסקה פעילה* 🟢\n\n"
        f"💵 כניסה: `{price:.3f}`\n"
        f"🛑 SL: `{sl:.3f}` (-{sl_pct}%)\n"
        f"🎯 TP: `{tp:.3f}` (+{tp_pct}%)\n"
        f"💼 {LEVERAGE}x · ${MARGIN:.0f} מרג'ין · ${POSITION_SIZE:.0f} נשלט\n\n"
        + _wallet_opened_summary() + "\n\n"
        + "_מעקב SL/TP פעיל — יתרה תעודכן אוטומטית_"
    )
    sol_chart = generate_chart(df_sol, sym, price, sl, tp, 'LONG') \
                if df_sol is not None else None
    send_chart_alert(sol_chart, sym, sol_msg)
    print(f"[SOL] Trade registered: entry={price} SL={sl} TP={tp} RSI={rsi}")


@bot.message_handler(commands=['sol'])
def handle_sol(message):
    """
    /sol — ניתוח חי של SOL/USDT לפי Breakout Strategy.
    תנאים: BTC 15m > EMA20 AND SOL 1H close > recent 4H High.
    כשמוחלט EXECUTE — רושם מיד כעסקה פעילה בארנק הוירטואלי.
    """
    if not _is_authorized(message.chat.id):
        return
    send_msg("🔭 *SOL Breakout Strategy* — מריץ ניתוח חי\\.\\.\\.")

    def _run():
        try:
            msg, execute, price, sl, tp, rsi = evening_sol_analysis()
            send_msg(msg)
            if execute:
                _register_sol_trade(price, sl, tp, rsi)
        except Exception as e:
            print(f"[/sol] error: {e}")
            send_msg(f"⚠️ שגיאה בניתוח SOL: `{str(e)[:80]}`")

    threading.Thread(target=_run, daemon=True).start()


@bot.message_handler(commands=['top10'])
def handle_top10(message):
    """
    /top10 — סריקת Breakout Strategy על 10 המטבעות המובילים.
    אותה לוגיקה כמו /sol: BTC 15m EMA20 filter + 1H close > 4H High.
    """
    send_msg("📡 *Top 20 Breakout Scan* — מריץ ניתוח חי\\.\\.\\.")

    def _run():
        try:
            btc_above_ema = _btc_above_ema20_15m()
            btc_icon      = "✅" if btc_above_ema else "❌"
            now_str       = now_il().strftime('%H:%M')

            if not btc_above_ema:
                send_msg(
                    f"📡 *Top 10 Breakout Scan* — {now_str}\n\n"
                    f"₿ BTC 15m > EMA20: {btc_icon}\n\n"
                    f"🔴 *ABORT — BTC במגמה שלילית*\n"
                    f"_כל הסריקה מבוטלת. ממתין לחזרת BTC מעל EMA20._"
                )
                return

            execute_lines = []
            wait_lines    = []

            for sym in TOP10_SYMBOLS:
                short = sym.replace('/USDT', '')
                try:
                    breakout, price, h4_high, rsi = _coin_1h_breakout_above_4h_high(sym)
                    rsi_str = f" | RSI {rsi:.0f}" if rsi is not None else ""
                    if breakout:
                        execute_lines.append(
                            f"🟢 *{short}* — `${price:.4g}` > 4H High `${h4_high:.4g}`{rsi_str}"
                        )
                    else:
                        gap_pct = round((h4_high - price) / h4_high * 100, 1) if h4_high > 0 else 0
                        wait_lines.append(
                            f"🟡 {short} — פחות {gap_pct}% מהשיא{rsi_str}"
                        )
                except Exception:
                    wait_lines.append(f"⚠️ {short} — שגיאת נתונים")

            parts = [
                f"📡 *Top 10 Breakout Scan* — {now_str}\n"
                f"₿ BTC 15m > EMA20: {btc_icon}\n"
                f"{'─' * 28}"
            ]

            if execute_lines:
                parts.append(f"\n🚀 *פורצים עכשיו ({len(execute_lines)}):*\n" + "\n".join(execute_lines))
            else:
                parts.append("\n🚀 *פורצים עכשיו:* אין")

            if wait_lines:
                parts.append(f"\n⏳ *ממתינים ({len(wait_lines)}):*\n" + "\n".join(wait_lines))

            active_syms = {t['symbol'].replace('/USDT','') for t in active_trades}
            if active_syms:
                parts.append(f"\n🔒 *עסקאות פתוחות:* {', '.join(sorted(active_syms))}")
            parts.append(f"\n{'─' * 28}\n🤖 _כניסה אוטומטית פעילה — Thread 7 סורק כל 15 דקות_")
            send_msg("\n".join(parts))

        except Exception as e:
            print(f"[/top10] error: {e}")
            send_msg(f"⚠️ שגיאה בסריקת Top 10: `{str(e)[:80]}`")

    threading.Thread(target=_run, daemon=True).start()


@bot.message_handler(commands=['fillslots'])
def handle_fillslots(message):
    """
    /fillslots              — ממלא slots פנויים מ-TOP10_SYMBOLS (לפי Breakout Strategy).
    /fillslots ETH LINK BNB — ממלא ממטבעות ספציפיים שצוינו.

    מרג'ין: 10% מהיתרה הפנויה לכל עסקה.
    אם יתרה < $30: 7% במקום 10%.
    פותח עסקה רק אם 1H close > 4H High (LONG) או < 4H Low (SHORT),
    לפי כיוון שנקבע מ-FNG + BTC EMA20.
    """
    if not _is_authorized(message.chat.id):
        return
    parts = message.text.strip().split()
    # מטבעות ספציפיים אם צוינו, אחרת TOP10
    if len(parts) > 1:
        requested = [p.upper() for p in parts[1:]]
        symbols   = [f"{s}/USDT" if '/USDT' not in s else s for s in requested]
    else:
        symbols = list(TOP10_SYMBOLS)

    def _run():
        try:
            open_slots = MAX_TRADES - len(active_trades)
            if open_slots <= 0:
                send_msg(f"⚠️ *כל {MAX_TRADES} הסלוטים תפוסים* — אין מקום לעסקאות חדשות.")
                return

            avail = wallet.get('balance', STARTING_BALANCE)
            if avail < 5:
                send_msg(f"⚠️ *יתרה נמוכה מדי* (${avail:.2f}) — לא ניתן לפתוח עסקאות.")
                return

            margin = MARGIN  # always $50 — fixed for all strategies

            # קביעת כיוון
            btc_above_ema     = _btc_above_ema20_15m()
            fng_v, fng_lbl, _ = sentiment_check("fillslots")
            if fng_v is None:
                send_msg("⚠️ *לא ניתן לקרוא FNG* — נסה שוב.")
                return

            direction = _breakout_determine_direction(btc_above_ema, fng_v)
            if direction is None:
                btc_lbl = "✅ BTC > EMA20" if btc_above_ema else "❌ BTC < EMA20"
                send_msg(
                    f"⚠️ *סיגנלים מנוגדים — לא ניתן לפתוח*\n"
                    f"FNG={fng_v} ({fng_lbl}) | {btc_lbl}\n\n"
                    f"_נדרש: BTC > EMA20 + FNG ≥ 20 לLONG, או BTC < EMA20 + FNG ≤ 65 לSHORT._"
                )
                return

            # ── Trend Bias: BTC > EMA200 על 1H+4H → חסום SHORTs ─────────────
            if direction == 'SHORT':
                _sup_fs, _e1h_fs, _e4h_fs = is_btc_strong_uptrend()
                if _sup_fs:
                    send_msg(
                        f"🟢 *Trend Bias — FillSlots חסום*\n"
                        f"BTC מעל EMA200 על 1H+4H — שוק עולה מאקרו.\n"
                        f"_לא פותחים SHORTs נגד הטרנד. ממתינים לסיגנל LONG._"
                    )
                    return

            now_str       = now_il().strftime('%H:%M')
            dir_emoji     = "🚀" if direction == 'LONG' else "🩸"
            existing_syms = {t['symbol'] for t in active_trades}

            send_msg(
                f"{dir_emoji} *FillSlots — {direction}* — {now_str}\n"
                f"💰 יתרה: ${avail:.2f} | מרג'ין: ${margin:.0f} (קבוע) | Slots: {open_slots}\n"
                f"😨 FNG: {fng_v} | ₿ BTC: {'מעל' if btc_above_ema else 'מתחת'} EMA20\n"
                f"_סורק {len(symbols)} מטבעות..._"
            )

            opened  = 0
            skipped = []

            for sym in symbols:
                if opened >= open_slots:
                    break
                if len(active_trades) >= MAX_TRADES:
                    break
                if sym in existing_syms:
                    skipped.append(f"⏭ {sym.replace('/USDT','')} — עסקה פתוחה")
                    continue

                signal, price, h4_level, rsi, vol_ratio = _coin_breakout_full(sym, direction)

                if not signal:
                    gap = abs(price - h4_level) / h4_level * 100
                    cmp = "מתחת" if direction == 'LONG' else "מעל"
                    skipped.append(
                        f"🟡 {sym.replace('/USDT','')} — {cmp} {('4H High' if direction=='LONG' else '4H Low')} ב-{gap:.1f}%"
                    )
                    continue

                # RSI Filter
                rsi_ok = True
                if direction == 'LONG' and rsi is not None and rsi > RSI_VETO_LONG:
                    skipped.append(f"🔶 {sym.replace('/USDT','')} — RSI {rsi:.0f} גבוה מדי (>{RSI_VETO_LONG})")
                    rsi_ok = False
                elif direction == 'SHORT' and rsi is not None and rsi < RSI_VETO_SHORT:
                    skipped.append(f"🔶 {sym.replace('/USDT','')} — RSI {rsi:.0f} נמוך מדי (<{RSI_VETO_SHORT})")
                    rsi_ok = False
                if not rsi_ok:
                    continue

                # פתיחת עסקה
                open_breakout_trade(
                    symbol    = sym,
                    price     = price,
                    margin    = MARGIN,  # fixed — open_breakout_trade overrides anyway
                    rsi       = rsi,
                    vol_ratio = vol_ratio,
                    h4_level  = h4_level,
                    fng_v     = fng_v,
                    direction = direction,
                )
                existing_syms.add(sym)
                opened += 1
                time.sleep(1)

            # סיכום
            if opened == 0 and skipped:
                summary = (
                    f"📋 *FillSlots — לא נפתחו עסקאות*\n\n"
                    + "\n".join(skipped[:8])
                    + f"\n\n_הסריקה בדקה {len(symbols)} מטבעות — אף אחד לא עמד בתנאי הפריצה._"
                )
            else:
                summary_lines = [f"✅ *FillSlots הושלם — נפתחו {opened} עסקאות*"]
                if skipped:
                    summary_lines.append(f"\n📋 *דולגו ({len(skipped)}):*\n" + "\n".join(skipped[:6]))
                summary = "\n".join(summary_lines)

            send_msg(summary)

        except Exception as e:
            print(f"[/fillslots] error: {e}")
            send_msg(f"⚠️ שגיאה ב-fillslots: `{str(e)[:100]}`")

    threading.Thread(target=_run, daemon=True).start()


@bot.message_handler(commands=['slots'])
def handle_slots(message):
    """
    /slots       — מציג מספר slots פעיל
    /slots 0     — הפסקה (אין כניסות חדשות, עסקאות פעילות ממשיכות)
    /slots 1–5   — מגדיר מקסימום slots (עסקאות פתוחות בו-זמנית)
    """
    if not _is_authorized(message.chat.id):
        return
    global MAX_TRADES
    parts = message.text.strip().split()

    if len(parts) == 1:
        n_open = len(active_trades)
        bar    = '🟢' * n_open + '⬜' * max(0, MAX_TRADES - n_open)
        send_msg(
            f"💼 *Slot Management*\n\n"
            f"{bar}  `{n_open}/{MAX_TRADES}`\n\n"
            f"  מקסימום slots: *{MAX_TRADES}*\n"
            f"  פתוחות כרגע:  *{n_open}*\n"
            f"  פנויות:        *{max(0, MAX_TRADES - n_open)}*\n\n"
            f"_שנה עם /slots 1–5_"
        )
        return

    try:
        v = int(parts[1])
    except ValueError:
        send_msg("❌ שימוש: `/slots 1` עד `/slots 5`")
        return

    if not (0 <= v <= 5):
        send_msg("❌ הערך חייב להיות בין 0 ל-5.\n_0 = הפסקה (אין כניסות חדשות)_")
        return

    old        = MAX_TRADES
    MAX_TRADES = v
    _save_config()
    n_open     = len(active_trades)
    bar        = '🟢' * n_open + '⬜' * max(0, MAX_TRADES - n_open)

    if v == 0:
        send_msg(
            f"⏸️ *בוט בהפסקה — Slots = 0*\n\n"
            f"{'🟢' * n_open}  {n_open} עסקאות פעילות ממשיכות עד שיסגרו לבד.\n\n"
            f"_אין כניסות חדשות עד שתשנה חזרה עם /slots 3_"
        )
    elif v < n_open:
        send_msg(
            f"💼 *Slots עודכן: {old}→{v}*\n\n"
            f"{bar}  `{n_open}/{MAX_TRADES}`\n\n"
            f"⚠️ יש {n_open} עסקאות פתוחות — *אינן נסגרות*.\n"
            f"עסקאות חדשות יפתחו רק לאחר שהמספר ירד מתחת ל-{v}."
        )
    else:
        send_msg(
            f"✅ *Slots עודכן: {old}→{v}*\n\n"
            f"{bar}  `{n_open}/{MAX_TRADES}`\n\n"
            f"הבוט יפתח עד *{v}* עסקאות בו-זמנית."
        )


@bot.message_handler(commands=['watch'])
def handle_watch(message):
    """
    /watch SOL LONG  — מתחיל מעקב כל 15 דקות
    /watch SOL SHORT — מעקב SHORT
    /watch           — מציג רשימת מעקב פעילה
    """
    if not _is_authorized(message.chat.id):
        return
    parts = message.text.strip().split()

    # /watch ללא פרמטרים — הצג רשימה
    if len(parts) == 1:
        with watch_lock:
            if not watch_list:
                send_msg("🔭 *Watch List ריק*\n_השתמש: /watch SOL LONG_")
                return
            lines = []
            for sym, e in watch_list.items():
                remaining = max(0, int((e['expires_at'] - time.time()) / 3600))
                lines.append(
                    f"  {'🟢' if e['direction']=='LONG' else '🔴'} `{sym}` {e['direction']} "
                    f"— ציון אחרון: *{e.get('last_score', '—')}* | פוקע בעוד {remaining}ש'"
                )
        send_msg("🔭 *Watch List פעיל:*\n" + "\n".join(lines) + "\n\n_/unwatch SOL — להסרה_")
        return

    # /watch SOL או /watch SOL LONG
    raw_symbol = parts[1].upper()
    direction  = parts[2].upper() if len(parts) >= 3 else 'LONG'
    if direction not in ('LONG', 'SHORT'):
        send_msg("⚠️ כיוון חייב להיות LONG או SHORT\n_דוגמה: /watch SOL LONG_")
        return

    symbol = raw_symbol + '/USDT' if '/' not in raw_symbol else raw_symbol

    # רישום ב-watch_list
    entry = {
        'direction':  direction,
        'added_at':   now_il().isoformat(timespec='seconds'),
        'expires_at': time.time() + 24 * 3600,
        'last_score': 0,
    }
    with watch_lock:
        watch_list[symbol] = entry

    dir_emoji = "🟢" if direction == 'LONG' else "🔴"
    sym_clean = symbol.replace('/', '\\/')
    send_msg(
        f"🔭 *Watch הופעל — {sym_clean} {direction}*\n\n"
        f"{dir_emoji} מעקב כל *15 דקות* למשך 24 שעות\n"
        f"📊 עדכון יישלח בכל שינוי של ≥5 נקודות בציון\n"
        f"🚨 התראה מיוחדת אם ציון יגיע ≥90\n\n"
        f"_/unwatch {raw_symbol} להפסקת המעקב_\n"
        f"_מריץ ניתוח ראשוני..._"
    )

    # בדיקה ראשונה מיידית ב-Thread נפרד (לא לחסום את הטלגרם)
    def _first_check():
        time.sleep(2)
        with watch_lock:
            e = watch_list.get(symbol)
        if e:
            _run_watch_check(symbol, e, silent=False)

    threading.Thread(target=_first_check, daemon=True).start()


@bot.message_handler(commands=['unwatch'])
def handle_unwatch(message):
    """/unwatch SOL — מסיר מ-Watch List"""
    if not _is_authorized(message.chat.id):
        return
    parts = message.text.strip().split()
    if len(parts) < 2:
        send_msg("_שימוש: /unwatch SOL_")
        return

    raw_symbol = parts[1].upper()
    symbol = raw_symbol + '/USDT' if '/' not in raw_symbol else raw_symbol

    with watch_lock:
        removed = watch_list.pop(symbol, None)

    if removed:
        send_msg(f"🔭 *Watch הופסק — {symbol.replace('/', '\\/')}*\n_המטבע הוסר מרשימת המעקב_")
    else:
        send_msg(f"⚠️ `{symbol}` לא נמצא ב\\-Watch List")


@bot.message_handler(commands=['fng'])
def handle_fng(message):
    """מציג את הסף הנוכחי של מדד הפחד + הטווח המותר."""
    fng_v, fng_lbl = get_fear_greed()
    status = ""
    if fng_v < EXTREME_FEAR_THRESHOLD:
        status = "😱 Extreme Fear — חוקים קבועים פעילים"
    elif fng_v <= FEAR_THRESHOLD:
        status = "🟠 Fear — חוקים קבועים פעילים"
    elif fng_v >= GREED_THRESHOLD:
        status = "🟢 Greed — חוקים קבועים פעילים"
    else:
        status = "🟡 Neutral — מסחר רגיל"

    send_msg(
        f"📊 *הגדרות מדד הפחד — FNG*\n"
        f"{'─' * 28}\n\n"
        f"📡 *מדד נוכחי:* {fng_v} ({fng_lbl})\n"
        f"⚡ *סטטוס:* {status}\n\n"
        f"*⚙️ ספים פעילים:*\n"
        f"  😱 Extreme Fear: FNG < *{EXTREME_FEAR_THRESHOLD}* (טווח: 5–25)\n"
        f"  🟠 Fear:         FNG ≤ *{FEAR_THRESHOLD}* (טווח: 15–45)\n"
        f"  🟢 Greed:        FNG ≥ *{GREED_THRESHOLD}* (טווח: 55–85)\n\n"
        f"*🔧 לשינוי:*\n"
        f"  `/setfng extreme 15` — שנה Extreme Fear\n"
        f"  `/setfng fear 25`    — שנה Fear\n"
        f"  `/setfng greed 75`   — שנה Greed"
    )


@bot.message_handler(commands=['setfng'])
def handle_setfng(message):
    """שינוי סף מדד הפחד. שימוש: /setfng extreme|fear|greed <ערך>"""
    if not _is_authorized(message.chat.id):
        return
    global EXTREME_FEAR_THRESHOLD, FEAR_THRESHOLD, GREED_THRESHOLD
    parts = message.text.strip().split()

    if len(parts) != 3:
        send_msg(
            "⚠️ *שימוש שגוי*\n\n"
            "```\n"
            "/setfng extreme <5-25>\n"
            "/setfng fear    <15-45>\n"
            "/setfng greed   <55-85>\n"
            "```"
        )
        return

    param = parts[1].lower()
    try:
        val = int(parts[2])
    except ValueError:
        send_msg(f"❌ הערך `{parts[2]}` לא מספר תקין.")
        return

    ranges = {
        'extreme': (5,  25,  'Extreme Fear'),
        'fear':    (15, 45,  'Fear'),
        'greed':   (55, 85,  'Greed'),
    }

    if param not in ranges:
        send_msg(f"❌ פרמטר לא מוכר: `{param}`\nאפשרויות: `extreme`, `fear`, `greed`")
        return

    lo, hi, label = ranges[param]
    if not (lo <= val <= hi):
        send_msg(f"❌ *{label}* חייב להיות בין *{lo}* ל-*{hi}*\nקיבלתי: `{val}`")
        return

    old_val = {'extreme': EXTREME_FEAR_THRESHOLD, 'fear': FEAR_THRESHOLD, 'greed': GREED_THRESHOLD}[param]

    if param == 'extreme':
        EXTREME_FEAR_THRESHOLD = val
    elif param == 'fear':
        FEAR_THRESHOLD = val
    elif param == 'greed':
        GREED_THRESHOLD = val

    _save_fng_settings()
    set_sentiment_thresholds(EXTREME_FEAR_THRESHOLD, FEAR_THRESHOLD, GREED_THRESHOLD)
    fng_v, _ = get_fear_greed()
    print(f"[FNG Settings] {param}={old_val}→{val} by Telegram — שמור לקובץ", flush=True)

    send_msg(
        f"✅ *{label} עודכן ונשמר*\n\n"
        f"  לפני: *{old_val}* → אחרי: *{val}*\n\n"
        f"📊 *מצב נוכחי — FNG={fng_v}:*\n"
        f"  😱 Extreme Fear: FNG < *{EXTREME_FEAR_THRESHOLD}*\n"
        f"  🟠 Fear:         FNG ≤ *{FEAR_THRESHOLD}*\n"
        f"  🟢 Greed:        FNG ≥ *{GREED_THRESHOLD}*\n\n"
        f"_ניתן לשנות שוב עם /setfng_"
    )


@bot.message_handler(commands=['ping'])
def handle_ping(message):
    """בדיקת חיים מהירה."""
    import platform
    env = "PROD 🚀" if IS_DEPLOYED else "DEV 🛠"
    with trades_lock:
        n = len(active_trades)
    send_msg(f"🏓 *Pong!* — בוט פעיל\n"
             f"⚙️ {env} | עסקאות: {n} | Flask: port 8091")


@bot.message_handler(commands=['stop'])
def handle_stop(message):
    """עוצר את כל הסריקות וההודעות האוטומטיות."""
    if not _is_authorized(message.chat.id):
        return
    global _bot_paused
    if _bot_paused:
        send_msg("⚠️ הבוט כבר מושהה\\. שלח /resume להמשך\\.", force=True)
        return
    _bot_paused = True
    with trades_lock:
        n = len(active_trades)
    send_msg(
        f"⛔ *הבוט הושהה*\n"
        f"{'─' * 28}\n\n"
        f"🔇 כל הסריקות והודעות אוטומטיות — *מושבתות*\n"
        f"📂 עסקאות פתוחות: *{n}* \\(לא מנוטרות עד /resume\\)\n\n"
        f"▶️ שלח */resume* להמשך פעולה",
        force=True
    )
    print(f"[BOT] ⛔ הושהה על ידי Telegram /stop — עסקאות פתוחות: {n}")


@bot.message_handler(commands=['resume'])
def handle_resume(message):
    """מחדש את פעולת הבוט לאחר /stop."""
    if not _is_authorized(message.chat.id):
        return
    global _bot_paused
    if not _bot_paused:
        send_msg("ℹ️ הבוט כבר פעיל\\. אין צורך ב\\-/resume\\.", force=True)
        return
    _bot_paused = False
    send_msg(
        f"✅ *הבוט חזר לפעולה*\n"
        f"{'─' * 28}\n\n"
        f"🔔 סריקות והודעות אוטומטיות — *פעילות*\n"
        f"⏱ הסריקה הבאה תתחיל בציקל הקרוב",
        force=True
    )
    print("[BOT] ✅ חודש על ידי Telegram /resume")


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
        f"  /wallet     — סיכום ארנק מלא (יתרה, equity, win rate)\n"
        f"  /report        — דוח יומי מלא (סטטיסטיקות)\n"
        f"  /audit         — דוח ניתוח AI מלא (Gemini) | /audit DD.MM לתאריך ספציפי\n"
        f"  /exporttrades  — הורדת trade\\_history.csv (כל העסקאות)\n"
        f"  /scanreport    — דוח סריקה אחרון\n"
        f"  /ping       — בדיקת חיות הבוט\n\n"
        f"📊 *הגדרות מדד הפחד (FNG)*\n"
        f"  /fng                  — הצג ספים נוכחיים + טווחים מותרים\n"
        f"  /setfng extreme 15    — שנה Extreme Fear (5–25)\n"
        f"  /setfng fear 25       — שנה Fear (15–45)\n"
        f"  /setfng greed 75      — שנה Greed (55–85)\n\n"
        f"📰 *חדשות וניתוח*\n"
        f"  /news <טקסט>      — הדבק פוסט מערוץ → Gemini מנתח + השפעה על עסקאות\n\n"
        f"🔬 *Dr\\. Sniper Research \\(AI אוטונומי\\)*\n"
        f"  /research          — הרץ סריקה עכשיו \\(CoinGecko+TA+web\\)\n"
        f"  /research status   — סטטיסטיקות + מועמדים אחרונים\n"
        f"  /research off      — מצב התראות בלבד \\(ללא ביצוע\\)\n"
        f"  /research on       — חזור לביצוע אוטומטי\n\n"
        f"🧠 *Claude AI Gate*\n"
        f"  /gate              — סטטיסטיקות + 3 החלטות אחרונות של קלוד\n\n"
        f"🔍 *פקודות פעולה*\n"
        f"  /scan              — סריקה ידנית עכשיו\n"
        f"  /close BTC         — סגירת עסקה ידנית\n"
        f"  /addtrade SOL LONG 83.66 — רישום ידני של עסקה פעילה\n"
        f"  /update BTC 84000 95000 — עדכון SL/TP\n\n"
        f"🔭 *Watch — מעקב מטבע ספציפי*\n"
        f"  /watch SOL LONG    — מעקב כל 15 דקות\n"
        f"  /watch SOL SHORT   — מעקב SHORT\n"
        f"  /watch             — רשימת מעקב פעילה\n"
        f"  /unwatch SOL       — הפסקת מעקב\n\n"
        f"📡 *Breakout Strategy*\n"
        f"  /top10     — סריקת Breakout על 20 מטבעות (כולל AI: FET/RENDER, RWA: ONDO)\n"
        f"  /fillslots — מלא slots פנויים מ-20 מטבעות (LONG/SHORT לפי FNG+BTC)\n"
        f"  /fillslots ETH FET RENDER — פתח מטבעות ספציפיים (AI/RWA קודמים)\n"
        f"  /slots     — הצג/שנה מקסימום עסקאות בו-זמנית (כרגע: *{MAX_TRADES}*)\n"
        f"  /sol       — ניתוח SOL חי: BTC EMA20 + 1H > 4H High\n\n"
        f"🖥 *דאשבורד*\n"
        f"  /dashboard — קבל קישור לדאשבורד\n"
        f"  [👉 פתח דאשבורד]({DASHBOARD_URL})\n\n"
        f"{'─' * 30}\n"
        f"⚙️ *פרמטרים*\n"
        f"  📐 סף איתות: *{MIN_SCORE}/100*\n"
        f"  💵 מרג'ין לעסקה: *${MARGIN}*  ·  מינוף: *{LEVERAGE}x*\n"
        f"  🛡️ SL: *{SL_PCT}%*  ·  TP1: *{TP1_PCT}%*  ·  TP2: *{TP2_PCT}%*\n"
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

    # הוסף רמז ל-Sandbox אם קיים
    sandbox = d.get('sandbox_analysis', [])
    if sandbox:
        msg += f"\n{'─' * 28}\n"
        msg += f"🧪 *Sandbox Analysis זמין* — {len(sandbox)} מטבע/ות\n"
        msg += f"_פרטים ממשיכים בהודעה הבאה_"
    elif fng_v < EXTREME_FEAR_THRESHOLD:
        msg += f"\n{'─' * 28}\n"
        msg += f"🧪 *Claude Sandbox:* _Kill\\-Switch פעיל · אין מטבעות Bubble Watch לניתוח_"

    send_msg(msg)

    # ── הודעה נפרדת: Sandbox Analysis (חינוכי בלבד) ────────────────────────────
    if sandbox:
        sandbox_msg  = f"🧪 *Claude's Sandbox Analysis*\n"
        sandbox_msg += f"\\(Educational Only — Kill\\-Switch Active\\)\n"
        sandbox_msg += f"_ניתוח היפותטי בלבד · אין עסקאות נפתחות · לצורכי לימוד_\n"
        sandbox_msg += f"{'─' * 28}\n\n"

        for i, s in enumerate(sandbox, 1):
            sym           = s.get('symbol', '?')
            dir_          = s.get('direction', '')
            chg           = s.get('change_24h', 0)
            verdict       = s.get('verdict', '?')
            analysis      = s.get('analysis', '')
            r4h           = s.get('rsi_4h')
            r1h           = s.get('rsi_1h')
            r15           = s.get('rsi_15m')
            vol           = s.get('volume_ratio')
            score         = s.get('score', 0)
            entry_zone    = s.get('entry_zone')
            entry_reason  = s.get('entry_reason')
            rsi_wait      = s.get('rsi_wait')
            ema200_4h     = s.get('ema200_4h')
            fib_500       = s.get('fib_500')
            fib_618       = s.get('fib_618')
            overextended  = s.get('overextended', False)

            dir_e    = "🟢" if dir_ == 'LONG' else "🔴"
            chg_str  = f"{chg:+.1f}%" if chg is not None else "N/A"
            rsi_str  = (
                f"4H={r4h:.0f} · 1H={r1h:.0f} · 15m={r15:.0f}"
                if None not in (r4h, r1h, r15) else "N/A"
            )
            vol_str = f"{vol:.2f}×" if vol is not None else "N/A"
            ext_tag = " ⚠️ _Overextended_" if overextended else ""

            sandbox_msg += f"*{i}\\. {dir_e} `{sym}`*{ext_tag}\n"
            sandbox_msg += f"📈 שינוי 24h: *{chg_str}*\n"
            sandbox_msg += f"📊 RSI \\(3 TF\\): `{rsi_str}`\n"
            sandbox_msg += f"📦 Volume: `{vol_str}` avg  ·  Score: `{score}/100`\n\n"

            # Claude verdict + analysis
            sandbox_msg += f"🤖 *Claude: {verdict}*\n"
            if analysis:
                analysis_esc = (analysis[:250] + "…") if len(analysis) > 250 else analysis
                for ch in r'_*[]()~`>#+-=|{}.!':
                    analysis_esc = analysis_esc.replace(ch, f'\\{ch}')
                sandbox_msg += f"_{analysis_esc}_\n\n"

            # ── Value Entry Section ──────────────────────────────────────────
            sandbox_msg += f"{'─' * 22}\n"
            sandbox_msg += f"📐 *Value Entry Levels*\n"

            # Pre-computed reference levels
            if ema200_4h:
                ema_str = str(ema200_4h)
                for ch in r'_*[]()~`>#+-=|{}.!':
                    ema_str = ema_str.replace(ch, f'\\{ch}')
                sandbox_msg += f"  EMA 200 \\(4H\\): `{ema_str}`\n"
            if fib_500 and fib_618:
                f5 = str(fib_500)
                f6 = str(fib_618)
                for ch in r'_*[]()~`>#+-=|{}.!':
                    f5 = f5.replace(ch, f'\\{ch}')
                    f6 = f6.replace(ch, f'\\{ch}')
                lbl = "Pullback" if dir_ == 'LONG' else "Bounce"
                sandbox_msg += f"  Fib 0\\.5  \\({lbl}\\): `{f5}`\n"
                sandbox_msg += f"  Fib 0\\.618 \\({lbl}\\): `{f6}`\n"
            sandbox_msg += "\n"

            # Claude's ideal entry zone
            if entry_zone:
                ez = entry_zone
                for ch in r'_*[]()~`>#+-=|{}.!':
                    ez = ez.replace(ch, f'\\{ch}')
                sandbox_msg += f"🎯 *Ideal Entry Zone:* `{ez}`\n"
            if entry_reason:
                er = entry_reason
                for ch in r'_*[]()~`>#+-=|{}.!':
                    er = er.replace(ch, f'\\{ch}')
                sandbox_msg += f"💡 *Reason:* _{er}_\n"
            if rsi_wait:
                rw = rsi_wait
                for ch in r'_*[]()~`>#+-=|{}.!':
                    rw = rw.replace(ch, f'\\{ch}')
                sandbox_msg += f"⏳ *RSI Condition:* _{rw}_\n"

            if i < len(sandbox):
                sandbox_msg += f"\n{'─' * 28}\n\n"

        send_msg(sandbox_msg)


@bot.message_handler(commands=['major'])
def handle_major(message):
    """
    /major — סיכום נוכחי של כל 8 המטבעות הגדולים במעקב.
    מציג מצב (EXECUTE/WAIT/ABORT) + מחיר + RSI + Breakout לכל מטבע.
    """
    now_str = now_il().strftime('%H:%M')
    btc_above_ema = _btc_above_ema20_15m()
    btc_label     = "✅ מעל EMA20" if btc_above_ema else "⛔ מתחת EMA20"

    lines = []
    for symbol in MAJOR_WATCH_COINS:
        try:
            ticker_name = symbol.replace('/USDT', '')
            coin_icon   = MAJOR_WATCH_ICONS.get(ticker_name, '🔹')
            breakout, price_1h, h4_high, rsi = _coin_1h_breakout_above_4h_high(symbol)

            if symbol == 'BTC/USDT':
                try:
                    df_btc4h = get_data('BTC/USDT', timeframe='4h', limit=60)
                    ema50    = ta.ema(df_btc4h['close'], length=50)
                    trend_ok = float(df_btc4h['close'].iloc[-1]) > float(ema50.iloc[-1]) \
                               if ema50 is not None else True
                except Exception:
                    trend_ok = True
            else:
                trend_ok = btc_above_ema

            if not trend_ok:
                decision = 'ABORT'
                dec_icon = '🔴'
            elif breakout:
                decision = 'EXECUTE'
                dec_icon = '🟢'
            else:
                decision = 'WAIT'
                dec_icon = '🟡'

            rsi_str = f" | RSI:{rsi}" if rsi is not None else ""
            bo_str  = f" ✅ Breakout" if breakout else f" ⏳ 4H:{h4_high:.4g}"
            lines.append(f"{dec_icon} *{coin_icon}{ticker_name}* `${price_1h:.4g}`{rsi_str}{bo_str}")

        except Exception as e:
            lines.append(f"⚠️ {symbol.replace('/USDT','')} — שגיאה")

    msg = (
        f"🏦 *Major Coins Watch — {now_str}*\n"
        f"{'─' * 26}\n"
        f"₿ BTC 15m EMA20: {btc_label}\n"
        f"{'─' * 26}\n\n"
        + "\n".join(lines) +
        f"\n\n{'─' * 26}\n"
        f"🟢=EXECUTE | 🟡=WAIT | 🔴=ABORT"
    )
    send_msg(msg)


@bot.message_handler(commands=['scan'])
def handle_scan(message):
    """סריקה מיידית — מופעלת ב-Thread נפרד כדי לא לחסום את ה-polling."""
    if not _is_authorized(message.chat.id):
        return
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
            now_str    = now_il().strftime('%H:%M:%S')
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

            # ── Sandbox Mode (Extreme Fear) ───────────────────────────────────
            sandbox_m = []
            if fng_v_m < EXTREME_FEAR_THRESHOLD and bubble_watch_m:
                print(f"[Sandbox] Running educational analysis (manual scan)...")
                sandbox_m = claude_sandbox_analysis(
                    bubble_watch_m, btc_regime, fng_v_m, fng_lbl_m
                )

            pnl_today = round(daily_stats.get('total_pnl', 0), 2)
            bub_note = ""
            if bubble_watch_m:
                bub_names = ", ".join(
                    f"{b['symbol'].replace('/USDT','')} ({b['change_pct']:+.1f}%)"
                    for b in bubble_watch_m           # כל המטבעות — ללא חיתוך
                )
                bub_note = f"\n🫧 *Bubble Watch ({len(bubble_watch_m)}):* {bub_names}"
            fng_emoji_m = ("😱" if fng_v_m < 13 else
                           "😨" if fng_v_m < 25 else
                           "😟" if fng_v_m < 40 else
                           "😐" if fng_v_m < 60 else
                           "😊" if fng_v_m < 75 else "🤑")
            btc_regime_now = get_btc_regime()
            ks_note_m = ""
            # Unrealized P&L בזמן אמת לסיכום הסריקה
            try:
                unreal_m = _get_unrealized_pnl()
            except Exception:
                unreal_m = 0.0
            unreal_icon_m = "📈" if unreal_m >= 0 else "📉"

            # שורות P&L פר עסקה פתוחה
            per_trade_lines = ""
            for t_m in active_trades:
                dir_m   = t_m.get('direction', 'LONG')
                e_m     = t_m.get('entry', 0)
                cp_m    = t_m.get('current_price', e_m)
                ps_m    = t_m.get('pos_size', POSITION_SIZE)
                if e_m > 0:
                    tpnl_m = round(ps_m * (cp_m - e_m) / e_m, 2) if dir_m == 'LONG' \
                             else round(ps_m * (e_m - cp_m) / e_m, 2)
                    tpnl_icon = "📈" if tpnl_m >= 0 else "📉"
                    per_trade_lines += (
                        f"  {tpnl_icon} {t_m['symbol'].replace('/USDT','')} "
                        f"`{tpnl_m:+.2f}$` @ `{cp_m:.6g}`\n"
                    )

            send_msg(
                f"✅ *סריקה ידנית הושלמה*\n\n"
                f"🔍 נסרקו: *{total_scanned}* מטבעות\n"
                f"📊 איתותים: *{signals_found}*\n"
                f"{fng_emoji_m} Fear & Greed: *{fng_v_m}* — _{fng_lbl_m}_{ks_note_m}\n"
                f"📊 עסקאות פעילות: *{len(active_trades)}*\n"
                + (f"\n*P&L פר עסקה \\(חי\\):*\n{per_trade_lines}" if per_trade_lines else "")
                + f"{unreal_icon_m} Unrealized: *${unreal_m:+.2f}*\n"
                f"💰 Realized היום: *${pnl_today:+}*"
                f"{bub_note}\n"
                f"_השתמש ב /status לפרטים מלאים_"
            )

            # שמירת דוח סריקה
            if signals_found == 0 and len(active_trades) >= MAX_TRADES:
                sys_msg_m = f"מקסימום עסקאות ({MAX_TRADES}/{MAX_TRADES}) — ממתין לסגירה"
            elif signals_found == 0:
                top_m = sorted(all_rejections_m, key=lambda x: x.get('best_score', 0), reverse=True)
                best_m = top_m[0] if top_m else None
                _eff_m, _, _, _eff_lbl = adaptive_threshold(fng_v_m, btc_regime, 'LONG')
                sys_msg_m = (
                    f"אף מטבע לא הגיע לסף {_eff_m}/100 [{_eff_lbl}]. הטוב ביותר: {best_m['symbol']} עם {best_m['best_score']}/100" if best_m
                    else f"אף מטבע לא עמד בסף {_eff_m}/100 [{_eff_lbl}]"
                )
            else:
                sys_msg_m = f"{signals_found} עסקה/ות נפתחו — סריקה ידנית"
            save_scan_results(total_scanned, signals_found, fng_v_m, fng_lbl_m,
                              btc_regime, all_rejections_m, sys_msg_m, scan_start_m,
                              bubble_watch=bubble_watch_m,
                              sandbox_analysis=sandbox_m)
        except Exception as e:
            send_msg(f"❌ שגיאה בסריקה: {e}")
        finally:
            _scan_running = False

    threading.Thread(target=run_manual_scan, daemon=True).start()


def _polling_watchdog_loop():
    """
    Watchdog thread — restarts infinity_polling if it silently freezes.
    Telegram long-polling can drop on a network hiccup without raising an exception.
    If no update is received for WATCHDOG_IDLE_SEC, we call bot.stop_polling() which
    causes the while-True loop in start_telegram_polling() to restart the session.
    """
    WATCHDOG_IDLE_SEC = 1800  # 30 min: if polling session hasn't restarted → frozen
    WATCHDOG_CHECK_SEC = 60   # check every minute
    time.sleep(90)            # give the bot time to fully start before first check
    while True:
        time.sleep(WATCHDOG_CHECK_SEC)
        idle = time.time() - _polling_last_activity
        if _polling_last_activity > 0 and idle > WATCHDOG_IDLE_SEC:
            print(f"[WATCHDOG] ⚠️  Polling session frozen for {idle:.0f}s — force-restarting...", flush=True)
            try:
                bot.stop_polling()
            except Exception as _we:
                print(f"[WATCHDOG] stop_polling error (non-fatal): {_we}", flush=True)


def _tg_api_call(token: str, method: str, payload: dict, timeout: int = 12) -> dict:
    """Direct HTTP call to Telegram API with explicit timeout — never hangs."""
    import urllib.request as _urlreq, urllib.error as _urlerr, json as _json
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = _json.dumps(payload).encode('utf-8')
    req  = _urlreq.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with _urlreq.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def start_telegram_polling():
    """
    PROD only — infinity_polling רץ רק כשהבוט פרוס (REPLIT_DEPLOYMENT=1).
    בסביבת DEV מדלגים על polling לחלוטין כדי למנוע קונפליקט 409 עם ה-PROD.
    send_msg() עובד תמיד (HTTP POST ישיר — לא תלוי ב-polling).
    """
    is_deployed = bool(os.environ.get('REPLIT_DEPLOYMENT', ''))
    token       = os.environ.get('TELEGRAM_TOKEN', '')

    if not is_deployed:
        print("[DEV] Telegram polling SKIPPED — Production bot handles commands. "
              "send_msg() still works (direct HTTP POST).", flush=True)
        return

    mode_label = "PROD"

    print(f"[{mode_label}] Polling mode — ממתין 35s לסיום boot...", flush=True)
    time.sleep(35)

    # נקה webhook — מונע 409 Conflict בהפעלה ראשונה
    r = _tg_api_call(token, "deleteWebhook", {"drop_pending_updates": True})
    print(f"[{mode_label}] deleteWebhook: {r}", flush=True)
    time.sleep(3)

    _backoff = 3   # זמן המתנה בין restarts — מתחיל ב-3s, עולה עד 15s

    while True:
        global _polling_last_activity
        _polling_last_activity = time.time()
        try:
            print(f"[{mode_label}] polling starting (backoff={_backoff}s)...", flush=True)
            bot.polling(
                non_stop=True,
                timeout=25,
                long_polling_timeout=15,
                logger_level=None,
            )
            # הגענו לכאן רק אם polling() חזר בלי exception — מצב נדיר
            print(f"[{mode_label}] polling returned normally — restart in {_backoff}s", flush=True)
            time.sleep(_backoff)
            _backoff = min(_backoff + 2, 15)   # backoff רך: 3→5→7→…→15

        except Exception as e:
            err_str = str(e)
            print(f"[{mode_label}] polling exception: {err_str[:300]}", flush=True)
            if '409' in err_str:
                # 409 = עוד בוט מחובר — מנקה ומנסה שוב
                print(f"[{mode_label}] 409 Conflict — deleteWebhook + restart in 15s", flush=True)
                _tg_api_call(token, "deleteWebhook", {"drop_pending_updates": True})
                time.sleep(15)
                _backoff = 3
            elif '401' in err_str:
                print(f"[{mode_label}] 401 Unauthorized — TELEGRAM_TOKEN שגוי!", flush=True)
                time.sleep(60)
            else:
                print(f"[{mode_label}] polling error — restart in {_backoff}s", flush=True)
                time.sleep(_backoff)
                _backoff = min(_backoff + 2, 15)

# --- לולאת מעקב עסקאות — Thread נפרד ---

def trade_monitor_loop():
    """
    רץ בThread נפרד.
    בודק SL / TP / BE / Trailing כל 60 שניות — ללא תלות בסריקה.
    שולח Heartbeat כל 30 דקות כשיש עסקאות פעילות.
    בודק שינוי מצב FNG בכל איטרציה.
    """
    print("Trade monitor started — checking every 60s")
    while True:
        if not _bot_paused:
            try:
                # ── FNG state change detection ──
                check_fng_state_change()

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
    Thin wrapper around the scan that persists whatever it rejected.

    WHY HERE AND NOT IN save_scan_results
        save_scan_results() has not run in this instance since 2026-05-17
        (last_scan_results.json is that old), so a hook there never fires.
        This wrapper sits on the function that actually produces rejections,
        and the try/finally catches every early return - regime veto, the
        parabolic-bull veto, and the normal path.

    Never raises: a logging failure must not be able to abort a scan.
    """
    if rejected_out is None:
        rejected_out = []
    _before = len(rejected_out)
    try:
        return _scan_batch_inner(candidates, direction, btc_regime, rejected_out)
    finally:
        try:
            _new = rejected_out[_before:]
            _fng = _market_regime_cache.get('fng_v', 0)
            print(f"[RejectLog] batch {direction}: {len(_new)} rejections, "
                  f"{len(candidates) if candidates else 0} candidates", flush=True)
            log_rejections(_new, _fng, btc_regime)
        except Exception as _le:
            print(f"[RejectLog] wrapper error: {type(_le).__name__} {_le}", flush=True)


def _scan_batch_inner(candidates, direction, btc_regime='NEUTRAL', rejected_out=None):
    """
    עוזר לסריקה: מריץ score_symbol על רשימת מועמדים.
    direction: 'LONG' או 'SHORT'
    btc_regime: פרמטר legacy — הלוגיקה עברה ל-get_market_regime() / is_direction_allowed()
    rejected_out: רשימה שבה יצטברו מטבעות שנדחו (לדוח הסריקה)
    מחזיר מספר האיתותים שנמצאו.
    """
    if rejected_out is None:
        rejected_out = []

    # ── Sentiment ─────────────────────────────────────────────────────────────
    fng_v_scan, fng_lbl_scan, fng_action = sentiment_check("scan")

    # ── Adaptive Threshold — FNG + BTC Regime → effective entry bar ───────────
    eff_min, eff_rsi_long, eff_rsi_short, eff_label = adaptive_threshold(
        fng_v_scan, btc_regime, direction
    )
    print(
        f"[AdaptiveThreshold] {direction} | base={MIN_SCORE} → eff={eff_min} "
        f"[{eff_label}] | RSI_L≤{eff_rsi_long} RSI_S≥{eff_rsi_short}",
        flush=True,
    )

    # ── Market Regime Gate (FNG + BTC EMA20 4H) ──────────────────────────────
    _mr_allowed, _mr_reason = is_direction_allowed(direction, context='Scan')
    if not _mr_allowed:
        print(f"[Scan] REGIME VETO {direction} ({len(candidates)} candidates) — {_mr_reason}",
              flush=True)
        for c in candidates:
            rejected_out.append({
                'symbol': c['symbol'], 'direction': direction, 'best_score': 0,
                'reason': _mr_reason,
                'scores': {},
            })
        return 0

    # ── BTC Compass Parabolic Bull Filter — EMA200(4H) + RSI(1H)>60 ────────────
    if direction == 'SHORT':
        _parabolic, _rsi_1h, _ema200 = is_btc_parabolic_bull()
        if _parabolic:
            print(f"BTC COMPASS VETO: PARABOLIC BULL — RSI1H={_rsi_1h:.1f}>65 above EMA200 → skipping {len(candidates)} SHORT candidates")
            for c in candidates:
                rejected_out.append({
                    'symbol': c['symbol'], 'direction': direction, 'best_score': 0,
                    'reason': f'BTC Parabolic Bull (EMA200 4H above + RSI1H={_rsi_1h:.1f}>65) — SHORTs חסומים',
                    'scores': {},
                })
            return 0

    # ── Async OHLCV + OI Pre-fetch — parallel fetch 4H+1H+15m + OI ─────────────
    global _ohlcv_cache, _oi_cache
    _ohlcv_cache = {}   # clear any stale cache from previous scan
    _oi_cache    = {}   # clear stale OI data from previous scan
    try:
        asyncio.run(_prefetch_ohlcv(candidates, timeframes=['4h', '1h', '15m']))
    except RuntimeError:
        # Already in an event loop (shouldn't happen in scan thread, but safe fallback)
        pass
    except Exception as _ae:
        print(f"[ASYNC] pre-fetch skipped (non-fatal): {_ae}")

    found = 0
    for candidate in candidates:
        at_capacity = (len(active_trades) >= MAX_TRADES)
        if at_capacity:
            print(f"[Slots] 🔒 {len(active_trades)}/{MAX_TRADES} slots full — scanning for priority comparison")
        symbol = candidate['symbol']

        # ── Slow-Movers Blacklist — low-momentum coins skipped ──
        if symbol in SLOW_MOVERS:
            print(f"  [SLOW_MOVERS] {symbol} — blacklisted (low momentum), skipping")
            if rejected_out is not None:
                rejected_out.append({
                    'symbol': symbol, 'direction': direction, 'best_score': 0,
                    'reason': 'Slow-Mover Blacklist (TRX/ADA — low momentum)',
                    'scores': {},
                })
            continue

        # ── Trade Close Cooldown — 2h אחרי FastLoss/SL/ReversalGuard ───────────
        _tcc_ts = trade_close_cooldown.get(symbol, 0)
        if time.time() - _tcc_ts < TRADE_CLOSE_COOLDOWN_SEC:
            _tcc_min = int((TRADE_CLOSE_COOLDOWN_SEC - (time.time() - _tcc_ts)) / 60)
            print(f"  [TradeCooldown] {symbol} — עוד {_tcc_min} דק' (2h אחרי סגירה)", flush=True)
            if rejected_out is not None:
                rejected_out.append({
                    'symbol': symbol, 'direction': direction, 'best_score': 0,
                    'reason': f'Trade Cooldown ({_tcc_min} דק\' נותרו אחרי סגירה)',
                    'scores': {},
                })
            continue
        # ── MaxDuration Cooldown — 6h אחרי MaxDuration (SOL loop prevention) ──
        _mdc_ts = max_duration_cooldown.get(symbol, 0)
        if time.time() - _mdc_ts < MAX_DURATION_COOLDOWN_SEC:
            _mdc_min = int((MAX_DURATION_COOLDOWN_SEC - (time.time() - _mdc_ts)) / 60)
            print(f"  [MaxDurCooldown] {symbol} — עוד {_mdc_min} דק' (6h אחרי MaxDuration)", flush=True)
            if rejected_out is not None:
                rejected_out.append({
                    'symbol': symbol, 'direction': direction, 'best_score': 0,
                    'reason': f'MaxDuration Cooldown ({_mdc_min} דק\' נותרו, 6h)',
                    'scores': {},
                })
            continue

        # ── 1 trade per symbol enforcement ──
        if any(t['symbol'] == symbol for t in active_trades):
            continue

        # ── IG-1: Daily Circuit Breaker ─────────────────────────────────────
        if check_daily_circuit_breaker():
            rejected_out.append({
                'symbol': symbol, 'direction': direction, 'best_score': 0,
                'reason': f'Daily Circuit Breaker: P&L={daily_stats.get("total_pnl",0):.2f} ≤ {DAILY_LOSS_LIMIT}',
                'scores': {},
            })
            continue

        df_15m = None  # אתחול — נטען רק אם 4H+1H לא מספיקים
        try:
            # ── שלב 1: ניסיון על 4H — טרנד ראשי (cache hits from async pre-fetch) ──
            df_4h  = get_data_cached(symbol, timeframe='4h', limit=250)
            df_1h  = get_data_cached(symbol, timeframe='1h', limit=250)
            price  = df_4h['close'].iloc[-1]

            # ── 3-Filter Momentum Gate ────────────────────────────────────────────
            if MOMENTUM_GATE_ENABLED:
                _df_15m_gate = get_data_cached(symbol, timeframe='15m', limit=250)
                _gate_ok, _gate_reason = momentum_gate(
                    symbol=symbol,
                    df_15m=_df_15m_gate,
                    df_1h=df_1h,
                    direction=direction,
                    volume_usd_24h=float(candidate.get('volume_usd', 0)),
                    oi_data=_oi_cache.get(symbol),
                )
                if not _gate_ok:
                    print(f"  [MomentumGate] ❌ {symbol}: {_gate_reason}")
                    rejected_out.append({
                        'symbol':     symbol,
                        'direction':  direction,
                        'best_score': 0,
                        'reason':     f'MomentumGate: {_gate_reason}',
                        'scores':     {'4H': 0, '1H': 0, '15m': 0},
                    })
                    continue
                print(f"  [MomentumGate] ✅ {symbol}: {_gate_reason}")
            # ─────────────────────────────────────────────────────────────────────

            print(f"Scoring {symbol} [{direction}] @ {price:.6g} [4H] | threshold={eff_min} [{eff_label}]")
            score, breakdown, atr = score_symbol(
                df_4h, df_1h, symbol, direction, fng_v=fng_v_scan,
                rsi_veto_long=eff_rsi_long, rsi_veto_short=eff_rsi_short,
                change_24h=candidate.get('change'),
            )
            score_4h  = score
            score_1h  = 0
            score_15m = 0
            best_score     = score
            best_breakdown = breakdown
            chosen_tf = '4H'
            chosen_df = df_4h
            tf_reason = 'טרנד חזק בגרף 4H'

            # ── שלב 2: אם 4H לא מספיק — נסה 1H ──
            if score < eff_min:
                df_15m = get_data_cached(symbol, timeframe='15m', limit=250)
                score_1h, breakdown_1h, atr_1h = score_symbol(
                    df_1h, df_15m, symbol, direction, fng_v=fng_v_scan,
                    rsi_veto_long=eff_rsi_long, rsi_veto_short=eff_rsi_short,
                    change_24h=candidate.get('change'),
                )
                print(f"  4H={score_4h} < {eff_min} → try 1H: {score_1h}")
                if score_1h > best_score:
                    best_score     = score_1h
                    best_breakdown = breakdown_1h
                if score_1h >= eff_min:
                    score     = score_1h
                    breakdown = breakdown_1h
                    atr       = atr_1h
                    chosen_tf = '1H'
                    chosen_df = df_1h
                    price     = df_1h['close'].iloc[-1]
                    tf_reason = f'High Volatility Entry ב-1H (4H={score_4h} < {eff_min})'

                # ── שלב 3: אם גם 1H לא מספיק — נסה 15m (Scalp) ──
                else:
                    score_15m, breakdown_15m, atr_15m = score_symbol(
                        df_15m, df_1h, symbol, direction, fng_v=fng_v_scan,
                        rsi_veto_long=eff_rsi_long, rsi_veto_short=eff_rsi_short,
                        change_24h=candidate.get('change'),
                    )
                    print(f"  1H={score_1h} < {eff_min} → try 15m: {score_15m}")
                    if score_15m > best_score:
                        best_score     = score_15m
                        best_breakdown = breakdown_15m
                    if score_15m >= eff_min:
                        score     = score_15m
                        breakdown = breakdown_15m
                        atr       = atr_15m
                        chosen_tf = '15m'
                        chosen_df = df_15m
                        price     = df_15m['close'].iloc[-1]
                        tf_reason = f'Scalp Entry ב-15m (4H={score_4h}, 1H={score_1h} < {eff_min})'

            # ── Priority Score Logging — compare with open trades when slots full ─
            if at_capacity:
                if score >= eff_min and active_trades:
                    _lowest = min(active_trades, key=lambda t: t.get('score', 0))
                    _delta  = score - _lowest.get('score', 0)
                    if _delta > 0:
                        print(
                            f"[Priority] 📊 {symbol} {direction} score={score} "
                            f"> open {_lowest['symbol']} score={_lowest.get('score', 0)} "
                            f"(Δ+{_delta}) — {MAX_TRADES}/{MAX_TRADES} slots full, would replace"
                        )
                    else:
                        print(
                            f"[Priority] ➡️  {symbol} {direction} score={score} ≤ lowest open "
                            f"{_lowest['symbol']} score={_lowest.get('score', 0)} — no swap needed"
                        )
                continue  # never open new trades when at capacity

            if score >= eff_min:
                # חישוב RSI ו-EMA200 רגע לפני פתיחה לשמירה בדוח
                try:
                    _last_rsi   = ta.rsi(chosen_df['close'], length=14).iloc[-1]
                    _last_ema   = ta.ema(chosen_df['close'], length=200).iloc[-1]
                except Exception:
                    _last_rsi = _last_ema = None

                # ── Fear Filter RSI: REMOVED — RSI_VETO_LONG (85) only ──

                # ── IG-2: Multi-TF RSI + Volume context for Claude ──────────────
                try:
                    _rsi_4h  = ta.rsi(df_4h['close'],  length=14).iloc[-1]
                    _rsi_1h  = ta.rsi(df_1h['close'],  length=14).iloc[-1]
                    _rsi_15m = ta.rsi(df_15m['close'], length=14).iloc[-1] if df_15m is not None else None
                    _vol_avg = chosen_df['volume'].iloc[-12:-2].mean()
                    _vol_ratio = (chosen_df['volume'].iloc[-2] / _vol_avg
                                  if _vol_avg > 0 else None)
                except Exception:
                    _rsi_4h = _rsi_1h = _rsi_15m = _vol_ratio = None

                _change_24h = candidate.get('change', None)

                # ── Claude Risk Advisor (ADVISOR MODE — אינו חוסם) ──────────────
                # Claude = הערה בלבד. ציון ≥ MIN_SCORE + BTC Regime = כניסה.
                _, claude_note = claude_filter(
                    symbol=symbol, direction=direction, score=score,
                    breakdown=breakdown, price=price, timeframe=chosen_tf,
                    btc_regime=btc_regime, fng_v=fng_v_scan, fng_lbl=fng_lbl_scan,
                    rsi_4h=_rsi_4h, rsi_1h=_rsi_1h, rsi_15m=_rsi_15m,
                    volume_ratio=_vol_ratio, change_24h=_change_24h,
                    daily_pnl=daily_stats.get('total_pnl', 0.0),
                )
                # תמיד ממשיך — Claude GO/NO-GO מבוטל
                print(f"  [Advisor] ✅ Proceeding: {symbol} {direction} — {claude_note}")

                # ── IG-3: Sector Concentration Guard ────────────────────────────
                sect_blocked, sect_reason = check_sector_concentration(symbol, direction)
                if sect_blocked:
                    print(f"  [Sector] 🚫 {symbol} — {sect_reason}")
                    send_msg(
                        f"🏛 *Sector Concentration Block*\n\n"
                        f"{'🟢' if direction == 'LONG' else '🔴'} `{symbol}` {direction}\n"
                        f"📊 ציון: *{score}/100* ✅ | Claude ✅\n"
                        f"🚫 _{sect_reason}_"
                    )
                    rejected_out.append({
                        'symbol': symbol, 'direction': direction, 'best_score': score,
                        'reason': sect_reason[:80],
                        'scores': {'4H': score_4h, '1H': score_1h, '15m': score_15m},
                    })
                    continue

                # ── OB zone from df_1h — same df used in score_symbol OB check ──
                _ob_f, _ob_h, _ob_l = False, None, None
                try:
                    _ob_f, _ob_h, _ob_l, _ = detect_order_blocks(df_1h, direction, lookback=50)
                    if not (_ob_f and _ob_h and _ob_h > 0 and _ob_l > 0):
                        _ob_f, _ob_h, _ob_l = False, None, None
                except Exception:
                    pass

                # ── פתיחת עסקה ──────────────────────────────────────────────────
                opened = open_demo_trade(
                    symbol, price, breakdown,
                    chosen_df, direction=direction,
                    score=score, atr=atr,
                    timeframe=chosen_tf, tf_reason=tf_reason,
                    rsi=_last_rsi, ema200=_last_ema,
                    fng_v=fng_v_scan,
                    ob_found=_ob_f, ob_high=_ob_h, ob_low=_ob_l,
                    df_1h=df_1h,
                )
                if opened:
                    found += 1
                else:
                    rejected_out.append({
                        'symbol': symbol, 'direction': direction, 'best_score': score,
                        'reason': 'הכניסה נדחתה בשער סיכון סופי או בבדיקת יתרה',
                        'scores': {'4H': score_4h, '1H': score_1h, '15m': score_15m},
                    })
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
    now_hour = now_il().hour
    if now_hour in AUDIT_HOURS and now_hour != _last_audit_hour:
        _last_audit_hour = now_hour
        try:
            from gdrive_reporter import run_audit_upload
            run_audit_upload(
                active_trades, wallet, trade_audit_log,
                send_telegram=send_msg
            )
            print(f"Daily audit report generated at {now_hour:02d}:00")
        except ImportError:
            print("gdrive_reporter not available — skipping audit")
        except Exception as e:
            print(f"Audit error: {e}")


def _run_watch_check(symbol: str, entry: dict, silent: bool = False) -> int:
    """
    מריץ ניתוח טכני על מטבע ב-Watch List ושולח עדכון לטלגרם.
    מחזיר את הציון שנמצא (0 אם שגיאה).
    silent=True → לא שולח הודעה אם אין שינוי משמעותי (רק אם ציון עלה/ירד ≥5)
    """
    direction    = entry.get('direction', 'LONG')
    last_score   = entry.get('last_score', 0)
    try:
        df_4h = get_data(symbol, timeframe='4h', limit=250)
        df_1h = get_data(symbol, timeframe='1h', limit=250)
        price = float(df_4h['close'].iloc[-1])

        score, breakdown, atr = score_symbol(df_4h, df_1h, symbol, direction)

        # ── מדדים לדוח ──────────────────────────────────────────────────────
        rsi_4h_s = ta.rsi(df_4h['close'], length=14)
        rsi_1h_s = ta.rsi(df_1h['close'], length=14)
        rsi_4h   = float(rsi_4h_s.iloc[-1]) if rsi_4h_s is not None else None
        rsi_1h   = float(rsi_1h_s.iloc[-1]) if rsi_1h_s is not None else None

        ema200_s = ta.ema(df_4h['close'], length=200)
        ema200   = float(ema200_s.iloc[-1]) if ema200_s is not None else None
        ema_dist = round(abs(price - ema200) / ema200 * 100, 1) if ema200 else None
        ema_dir  = "↑" if price > ema200 else "↓"

        vol_avg   = df_4h['volume'].iloc[-12:-2].mean()
        vol_ratio = round(df_4h['volume'].iloc[-2] / vol_avg, 2) if vol_avg > 0 else None

        # ── שינוי ציון ──────────────────────────────────────────────────────
        score_delta = score - last_score
        entry['last_score'] = score

        # ── רמת עניין ───────────────────────────────────────────────────────
        if score >= MIN_SCORE:
            level = "🚨 *מוכן לעסקה\\!*"
        elif score >= 85:
            level = "⚠️ *חם — קרוב לסף*"
        elif score >= 70:
            level = "🔥 מתחמם"
        else:
            level = "❄️ קר"

        # silent mode — שולח רק אם שינוי ≥5 נקודות
        if silent and abs(score_delta) < 5:
            return score

        now_str   = now_il().strftime('%H:%M')
        dir_emoji = "🟢" if direction == 'LONG' else "🔴"
        sym_clean = symbol.replace('/', '\\/')
        delta_str = f"\\({score_delta:+d} מהבדיקה הקודמת\\)" if last_score > 0 else "\\(בדיקה ראשונה\\)"
        rsi_str   = f"RSI 4H: `{rsi_4h:.1f}`" + (f" | 1H: `{rsi_1h:.1f}`" if rsi_1h else "")
        ema_str   = f"`{ema_dist:.1f}%` {ema_dir}EMA200" if ema_dist is not None else "N/A"
        vol_str   = f"`{vol_ratio:.2f}×`" if vol_ratio else "N/A"
        ema_ok    = "✅" if ema_dist is not None and ema_dist <= 5.0 else "⚠️"
        vol_ok    = "✅" if vol_ratio and vol_ratio >= 2.5 else "—"

        score_bar = "█" * (score // 10) + "░" * (10 - score // 10)

        msg = (
            f"🔭 *Watch Update — {sym_clean} {direction}*\n"
            f"⏰ {now_str}\n"
            f"{'─' * 26}\n"
            f"{dir_emoji} ציון: *{score}/100* {level}\n"
            f"`{score_bar}` {delta_str}\n\n"
            f"📉 {rsi_str}\n"
            f"📐 EMA200: {ema_str} {ema_ok}\n"
            f"📦 Volume: {vol_str} {vol_ok}\n"
            f"💰 מחיר: `{price:.6g}`\n"
        )

        if score >= MIN_SCORE:
            fng_v, _, _ = sentiment_check("watch")
            btc_r = get_btc_regime()
            msg += f"\n✅ _הבוט יפתח עסקה בסריקה הבאה אם הציון יחזיק_"
        elif score >= 85:
            msg += f"\n_עוד *{MIN_SCORE - score}* נקודות לעסקה_"

        send_msg(msg)
        return score

    except Exception as e:
        print(f"[Watch] Error checking {symbol}: {e}")
        if not silent:
            send_msg(f"⚠️ *Watch שגיאה* — `{symbol}`: {str(e)[:60]}")
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
# SCALP MODE — High-Volatility Mean Reversion (FNG < EXTREME_FEAR_THRESHOLD)
# ═══════════════════════════════════════════════════════════════════════════════

def _check_scalp_short(symbol: str, price: float):
    """
    בדיקת תנאי Scalp-Short:
    - RSI 15m > 82 (overbought extreme)
    - מחיר < EMA9 על גרף 5m (מתחיל לרדת)
    מחזיר: (ok, rsi15, ema9_5m)
    """
    try:
        candles_15m = exchange.fetch_ohlcv(symbol, '15m', limit=30)
        if len(candles_15m) < 20:
            return False, 0.0, 0.0
        df15  = pd.DataFrame(candles_15m, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        rsi_s = ta.rsi(df15['close'], length=14)
        if rsi_s is None or rsi_s.isna().all():
            return False, 0.0, 0.0
        rsi15 = float(rsi_s.iloc[-1])
        if rsi15 <= SCALP_SHORT_RSI_THRESH:
            return False, round(rsi15, 2), 0.0

        candles_5m = exchange.fetch_ohlcv(symbol, '5m', limit=20)
        if len(candles_5m) < 15:
            return False, round(rsi15, 2), 0.0
        df5  = pd.DataFrame(candles_5m, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        ema9 = float(df5['close'].ewm(span=9, adjust=False).mean().iloc[-1])
        if price >= ema9:
            return False, round(rsi15, 2), round(ema9, 8)

        return True, round(rsi15, 2), round(ema9, 8)
    except Exception as e:
        print(f"_check_scalp_short {symbol}: {e}")
        return False, 0.0, 0.0


def _check_quick_long(symbol: str, price: float):
    """
    בדיקת תנאי Quick-Long (Dip Buy):
    - ירידה > 20% ב-2 שעות (8 קנדלים × 15m)
    - RSI 15m < 18 (oversold extreme)
    - מחיר קפץ 1%+ מהשפל של 2h האחרונות
    מחזיר: (ok, rsi15, drop_pct, bounce_pct)
    """
    try:
        candles_15m = exchange.fetch_ohlcv(symbol, '15m', limit=30)
        if len(candles_15m) < 12:
            return False, 0.0, 0.0, 0.0
        df15  = pd.DataFrame(candles_15m, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        rsi_s = ta.rsi(df15['close'], length=14)
        if rsi_s is None or rsi_s.isna().all():
            return False, 0.0, 0.0, 0.0
        rsi15 = float(rsi_s.iloc[-1])
        if rsi15 >= SCALP_LONG_RSI_THRESH:
            return False, round(rsi15, 2), 0.0, 0.0

        # מחיר ~2 שעות קודם (סגירה 8 קנדלים אחורה)
        price_2h_ago = df15['close'].iloc[-9] if len(df15) >= 9 else df15['close'].iloc[0]
        if price_2h_ago <= 0:
            return False, round(rsi15, 2), 0.0, 0.0
        drop_pct = (price_2h_ago - price) / price_2h_ago * 100
        if drop_pct < SCALP_CRASH_MIN_PCT:
            return False, round(rsi15, 2), round(drop_pct, 2), 0.0

        # שפל 2h ובדיקת קפיצה
        low_2h = df15['low'].iloc[-9:].min()
        if low_2h <= 0:
            return False, round(rsi15, 2), round(drop_pct, 2), 0.0
        bounce_pct = (price - low_2h) / low_2h * 100
        if bounce_pct < SCALP_BOUNCE_PCT:
            return False, round(rsi15, 2), round(drop_pct, 2), round(bounce_pct, 2)

        return True, round(rsi15, 2), round(drop_pct, 2), round(bounce_pct, 2)
    except Exception as e:
        print(f"_check_quick_long {symbol}: {e}")
        return False, 0.0, 0.0, 0.0


def open_scalp_trade(symbol: str, direction: str, price: float, reason: str):
    """
    פותח עסקת Scalp — ⚡ מסלול Scalp.
    SL=2% | TP1=4% (RR 1:2) | TP=8% (RR 1:4) | BE=50% of way to TP1
    גודל פוזיציה לפי 1.5% סיכון מהון | Market Order | תוקף 60 דקות.
    """
    global active_trades

    track = 'Scalp'

    # ── Daily Circuit Breaker ────────────────────────────────────────────────
    if check_daily_circuit_breaker():
        print(f"[SCALP] ⛔ Circuit Breaker — לא פותחים {symbol} (הפסד יומי ≤ ${DAILY_LOSS_LIMIT})")
        return

    # ── RegimeClose Cooldown — מניעת פינג-פונג ───────────────────────────────
    _rc_ts_sc = regime_close_cooldown.get(symbol, 0)
    if time.time() - _rc_ts_sc < REGIME_CLOSE_COOLDOWN_SEC:
        _rc_min_sc = int((REGIME_CLOSE_COOLDOWN_SEC - (time.time() - _rc_ts_sc)) / 60)
        print(f"[RegimeCooldown] {symbol} בקולדאון {_rc_min_sc}min — skip Scalp", flush=True)
        return

    # ── Trade Close Cooldown — 2h אחרי FastLoss/SL/ReversalGuard ────────────
    _tcc_ts_sc = trade_close_cooldown.get(symbol, 0)
    if time.time() - _tcc_ts_sc < TRADE_CLOSE_COOLDOWN_SEC:
        _tcc_min_sc = int((TRADE_CLOSE_COOLDOWN_SEC - (time.time() - _tcc_ts_sc)) / 60)
        print(f"[TradeCooldown] {symbol} בקולדאון {_tcc_min_sc}min — skip Scalp", flush=True)
        return
    # ── MaxDuration Cooldown — 6h (SOL loop prevention) ──────────────────────
    _mdc_ts_sc = max_duration_cooldown.get(symbol, 0)
    if time.time() - _mdc_ts_sc < MAX_DURATION_COOLDOWN_SEC:
        _mdc_min_sc = int((MAX_DURATION_COOLDOWN_SEC - (time.time() - _mdc_ts_sc)) / 60)
        print(f"[MaxDurCooldown] {symbol} {_mdc_min_sc}min נותרו (6h) — skip Scalp", flush=True)
        return

    with trades_lock:
        if any(t['symbol'] == symbol for t in active_trades):
            print(f"SCALP: {symbol} already in active_trades — skip")
            return
        scalp_count = sum(1 for t in active_trades if t.get('scalp'))
    if scalp_count >= MAX_SCALP_TRADES:
        print(f"SCALP: max scalp trades ({MAX_SCALP_TRADES}) reached — skip {symbol}")
        return

    # ── Market Regime Gate ────────────────────────────────────────────────────
    _allowed_s, _reason_s = is_direction_allowed(direction, context='Scalp')
    if not _allowed_s:
        print(f"[Scalp] {symbol} {direction} נדחה — {_reason_s}", flush=True)
        return

    # ── נפח + שינוי 24h (קריאה אחת) ─────────────────────────────────────────
    vol_usd, change_24h = fetch_symbol_ticker_info(symbol)
    if vol_usd > 0 and vol_usd < SCALP_TRACK_VOL_MIN:
        print(f"[SCALP] נפח נמוך עבור {symbol}: ${vol_usd/1e6:.1f}M < $50M — מדלג")
        send_msg(
            f"⚠️ *נפח נמוך — {symbol.replace('/USDT','')}*\n"
            f"נפח 24h: ${vol_usd/1e6:.1f}M | מינימום Scalp: ${SCALP_TRACK_VOL_MIN/1e6:.0f}M\n"
            f"_עסקת Scalp נדחתה — נזילות לא מספקת_"
        )
        return

    # ── Fixed Sizing ──────────────────────────────────────────────────────────
    fng_v_now, _, _ = sentiment_check("scalp_open")
    eff_margin = MARGIN        # $50
    pos_size   = POSITION_SIZE  # $500 (MARGIN × LEVERAGE)
    leverage   = LEVERAGE       # 10x
    sl_pct     = SL_PCT         # 2%
    tp1_pct    = TP1_PCT        # 2%
    tp_pct     = TP2_PCT        # 4%

    if wallet.get('balance', 0) < eff_margin:
        print(f"SCALP: insufficient balance (${wallet.get('balance', 0):.2f}) — skip {symbol}")
        return

    # ── Claude AI Gate ────────────────────────────────────────────────────────
    _cl_regime_sc, _, _cl_btc_sc, _ = get_market_regime()
    _cl_ok_sc, _cl_score_sc, _cl_reason_sc, _cl_risk_sc = claude_gate.claude_trade_gate(
        symbol=symbol, direction=direction, strategy='Scalp',
        price=price, bot_score=0,
        regime=_cl_regime_sc, fng=int(fng_v_now or 50), btc_above_ema=_cl_btc_sc,
        rsi=None, reason=str(reason or ''),
        daily_pnl=daily_stats.get('total_pnl', 0.0),
        open_trades=len(active_trades),
    )
    if not _cl_ok_sc or _cl_score_sc < 62:
        print(f"[ClaudeGate] ⛔ {symbol} Scalp נדחה — claude={_cl_score_sc} | {_cl_reason_sc}", flush=True)
        return

    # ── מחירי SL / TP1 / TP ──────────────────────────────────────────────────
    tgt = se.calc_targets(price, direction)
    sl_price  = tgt['sl_price']
    tp1_price = tgt['tp1_price']
    tp_price  = tgt['tp_price']
    be_price  = tp1_price   # BE triggers at TP1

    max_risk_usd = round(pos_size * sl_pct / 100, 2)

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
        'phase':           'scalp',
        'be_triggered':    False,
        'tp1_triggered':   False,
        'tp1_pnl':         0.0,
        'peak_price':      price,
        'trailing_sl':     None,
        'score':           0,
        'claude_score':    _cl_score_sc,
        'claude_reason':   _cl_reason_sc,
        'claude_key_risk': _cl_risk_sc,
        'atr':             0.0,
        'timeframe':       '15m',
        'rsi':             None,
        'ema200':          None,
        'score_breakdown': reason,
        'opened_at':       now_il().isoformat(timespec='seconds'),
        'pos_size':        pos_size,
        'margin':          eff_margin,
        'leverage':        leverage,
        'fng_at_entry':    fng_v_now,
        'scalp':           True,
        'scalp_opened_ts': time.time(),
        'track':           track,
        'vol_usd':         round(vol_usd),
        'change_24h':      round(change_24h, 2),
        'slippage_pct':    0.0,
    }
    place_order(trade, eff_margin)

    emoji     = "🟢" if direction == 'LONG' else "🔴"
    dir_label = "Quick-Long (Dip Buy)" if direction == 'LONG' else "Scalp-Short (Bubble)"
    scalp_msg = (
        f"⚡ *{dir_label}: {symbol.replace('/USDT', '')} {emoji}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 כניסה: `{price:.6g}`\n"
        f"🛑 SL:    `{sl_price:.6g}` (-{sl_pct:.1f}%)\n"
        f"🔒 BE:    `{be_price:.6g}` (≡ TP1)\n"
        f"🎯 TP1:   `{tp1_price:.6g}` (+{tp1_pct:.1f}%)\n"
        f"🎯 TP:    `{tp_price:.6g}` (+{tp_pct:.1f}%)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💼 {leverage}x · ${eff_margin:.0f} מרג'ין · ${pos_size:.0f} נשלט · ⏱ {SCALP_MAX_DURATION_MIN}m"
    )
    try:
        df_scalp = get_data(symbol, timeframe='1h', limit=80)
        chart_buf = generate_chart(df_scalp, symbol, price, sl_price, tp1_price, direction) \
                    if df_scalp is not None else None
    except Exception:
        chart_buf = None
    send_chart_alert(chart_buf, symbol, scalp_msg)
    print(f"[SCALP] {direction}: {symbol} @ {price:.6g} | SL={sl_pct:.1f}% TP1={tp1_pct:.1f}% TP2={tp_pct:.1f}% | {leverage}x margin=${eff_margin:.0f} risk=${max_risk_usd}")


def _cliff_detect(symbol: str) -> tuple[bool, str, float, float, bool]:
    """
    מזהה High-Velocity Event על גרף 5m (LONG "Rocket" או SHORT "Cliff"):
      - תנועה >CLIFF_DROP_PCT% בנר אחד (עלייה או ירידה)
      - Volume >CLIFF_VOL_MULT× ממוצע 10 נרות אחרונים (400%)
      - RSI Divergence: RSI(14) ב-15m היה >70 ועכשיו יורד → HIGH-CONVICTION SHORT

    מחזיר (is_velocity, direction, move_pct, vol_ratio, rsi_divergence).
    """
    try:
        # ── 5m candles ──────────────────────────────────────────────────────
        df5 = get_data(symbol, timeframe='5m', limit=20)
        if df5 is None or len(df5) < 12:
            return False, '', 0.0, 0.0, False

        # נר אחרון סגור (index -2; -1 הוא נר פתוח)
        last         = df5.iloc[-2]
        candle_open  = last['open']
        candle_close = last['close']
        candle_vol   = last['volume']

        candle_chg = (candle_close - candle_open) / candle_open * 100  # + = עלייה

        # Volume ממוצע של 10 נרות (ללא הנר הנוכחי)
        avg_vol = df5['volume'].iloc[-12:-2].mean()
        if avg_vol <= 0:
            return False, '', 0.0, 0.0, False
        vol_ratio = candle_vol / avg_vol

        # כיוון + בדיקת סף
        vol_ok    = vol_ratio >= CLIFF_VOL_MULT
        is_rocket = candle_chg >=  CLIFF_DROP_PCT and vol_ok   # LONG
        is_cliff  = candle_chg <= -CLIFF_DROP_PCT and vol_ok   # SHORT

        if not (is_rocket or is_cliff):
            return False, '', 0.0, 0.0, False

        direction = 'LONG' if is_rocket else 'SHORT'
        move_pct  = abs(candle_chg)

        # ── RSI Divergence (15m) — רלוונטי רק ל-SHORT ───────────────────
        rsi_divergence = False
        if direction == 'SHORT':
            try:
                df15 = get_data(symbol, timeframe='15m', limit=20)
                if df15 is not None and len(df15) >= 15:
                    rsi_series = df15['close'].rolling(14).apply(
                        lambda x: _rsi_from_series(x), raw=False
                    )
                    rsi_prev = rsi_series.iloc[-3]
                    rsi_now  = rsi_series.iloc[-2]
                    if (rsi_prev is not None and rsi_now is not None and
                            rsi_prev > CLIFF_RSI_OVERBOUGHT and rsi_now < rsi_prev):
                        rsi_divergence = True
            except Exception:
                pass

        return True, direction, move_pct, vol_ratio, rsi_divergence

    except Exception as e:
        print(f"[Velocity Detect] {symbol} error: {e}")
        return False, '', 0.0, 0.0, False


def _rsi_from_series(series):
    """עוזר: מחשב RSI מ-Series של 14 ערכים."""
    try:
        delta  = series.diff()
        gain   = delta.clip(lower=0).mean()
        loss   = -delta.clip(upper=0).mean()
        if loss == 0:
            return 100.0
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    except Exception:
        return None


def open_cliff_trade(symbol: str, price: float, direction: str, move_pct: float,
                     vol_ratio: float, rsi_divergence: bool):
    """
    פותח עסקת High-Velocity (LONG "Rocket" / SHORT "Cliff"):
      SL=1.5% ראשוני · Trailing 1% מהשיא · Break-Even ב-1.5% · TP 3% · תוקף 30 דקות
    ישיר — ללא BTC filter או 4H/1H אישור.
    """
    global active_trades

    # ── Strategy kill-switch (config.py: ENABLE_VELOCITY_STRATEGY) ───────────
    if not ENABLE_VELOCITY_STRATEGY:
        print(f"[Velocity] ⛔ ENABLE_VELOCITY_STRATEGY=False — {symbol} {direction} נדחה", flush=True)
        return

    # ── Circuit Breaker ────────────────────────────────────────────────────────
    if check_daily_circuit_breaker():
        print(f"[Velocity] ⛔ Circuit Breaker — לא פותחים {symbol} (הפסד יומי ≤ ${DAILY_LOSS_LIMIT})", flush=True)
        return

    with trades_lock:
        if any(t['symbol'] == symbol for t in active_trades):
            print(f"[Velocity] {symbol} כבר פתוח — skip")
            return
        cliff_count = sum(1 for t in active_trades if t.get('cliff'))

    if cliff_count >= MAX_CLIFF_TRADES:
        print(f"[Velocity] Max velocity trades ({MAX_CLIFF_TRADES}) — skip {symbol}")
        return
    if len(active_trades) >= MAX_TRADES:
        print(f"[Velocity] Max total trades ({MAX_TRADES}) — skip {symbol}")
        return
    if wallet.get('balance', 0) < CLIFF_MARGIN:
        print(f"[Velocity] יתרה נמוכה — skip {symbol}")
        return

    # ── Market Regime Gate ────────────────────────────────────────────────────
    _allowed_c, _reason_c = is_direction_allowed(direction, context='Velocity')
    if not _allowed_c:
        print(f"[Velocity] {symbol} {direction} נדחה — {_reason_c}", flush=True)
        return

    # ── Claude AI Gate ────────────────────────────────────────────────────────
    _cl_regime_cl, _cl_fng_cl, _cl_btc_cl, _ = get_market_regime()
    _cl_ok_cl, _cl_score_cl, _cl_reason_cl, _cl_risk_cl = claude_gate.claude_trade_gate(
        symbol=symbol, direction=direction, strategy='Velocity',
        price=price, bot_score=0,
        regime=_cl_regime_cl, fng=int(_cl_fng_cl or 50), btc_above_ema=_cl_btc_cl,
        rsi=None, reason=f"move={move_pct:.1f}% vol={vol_ratio:.1f}x",
        extra={'RSI Divergence': str(rsi_divergence)},
        daily_pnl=daily_stats.get('total_pnl', 0.0),
        open_trades=len(active_trades),
    )
    if not _cl_ok_cl or _cl_score_cl < 60:
        print(f"[ClaudeGate] ⛔ {symbol} Velocity נדחה — claude={_cl_score_cl} | {_cl_reason_cl}", flush=True)
        return

    if direction == 'LONG':
        sl_price   = round(price * (1 - CLIFF_SL_PCT         / 100), 8)
        tp_price   = round(price * (1 + CLIFF_TP_PCT         / 100), 8)
        be_level   = round(price * (1 + CLIFF_BE_TRIGGER_PCT / 100), 8)
        trail_init = round(price * (1 - CLIFF_TRAIL_PCT      / 100), 8)  # 1% מתחת לכניסה
    else:  # SHORT
        sl_price   = round(price * (1 + CLIFF_SL_PCT         / 100), 8)
        tp_price   = round(price * (1 - CLIFF_TP_PCT         / 100), 8)
        be_level   = round(price * (1 - CLIFF_BE_TRIGGER_PCT / 100), 8)
        trail_init = round(price * (1 + CLIFF_TRAIL_PCT      / 100), 8)  # 1% מעל לכניסה

    label     = "Rocket 🚀" if direction == 'LONG' else "Cliff 🪂"
    emoji     = "🟢" if direction == 'LONG' else "🔴"
    move_word = "עלה" if direction == 'LONG' else "ירד"
    div_note  = "\n⚡ *RSI Divergence — High-Conviction!*" if rsi_divergence else ""
    conviction_txt = (
        f"Velocity {move_pct:.1f}% {move_word} | Vol {vol_ratio:.1f}× | "
        + ("RSI-Divergence" if rsi_divergence else "High-Velocity")
    )

    trade = {
        'symbol':          symbol,
        'entry':           price,
        'sl':              trail_init,    # Trailing SL starts tight (1% from entry)
        'tp':              tp_price,
        'tp1':             tp_price,
        'be_lvl':          be_level,
        'sl_pct':          CLIFF_SL_PCT,
        'tp_pct':          CLIFF_TP_PCT,
        'direction':       direction,
        'phase':           'cliff',
        'be_triggered':    False,
        'tp1_triggered':   False,
        'tp1_pnl':         0.0,
        'peak_price':      price,
        'trailing_sl':     trail_init,
        'score':           0,
        'claude_score':    _cl_score_cl,
        'claude_reason':   _cl_reason_cl,
        'claude_key_risk': _cl_risk_cl,
        'atr':             0.0,
        'timeframe':       '5m',
        'rsi':             None,
        'ema200':          None,
        'score_breakdown': conviction_txt,
        'opened_at':       now_il().isoformat(timespec='seconds'),
        'pos_size':        CLIFF_POS_SIZE,
        'margin':          CLIFF_MARGIN,
        'fng_at_entry':    None,
        'scalp':           False,
        'cliff':           True,
        'cliff_opened_ts': time.time(),
        'track':           'Scalp',      # ⚡ High-Velocity = מסלול Scalp
        'slippage_pct':    0.0,
    }
    place_order(trade, CLIFF_MARGIN)

    send_msg(
        f"⚡ *High-Velocity {label}: {symbol.replace('/USDT', '')} {emoji}*{div_note}\n"
        f"_נר 5m {move_word} {move_pct:.1f}% עם Volume {vol_ratio:.1f}× ממוצע_\n\n"
        f"💵 כניסה: `{price:.6g}`\n"
        f"🏃 Trailing SL: `{trail_init:.6g}` ({CLIFF_TRAIL_PCT}% מיידי)\n"
        f"📍 Break-Even ב: `{be_level:.6g}` ({CLIFF_BE_TRIGGER_PCT}% רווח)\n"
        f"🎯 TP: `{tp_price:.6g}` ({CLIFF_TP_PCT}%)\n"
        f"⏱ תוקף: {CLIFF_MAX_DURATION_MIN} דקות · ללא פילטרי 4H/BTC\n"
        f"💼 {CLIFF_LEVERAGE}x · ${CLIFF_MARGIN:.0f} מרג'ין · ${CLIFF_POS_SIZE:.0f} נשלט\n\n"
        + _wallet_opened_summary()
    )
    print(f"[Velocity] {direction} {label} {symbol} @ {price:.6g}"
          f" | TrailSL={trail_init:.6g} TP={tp_price:.6g}"
          f" | Move={move_pct:.1f}% Vol={vol_ratio:.1f}×")


def scalp_scan_loop():
    """
    Thread 6 — High-Volatility Scalp Scanner.
    פעיל רק כשFNG < EXTREME_FEAR_THRESHOLD (כרגע 13).
    רץ כל 5 דקות ומחפש:
      • Scalp-Short: מטבע עלה >30% ב-24h + RSI15m>82 + מחיר מתחת EMA9(5m)
      • Quick-Long:  מטבע ירד >20% ב-2h + RSI15m<18 + קפיצה 1% מהשפל
    Mean Reversion — לא trend-following.
    """
    print("Thread 6 (Scalp Scanner) started.")
    time.sleep(90)   # המתן שה-bot יתייצב לפני הסריקה הראשונה

    while True:
        if _bot_paused:
            time.sleep(60)
            continue
        try:
            fng_v, fng_lbl, _ = sentiment_check("scalp_scan")
            if fng_v is None:
                time.sleep(SCALP_SCAN_INTERVAL_WAIT)
                continue

            # Scalp scanner is ONLY active during Extreme Fear (FNG < threshold)
            # In normal market (WAIT mode), sleep longer to save CPU
            if not ENABLE_SCALP_MODE:
                time.sleep(SCALP_SCAN_INTERVAL_WAIT)
                continue
            if fng_v >= EXTREME_FEAR_THRESHOLD:
                time.sleep(SCALP_SCAN_INTERVAL_WAIT)   # 10 min in WAIT mode
                continue

            with trades_lock:
                scalp_count = sum(1 for t in active_trades if t.get('scalp'))
            if scalp_count >= MAX_SCALP_TRADES:
                time.sleep(SCALP_SCAN_INTERVAL_WAIT)   # 10 min when at capacity
                continue

            # ── BTC Compass Filter ─────────────────────────────────────────
            btc_above_ema = _btc_above_ema20_15m()
            btc_compass   = "✅ BTC מעל EMA20" if btc_above_ema else "⛔ BTC מתחת EMA20"
            print(f"[Scalp] FNG={fng_v} | {btc_compass} — scanning...")
            tickers = exchange.fetch_tickers()

            # ── SCALP-SHORT: Bubble Watch — עלה >30% ב-24h ───────────────────────
            # ₿ BTC Compass: שורט רק כשBTC יורד/נייטרל — לא כשBTC מטפס
            if btc_above_ema:
                print("[Scalp] ₿ SHORT skipped — BTC bullish compass (no counter-trend shorts)")
            else:
                bubble_candidates = sorted(
                    [
                        {'symbol': s, 'change_pct': t.get('percentage', 0), 'price': t.get('last', 0)}
                        for s, t in tickers.items()
                        if s.endswith('/USDT')
                        and t.get('percentage', 0) >= SCALP_BUBBLE_MIN_PCT
                        and t.get('last', 0) > 0
                        and (t.get('quoteVolume') or 0) >= 1_000_000
                    ],
                    key=lambda x: x['change_pct'], reverse=True
                )[:5]

                if VERBOSE_LOG and bubble_candidates:
                    print(f"[Scalp] SHORT candidates: {[c['symbol'] for c in bubble_candidates]}")

                for cand in bubble_candidates:
                    with trades_lock:
                        if any(t['symbol'] == cand['symbol'] for t in active_trades):
                            continue
                        if sum(1 for t in active_trades if t.get('scalp')) >= MAX_SCALP_TRADES:
                            break

                    sym   = cand['symbol']
                    price = cand['price']
                    ok, rsi15, ema9 = _check_scalp_short(sym, price)
                    if ok:
                        reason = (f"Bubble +{cand['change_pct']:.1f}% 24h · "
                                  f"RSI 15m={rsi15:.1f} >82 · "
                                  f"Price {price:.6g} < EMA9(5m) {ema9:.6g}")
                        open_scalp_trade(sym, 'SHORT', price, reason)
                        time.sleep(2)

            # ── QUICK-LONG: Flash Crash — ירד >20% ב-2h ──────────────────────────
            # ₿ BTC Compass: LONG רק כשBTC עולה — לא ל-catch falling knives בBTC יורד
            if not btc_above_ema:
                print("[Scalp] ₿ LONG skipped — BTC bearish compass (no longs vs trend)")
            else:
                for sym, ticker in tickers.items():
                    if not sym.endswith('/USDT'):
                        continue
                    with trades_lock:
                        if sum(1 for t in active_trades if t.get('scalp')) >= MAX_SCALP_TRADES:
                            break
                        if any(t['symbol'] == sym for t in active_trades):
                            continue

                    price = ticker.get('last') or 0
                    if not price or price <= 0 or (ticker.get('quoteVolume') or 0) < 1_000_000:
                        continue

                    ok, rsi15, drop_pct, bounce_pct = _check_quick_long(sym, price)
                    if ok:
                        reason = (f"Flash Crash -{drop_pct:.1f}% (2h) · "
                                  f"RSI 15m={rsi15:.1f} <18 · "
                                  f"Bounce +{bounce_pct:.2f}% from low")
                        open_scalp_trade(sym, 'LONG', price, reason)
                        time.sleep(2)

        except Exception as e:
            print(f"[Scalp] ⚠️ error: {e}")

        time.sleep(SCALP_SCAN_INTERVAL)   # 5 min when actively scanning


def _btc_above_ema20_15m() -> bool:
    """
    BTC Correlation Filter:
    מחזיר True אם BTC/USDT 15m close > EMA20 — מגמה חיובית.
    משמש כפילטר לפני כניסה לכל עסקת SOL.
    Fail-SAFE: שגיאה → False (מניח שוק יורד, לא מאפשר כניסה)
    """
    try:
        df = get_data('BTC/USDT', timeframe='15m', limit=30)
        ema20 = ta.ema(df['close'], length=20)
        if ema20 is None:
            return False   # fail-safe: block entry
        btc_close = float(df['close'].iloc[-1])
        btc_ema   = float(ema20.iloc[-1])
        return btc_close > btc_ema
    except Exception:
        return False   # fail-safe: block entry


# ── BTC Intraday Momentum Cache (3 דקות TTL) ─────────────────────────────────
_btc_intraday_cache: dict = {
    'ts': 0.0, 'bias': 'SIDEWAYS', 'price': 0.0,
    'ema15m': 0.0, 'daily_open': 0.0,
}
_BTC_INTRADAY_TTL = 180   # 3 דקות

def get_btc_intraday_bias() -> tuple[str, float, float]:
    """
    BTC Intraday Momentum — combines EMA20(15m) + Daily Open.

    BULLISH : price > EMA20(15m) AND price > daily_open → BTC עולה intraday
    BEARISH : price < EMA20(15m) AND price < daily_open → BTC יורד intraday
    SIDEWAYS: איתות מעורב

    Returns: (bias, btc_price, btc_ema20_15m)
    Cache: 3 דקות — קריאה אחת לכל כמה סריקות.
    Fail-safe: שגיאה → SIDEWAYS (לא חוסם, לא מרשה בוודאות)
    """
    global _btc_intraday_cache
    now_ts = time.time()
    if now_ts - _btc_intraday_cache['ts'] < _BTC_INTRADAY_TTL:
        c = _btc_intraday_cache
        return c['bias'], c['price'], c['ema15m']

    try:
        df_15m     = get_data('BTC/USDT', timeframe='15m', limit=50)
        df_1d      = get_data('BTC/USDT', timeframe='1d',  limit=2)
        price      = float(df_15m['close'].iloc[-1])
        ema20_15m  = float(ta.ema(df_15m['close'], length=20).iloc[-1])
        daily_open = float(df_1d['open'].iloc[-1])

        above_ema   = price > ema20_15m
        above_dopen = price > daily_open

        if above_ema and above_dopen:
            bias = 'BULLISH'
        elif not above_ema and not above_dopen:
            bias = 'BEARISH'
        else:
            bias = 'SIDEWAYS'

        _btc_intraday_cache = {
            'ts': now_ts, 'bias': bias, 'price': price,
            'ema15m': ema20_15m, 'daily_open': daily_open,
        }
        _icon = '📈' if bias == 'BULLISH' else ('📉' if bias == 'BEARISH' else '↔️')
        print(
            f"[BTC Intraday] {_icon} {bias} | "
            f"price={price:.0f} | EMA20(15m)={ema20_15m:.0f} | "
            f"DailyOpen={daily_open:.0f}",
            flush=True
        )
        return bias, price, ema20_15m
    except Exception as _e:
        print(f"[BTC Intraday] fetch failed: {_e} — SIDEWAYS (safe)", flush=True)
        return 'SIDEWAYS', 0.0, 0.0


def _sol_1h_breakout_above_4h_high() -> tuple[bool, float, float]:
    """
    Breakout Confirmation:
    מחזיר (breakout:bool, sol_1h_close:float, recent_1h_high:float).
    breakout=True אם 1H close > highest high של 10 נרות 1H אחרונים שלמים.
    (שונה מ-5×4H ל-10×1H — תגובה מהירה יותר לשינויי טרנד)
    """
    try:
        df_4h = get_data('SOL/USDT', timeframe='4h', limit=20)
        df_1h = get_data('SOL/USDT', timeframe='1h', limit=15)
        # 10 נרות 1H שלמים (לא כולל הנר הנוכחי שעוד מתגבש)
        recent_1h_high = float(df_1h['high'].iloc[-11:-1].max())
        sol_1h_close   = float(df_1h['close'].iloc[-1])
        breakout       = sol_1h_close > recent_1h_high
        return breakout, sol_1h_close, recent_1h_high
    except Exception:
        return False, 0.0, 0.0


def _coin_1h_breakout_above_4h_high(symbol: str) -> tuple[bool, float, float, float | None]:
    """
    Generic Breakout Check — כל מטבע.
    מחזיר (breakout, price_1h, high_1h, rsi_4h).
    breakout=True אם 1H close > highest high של 10 נרות 1H שלמים אחרונים.
    (שונה מ-5×4H ל-10×1H — תגובה מהירה יותר לשינויי טרנד)
    """
    try:
        df_4h    = get_data(symbol, timeframe='4h', limit=20)
        df_1h    = get_data(symbol, timeframe='1h', limit=15)
        recent_1h_high = float(df_1h['high'].iloc[-11:-1].max())
        price_1h       = float(df_1h['close'].iloc[-1])
        breakout       = price_1h > recent_1h_high
        rsi_s          = ta.rsi(df_4h['close'], length=14)
        rsi            = round(float(rsi_s.iloc[-1]), 1) if (rsi_s is not None and not rsi_s.isna().all()) else None
        return breakout, price_1h, recent_1h_high, rsi
    except Exception:
        return False, 0.0, 0.0, None


# ── Breakout Strategy Constants ───────────────────────────────────────────────
BREAKOUT_FNG_LONG_MIN         = 0    # FNG מינימום ל-LONG — BTC BULL + כל FNG → קונים!
BREAKOUT_FNG_SHORT_MAX        = 65   # FNG מקסימום ל-SHORT (מעל = חמדנות, לא שורטים)
# RSI_VETO_SHORT = 52  ← REMOVED: היה דורס את ערך config.py (65) גלובלית ופוגע ב-fillslots
#                        Breakout משתמש ב-RSI_VETO_BREAKOUT_SHORT/BEAR_MIN/MAX בלבד
BREAKOUT_MIN_VOL              = 1.5  # volume ratio מינימלי (150% מהממוצע = 50% מעל)
RSI_VETO_BREAKOUT_LONG        = 62   # RSI מקסימום ל-LONG בפריצה — אסור אם RSI > 62 (overbought)
RSI_VETO_BREAKOUT_SHORT       = 60   # RSI מינימום ל-SHORT בפריצה [NEUTRAL/BULL בלבד] — fade pumps
RSI_VETO_BREAKOUT_SHORT_BEAR_MIN = 50  # RSI מינימום ב-BEAR — מתחת = oversold (INJ/XRP audit), לא שורטים
RSI_VETO_BREAKOUT_SHORT_BEAR_MAX = 65  # RSI מקסימום ב-BEAR — מעל = recovery, לא שורטים
# BREAKOUT_FNG_REDUCED_MARGIN — REMOVED: margin is always $50, no dynamic reduction
MAJOR_PRIORITY_SYMBOLS        = {'BTC/USDT', 'ETH/USDT'}  # תמיד ראשונים בתור המועמדים


def _coin_breakout_full(symbol: str, direction: str = 'LONG') -> tuple[bool, float, float, float | None, float]:
    """
    Breakout / Breakdown check + Volume ratio — לשימוש Top10 Auto-Loop.

    LONG:  breakout=True אם 1H close > highest HIGH של 10 נרות 1H שלמים.
    SHORT: breakout=True אם 1H close < lowest  LOW  של 10 נרות 1H שלמים
           + volume חייב להיות מעל הממוצע (anti-fakeout filter).

    שינויים מגרסה קודמת:
      - הוחלף חלון 5×4H ב-10×1H לתגובה מהירה יותר לשינויי כיוון
      - SHORT דורש vol_ratio ≥ BREAKOUT_MIN_VOL (anti-fakeout)

    מחזיר (signal, price_1h, level_1h, rsi_4h, vol_ratio).
    """
    try:
        df_4h     = get_data(symbol, timeframe='4h', limit=25)
        df_1h     = get_data(symbol, timeframe='1h', limit=15)
        price_1h  = float(df_1h['close'].iloc[-1])

        if direction == 'LONG':
            level_1h = float(df_1h['high'].iloc[-11:-1].max())  # 10 complete 1H candles
            signal   = price_1h > level_1h
        else:  # SHORT
            level_1h = float(df_1h['low'].iloc[-11:-1].min())   # 10 complete 1H candles
            signal   = price_1h < level_1h

        rsi_s     = ta.rsi(df_4h['close'], length=14)
        rsi       = round(float(rsi_s.iloc[-1]), 1) if (rsi_s is not None and not rsi_s.isna().all()) else None
        vol_cur   = float(df_1h['volume'].iloc[-1])
        vol_avg   = float(df_1h['volume'].iloc[-11:-1].mean())
        vol_ratio = round(vol_cur / vol_avg, 2) if vol_avg > 0 else 1.0

        # SHORT anti-fakeout: require volume above average to confirm breakdown
        if direction == 'SHORT' and signal and vol_ratio < BREAKOUT_MIN_VOL:
            signal = False

        return signal, price_1h, level_1h, rsi, vol_ratio
    except Exception:
        return False, 0.0, 0.0, None, 1.0


def open_breakout_trade(symbol: str, price: float, margin: float,
                        rsi: float | None, vol_ratio: float,
                        h4_level: float, fng_v: int,
                        direction: str = 'LONG'):
    """
    פותח עסקת Breakout LONG או SHORT — Breakout Strategy.
    margin   = MARGIN ($50 fixed — caller value is ignored).
    pos_size = POSITION_SIZE ($500 = MARGIN × LEVERAGE).
    SL=2% | TP1=2% | TP2=4% (RR 1:2).
    """
    margin   = MARGIN        # always $50 — ignore caller-provided value
    pos_size = POSITION_SIZE  # always $500

    if wallet.get('balance', STARTING_BALANCE) < margin:
        print(f"[Breakout] insufficient balance for {symbol} — skip")
        return

    # ── Breakout Strategy Kill-Switch ────────────────────────────────────────
    if not ENABLE_BREAKOUT_STRATEGY:
        print(f"[Breakout] ⛔ ENABLE_BREAKOUT_STRATEGY=False — skip {symbol}", flush=True)
        return

    # ── Daily Circuit Breaker ────────────────────────────────────────────────
    if check_daily_circuit_breaker():
        print(f"[Breakout] ⛔ Circuit Breaker — לא פותחים {symbol} (הפסד יומי ≤ ${DAILY_LOSS_LIMIT})")
        return

    # ── RegimeClose Cooldown — מניעת פינג-פונג ───────────────────────────────
    _rc_ts_br = regime_close_cooldown.get(symbol, 0)
    if time.time() - _rc_ts_br < REGIME_CLOSE_COOLDOWN_SEC:
        _rc_min_br = int((REGIME_CLOSE_COOLDOWN_SEC - (time.time() - _rc_ts_br)) / 60)
        print(f"[RegimeCooldown] {symbol} בקולדאון {_rc_min_br}min — skip Breakout", flush=True)
        return

    # ── Trade Close Cooldown — 2h אחרי FastLoss/SL/ReversalGuard ─────────────
    _tcc_ts_br = trade_close_cooldown.get(symbol, 0)
    if time.time() - _tcc_ts_br < TRADE_CLOSE_COOLDOWN_SEC:
        _tcc_min_br = int((TRADE_CLOSE_COOLDOWN_SEC - (time.time() - _tcc_ts_br)) / 60)
        print(f"[TradeCooldown] {symbol} בקולדאון {_tcc_min_br}min — skip Breakout", flush=True)
        return
    # ── MaxDuration Cooldown — 6h אחרי MaxDuration (מונע SOL loop) ────────────
    _mdc_ts_br = max_duration_cooldown.get(symbol, 0)
    if time.time() - _mdc_ts_br < MAX_DURATION_COOLDOWN_SEC:
        _mdc_min_br = int((MAX_DURATION_COOLDOWN_SEC - (time.time() - _mdc_ts_br)) / 60)
        print(f"[MaxDurCooldown] {symbol} {_mdc_min_br}min נותרו (6h) — skip Breakout", flush=True)
        return

    # ── Market Regime Gate ────────────────────────────────────────────────────
    _allowed_b, _reason_b = is_direction_allowed(direction, context='Breakout')
    if not _allowed_b:
        print(f"[Breakout] {symbol} {direction} נדחה — {_reason_b}", flush=True)
        return

    # ── Claude AI Gate ────────────────────────────────────────────────────────
    _cl_regime_br, _, _cl_btc_br, _ = get_market_regime()
    _cl_ok_br, _cl_score_br, _cl_reason_br, _cl_risk_br = claude_gate.claude_trade_gate(
        symbol=symbol, direction=direction, strategy='Breakout',
        price=price, bot_score=0,
        regime=_cl_regime_br, fng=int(fng_v or 50), btc_above_ema=_cl_btc_br,
        rsi=rsi, reason=f"breakout h4={h4_level:.6g} vol={vol_ratio:.1f}x",
        extra={'H4 Level': f"${h4_level:.6g}", 'Vol Ratio': f"{vol_ratio:.1f}x"},
        daily_pnl=daily_stats.get('total_pnl', 0.0),
        open_trades=len(active_trades),
    )
    if not _cl_ok_br or _cl_score_br < 62:
        print(f"[ClaudeGate] ⛔ {symbol} Breakout נדחה — claude={_cl_score_br} | {_cl_reason_br}", flush=True)
        return

    tgt_b     = se.calc_targets(price, direction)
    sl_pct    = tgt_b['sl_pct']
    tp_pct    = tgt_b['tp_pct']
    tp1_pct   = tgt_b['tp1_pct']
    sl_price  = tgt_b['sl_price']
    tp_price  = tgt_b['tp_price']
    tp1_price = tgt_b['tp1_price']
    be_price  = tgt_b['be_price']

    if direction == 'LONG':
        level_lbl = f"4H High `${h4_level:.6g}`"
        dir_emoji = "🚀"
        dir_label = "LONG"
        sl_sign   = "-"; tp_sign = "+"
    else:  # SHORT
        level_lbl = f"4H Low `${h4_level:.6g}`"
        dir_emoji = "🩸"
        dir_label = "SHORT"
        sl_sign   = "+"; tp_sign = "-"

    rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
    cmp_sym = ">" if direction == 'LONG' else "<"
    reason  = (
        f"Breakout Strategy {direction}: 1H ${price:.6g} {cmp_sym} {level_lbl} | "
        f"FNG={fng_v} | RSI={rsi_str} | Vol×{vol_ratio:.1f}"
    )

    # ── נתוני 1H: לגם ATR וגם גרף (80 נרות = ~3.3 ימים) ─────────────────
    df_1h   = None
    atr_val = 0.0
    try:
        df_1h = get_data(symbol, timeframe='1h', limit=80)
        atr_s = ta.atr(df_1h['high'], df_1h['low'], df_1h['close'], length=14)
        if atr_s is not None and not atr_s.isna().all():
            atr_val = round(float(atr_s.iloc[-1]), 8)
    except Exception:
        pass

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
        'track':                'Breakout',
        'be_triggered':    False,
        'be_triggered':    False,
        'tp1_triggered':   False,
        'tp1_pnl':         0.0,
        'peak_price':      price,
        'trailing_sl':     None,
        'score':           95,
        'atr':             atr_val,
        'claude_score':    _cl_score_br,
        'claude_reason':   _cl_reason_br,
        'claude_key_risk': _cl_risk_br,
        'timeframe':       'Breakout',
        'rsi':             round(rsi, 2) if rsi is not None else None,
        'ema200':          None,
        'score_breakdown': reason,
        'opened_at':       now_il().isoformat(timespec='seconds'),
        'pos_size':        pos_size,
        'margin':          margin,
        'fng_at_entry':    fng_v,
    }
    place_order(trade, margin)

    short_name = symbol.replace('/USDT', '')
    msg = (
        f"{dir_emoji} *Breakout {dir_label} — {short_name}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 כניסה: `{price:.6g}`\n"
        f"🛑 SL:    `{sl_price:.6g}` ({sl_sign}{sl_pct}%)\n"
        f"🎯 TP1:   `{tp1_price:.6g}` ({tp_sign}{tp1_pct}%)\n"
        f"🎯 TP:    `{tp_price:.6g}` ({tp_sign}{tp_pct}%)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 RSI: {rsi_str} | Vol ×{vol_ratio:.1f} | FNG {fng_v} | 💼 {LEVERAGE}x · ${margin:.0f}"
    )
    chart_buf = generate_chart(df_1h, symbol, price, sl_price, tp1_price, direction) \
                if df_1h is not None else None
    send_chart_alert(chart_buf, symbol, msg)
    print(f"[Breakout] ✅ {direction} {symbol} @ {price:.6g} | margin=${margin:.0f} | RSI={rsi_str} | FNG={fng_v}")


def _breakout_determine_direction(btc_above_ema: bool, fng_v: int) -> str | None:
    """
    קובע כיוון עסקה לפי FNG + BTC EMA20.

    LONG:  BTC מעל EMA20 + FNG ≥ BREAKOUT_FNG_LONG_MIN (20) → שוק עולה, קונים פריצות
    SHORT: BTC מתחת EMA20 + FNG ≤ BREAKOUT_FNG_SHORT_MAX (65) → שוק יורד, שורטים שברים
    None:  סיגנלים מנוגדים → לא נכנסים

    היגיון:
      FNG 0-19  + BTC↓ → Extreme Fear + downtrend → SHORT ✅
      FNG 20-45 + BTC↑ → Fear + uptrend → LONG ✅
      FNG 20-45 + BTC↓ → Fear + downtrend → SHORT ✅
      FNG 46-65 + BTC↑ → Neutral/Greed + uptrend → LONG ✅
      FNG 46-65 + BTC↓ → Neutral/Greed + downtrend → SHORT ✅
      FNG 66-100+ BTC↑ → High Greed + uptrend → LONG ✅
      FNG 66-100+ BTC↓ → High Greed + downtrend → skip (חמדנות + ירידה = מטעה)
    """
    if fng_v is None:
        return None

    if btc_above_ema:
        # BTC בעלייה
        if fng_v >= BREAKOUT_FNG_LONG_MIN:   # FNG ≥ 20 → LONG
            return 'LONG'
        else:
            return None  # Extreme Fear + BTC בעלייה → סיגנל מבלבל, לא נכנסים
    else:
        # BTC בירידה
        if fng_v <= BREAKOUT_FNG_SHORT_MAX:   # FNG ≤ 65 → SHORT
            return 'SHORT'
        else:
            return None  # High Greed + BTC בירידה → מטעה, לא נכנסים


def top10_breakout_loop():
    """
    Thread 7 — Top 10 Breakout Auto-Scanner, כל 15 דקות.

    כיוון נקבע לפי FNG + BTC EMA20:
      LONG:  BTC > EMA20 + FNG ≥ 20 (פחד/נייטרל/חמדנות + BTC עולה)
      SHORT: BTC < EMA20 + FNG ≤ 65 (פחד/נייטרל/חמדנות בינוני + BTC יורד)
      Skip:  סיגנלים מנוגדים (Extreme Fear + BTC עולה, או High Greed + BTC יורד)

    סריקה:
      LONG:  1H close > 4H High + RSI < 65 + Volume ≥ 0.8x
      SHORT: 1H close < 4H Low  + RSI > 35 + Volume ≥ 0.8x

    מרג'ין = 10% מהיתרה הפנויה | RR 1:3 | Trailing 2.5%
    הרצה ראשונה מיידית (כולל ETH).
    """
    TOP10_INTERVAL = 5 * 60   # 5 דקות — היה 15
    print("Thread 7 (Top10 Breakout) started.")
    first_run = True

    while True:
        if not first_run:
            time.sleep(TOP10_INTERVAL)
        first_run = False
        if _bot_paused:
            continue

        try:
            # ── 1. נתוני BTC + FNG ────────────────────────────────────────
            btc_above_ema      = _btc_above_ema20_15m()
            fng_v, fng_lbl, _  = sentiment_check("top10_breakout")

            if fng_v is None:
                print("[Top10 Breakout] FNG=None — skip")
                continue

            # ── 2. קביעת כיוון ────────────────────────────────────────────
            direction = _breakout_determine_direction(btc_above_ema, fng_v)
            if direction is None:
                btc_lbl = "↑ BTC > EMA20" if btc_above_ema else "↓ BTC < EMA20"
                print(f"[Top10 Breakout] Mixed signals — FNG={fng_v} {btc_lbl} → skip")
                continue

            # ── 2b. Trend Bias: BTC > EMA200 על 1H+4H → חסום SHORTs ──────────
            # אם BTC מעל EMA200 בשני גרפים → שוק עולה מאקרו, לא נלחמים בטרנד
            if direction == 'SHORT':
                _sup_break, _ema1h_b, _ema4h_b = is_btc_strong_uptrend()
                if _sup_break:
                    print(f"[Top10 Breakout] 🟢 TREND BIAS: BTC Strong Uptrend (1H+4H>EMA200) — blocking SHORT signals, waiting for LONG setup")
                    continue

            # ── 3. Max Trades ──────────────────────────────────────────────
            if len(active_trades) >= MAX_TRADES:
                print(f"[Top10 Breakout] Max trades ({MAX_TRADES}) — skip")
                continue

            # ── 4. סריקה ──────────────────────────────────────────────────
            existing_syms = {t['symbol'] for t in active_trades}
            candidates    = []

            for sym in TOP10_SYMBOLS:
                if sym in existing_syms:
                    continue
                # ── SLOW_MOVERS blacklist — BNB/SKYAI/TRX/ADA/DOGE ───────────────
                if sym in SLOW_MOVERS:
                    print(f"[Top10 Breakout] {sym} — SLOW_MOVERS blacklist, skip", flush=True)
                    continue
                # ── Trade Close Cooldown — 2h אחרי FastLoss/SL/ReversalGuard ───────
                _tcc_ts_br2 = trade_close_cooldown.get(sym, 0)
                if time.time() - _tcc_ts_br2 < TRADE_CLOSE_COOLDOWN_SEC:
                    _tcc_min_br2 = int((TRADE_CLOSE_COOLDOWN_SEC - (time.time() - _tcc_ts_br2)) / 60)
                    print(f"[Top10 Breakout] {sym} TradeCooldown {_tcc_min_br2}min — skip", flush=True)
                    continue
                # ── MaxDuration Cooldown — 6h (SOL loop prevention) ────────────────
                _mdc_ts_br2 = max_duration_cooldown.get(sym, 0)
                if time.time() - _mdc_ts_br2 < MAX_DURATION_COOLDOWN_SEC:
                    _mdc_min_br2 = int((MAX_DURATION_COOLDOWN_SEC - (time.time() - _mdc_ts_br2)) / 60)
                    print(f"[Top10 Breakout] {sym} MaxDurCooldown {_mdc_min_br2}min — skip", flush=True)
                    continue
                # Cooldown: דלג אם המטבע לקח SL בפריצה ב-5 השעות האחרונות
                _sl_ts = breakout_sl_cooldown.get(sym)
                if _sl_ts and (time.time() - _sl_ts) < BREAKOUT_SL_COOLDOWN_SEC:
                    _cd_remaining = (BREAKOUT_SL_COOLDOWN_SEC - (time.time() - _sl_ts)) / 3600
                    print(f"[Top10 Breakout] {sym} SL cooldown — {_cd_remaining:.1f}h remaining")
                    continue
                signal, price, h4_level, rsi, vol_ratio = _coin_breakout_full(sym, direction)
                if not signal:
                    continue

                # RSI Filter — Breakout with high volume allows RSI up to 78
                # In BULLISH regime, raise limit to 75: bull runs have naturally elevated RSI
                if direction == 'LONG':
                    if btc_above_ema:
                        _rsi_limit = 75   # BULLISH regime — trending coins have RSI 70-90
                    else:
                        _rsi_limit = RSI_VETO_BREAKOUT_LONG if vol_ratio >= VOL_EMA_BYPASS_MULT else RSI_VETO_LONG
                    if rsi is not None and rsi > _rsi_limit:
                        print(f"[Top10 Breakout] {sym} LONG RSI veto ({rsi:.0f} > {_rsi_limit})")
                        continue
                else:  # SHORT
                    if rsi is not None:
                        if not btc_above_ema:
                            # BEARISH regime: חלון RSI 30–58 — bounce entries ו-recovery מסוננים
                            if rsi < RSI_VETO_BREAKOUT_SHORT_BEAR_MIN or rsi > RSI_VETO_BREAKOUT_SHORT_BEAR_MAX:
                                print(f"[Top10 Breakout] {sym} SHORT RSI veto — BEAR window ({rsi:.0f} outside {RSI_VETO_BREAKOUT_SHORT_BEAR_MIN}–{RSI_VETO_BREAKOUT_SHORT_BEAR_MAX})")
                                continue
                        else:
                            # NEUTRAL/BULL: require RSI ≥ 60 to fade only overbought pumps
                            if rsi < RSI_VETO_BREAKOUT_SHORT:
                                print(f"[Top10 Breakout] {sym} SHORT RSI veto ({rsi:.0f} < {RSI_VETO_BREAKOUT_SHORT})")
                                continue

                # Volume Filter
                if vol_ratio < BREAKOUT_MIN_VOL:
                    print(f"[Top10 Breakout] {sym} low volume ({vol_ratio:.1f}x)")
                    continue

                candidates.append({
                    'symbol': sym, 'price': price, 'h4_level': h4_level,
                    'rsi': rsi, 'vol_ratio': vol_ratio,
                })
                print(f"[Top10 Breakout] ✅ {direction} {sym} | RSI={rsi} | Vol={vol_ratio:.1f}x")

            if not candidates:
                print(f"[Top10 Breakout] {direction} — no signals in {len(TOP10_SYMBOLS)} coins")
                continue

            # ── 5. מיון ───────────────────────────────────────────────────
            # Priority:
            #   0. BTC/ETH — Leaders תמיד ראשונים (major_bonus=0)
            #   1. Sector coins (AI/RWA) — sector_bonus=0 sorts before 1
            #   2. Volume high (>1.5x average) → הוכח עניין אמיתי
            #   3. LONG: RSI in sweet-spot / SHORT: highest RSI first
            def _sort_key(c):
                major_bonus  = 0 if c['symbol'] in MAJOR_PRIORITY_SYMBOLS else 1
                sector_bonus = 0 if c['symbol'] in SECTOR_PRIORITY_SYMBOLS else 1
                rsi_v        = c['rsi'] if c['rsi'] is not None else (999 if direction == 'LONG' else 0)
                vol_key      = -c['vol_ratio']  # higher vol = lower key = earlier
                if direction == 'LONG':
                    rsi_key = abs(rsi_v - 62.5)   # closest to midpoint 62.5 = best
                else:
                    rsi_key = -rsi_v              # highest RSI first for SHORT
                return (major_bonus, sector_bonus, rsi_key, vol_key)

            candidates.sort(key=_sort_key)

            # ── 6. פתיחת עסקאות ───────────────────────────────────────────
            for c in candidates:
                if len(active_trades) >= MAX_TRADES:
                    break
                avail = wallet.get('balance', STARTING_BALANCE)
                if avail < 5:
                    print("[Top10 Breakout] Balance too low — stop")
                    break
                open_breakout_trade(
                    symbol    = c['symbol'],
                    price     = c['price'],
                    margin    = MARGIN,  # fixed — open_breakout_trade overrides anyway
                    rsi       = c['rsi'],
                    vol_ratio = c['vol_ratio'],
                    h4_level  = c['h4_level'],
                    fng_v     = fng_v,
                    direction = direction,
                )
                time.sleep(2)

        except Exception as e:
            print(f"[Top10 Breakout] Error: {e}")


def bubble_watch_scan_loop():
    """
    Thread 8 — Bubble Watch Active Scanner, כל 15 דקות (Production בלבד).

    שלבים:
      1. שולף Top 15 Gainers + Top 15 Losers (get_hot_candidates)
      2. מסנן מטבעות עם שינוי >10% ב-24h (Bubble Watch definition)
      3. מריץ _scan_batch → מנוע הציון המלא (4H→1H→15m, RSI, EMA, Wick...)
      4. פותח עסקה אם ציון ≥ MIN_SCORE (90) וכל הבדיקות עוברות
    """
    if not IS_DEPLOYED:
        print("⚠️  [DEV] Bubble Watch Scanner DISABLED (IS_DEPLOYED=False). Prod only.")
        return

    print("Thread 8 (Bubble Watch Scanner) started.")
    BUBBLE_SCAN_INTERVAL = 900   # 15 דקות

    while True:
        time.sleep(BUBBLE_SCAN_INTERVAL)
        if _bot_paused:
            continue
        try:
            # ── 1. בדיקות מקדימות ──────────────────────────────────────────
            if len(active_trades) >= MAX_TRADES:
                print("[BubbleWatch] Max trades — skip")
                continue

            wallet = load_wallet()
            if wallet.get('balance', 0) < 5:
                print("[BubbleWatch] Balance too low — skip")
                continue

            # ── 2. שלוף גיינרים ולוזרים ──────────────────────────────────
            gainers, losers = get_hot_candidates()
            if not gainers and not losers:
                continue

            # ── 3. סנן ל-Bubble Watch (>10% שינוי) ───────────────────────
            bubble_longs  = [g for g in gainers if g.get('change_pct', 0) >  10.0]
            bubble_shorts = [l for l in losers  if abs(l.get('change_pct', 0)) > 10.0]

            total = len(bubble_longs) + len(bubble_shorts)
            if total == 0:
                print("[BubbleWatch] No coins >10% change — skip")
                continue

            now_str = now_il().strftime('%H:%M')
            print(f"[BubbleWatch] {now_str} — {len(bubble_longs)} LONG + {len(bubble_shorts)} SHORT candidates >10%")
            send_msg(
                f"🫧 *Bubble Watch Scan* — {now_str}\n"
                f"🟢 LONG: *{len(bubble_longs)}*  🔴 SHORT: *{len(bubble_shorts)}*  מטבעות עם שינוי >10%\n"
                f"📐 מריץ ניתוח מלא — ציון מינימום {MIN_SCORE}/100..."
            )

            # ── 4. BTC Regime ─────────────────────────────────────────────
            btc_regime = get_btc_regime()

            # ── 5. ציון מלא + פתיחת עסקאות ──────────────────────────────
            all_rejections = []
            signals_found  = 0

            if bubble_longs:
                signals_found += _scan_batch(
                    bubble_longs, 'LONG',
                    btc_regime=btc_regime,
                    rejected_out=all_rejections,
                )

            if bubble_shorts and len(active_trades) < MAX_TRADES:
                signals_found += _scan_batch(
                    bubble_shorts, 'SHORT',
                    btc_regime=btc_regime,
                    rejected_out=all_rejections,
                )

            # ── 6. דוח תוצאות ─────────────────────────────────────────────
            if signals_found == 0:
                # תמצית סיבות הדחייה (עד 5)
                reasons = []
                for r in all_rejections[:5]:
                    sym_clean = r['symbol'].replace('/USDT', '')
                    reasons.append(f"  ∙ {sym_clean}: {r['reason']}")
                reasons_txt = "\n".join(reasons) if reasons else "  ∙ לא עברו את הציון"
                send_msg(
                    f"🫧 *Bubble Watch* — אין כניסות\n\n"
                    f"{reasons_txt}\n\n"
                    f"_הסריקה הבאה בעוד 15 דקות_"
                )
            else:
                send_msg(
                    f"🫧 *Bubble Watch* — נפתחו *{signals_found}* עסקאות\n"
                    f"_ניהול SL/TP פעיל — יעדכן בשינויים_"
                )

        except Exception as e:
            print(f"[BubbleWatch] Error: {e}")


def cliff_hanger_loop():
    """
    Thread 9 — High-Velocity Scanner (Rocket LONG + Cliff SHORT), כל 2 דקות.

    מחפש נרות 5m עם תנועה >2.5% + Volume >400% ממוצע:
      🚀 Rocket: עלייה  → LONG — רק כשBTC מעל EMA20 (₿ Compass)
      🪂 Cliff:  ירידה  → SHORT — רק כשBTC מתחת EMA20 (₿ Compass)
    Trailing Stop · Break-Even · תוקף 30 דקות.
    """
    print("Thread 9 (High-Velocity: Rocket+Cliff) started.")

    while True:
        time.sleep(CLIFF_SCAN_INTERVAL)
        try:
            # מגבלות גלובליות
            if len(active_trades) >= MAX_TRADES:
                continue
            cliff_count = sum(1 for t in active_trades if t.get('cliff'))
            if cliff_count >= MAX_CLIFF_TRADES:
                continue
            if wallet.get('balance', 0) < CLIFF_MARGIN:
                continue

            # ── ₿ BTC Compass — בודק כיוון פעם אחת לכל מחזור ──────────────
            btc_above_ema = _btc_above_ema20_15m()

            existing_syms = {t['symbol'] for t in active_trades}

            for sym in TOP10_SYMBOLS:
                if sym in existing_syms:
                    continue
                if len(active_trades) >= MAX_TRADES:
                    break
                cliff_count = sum(1 for t in active_trades if t.get('cliff'))
                if cliff_count >= MAX_CLIFF_TRADES:
                    break
                # ── SLOW_MOVERS blacklist ─────────────────────────────────────
                if sym in SLOW_MOVERS:
                    continue
                # ── MaxDuration Cooldown — 6h (SOL loop prevention) ───────────
                _mdc_ts_v = max_duration_cooldown.get(sym, 0)
                if time.time() - _mdc_ts_v < MAX_DURATION_COOLDOWN_SEC:
                    continue

                is_velocity, vel_dir, move_pct, vol_ratio, rsi_div = _cliff_detect(sym)
                if not is_velocity:
                    continue

                # ── ₿ BTC Compass Filter ────────────────────────────────────
                # Rocket LONG רק כשBTC עולה | Cliff SHORT רק כשBTC יורד
                if vel_dir == 'LONG' and not btc_above_ema:
                    print(f"[Velocity] 🚀 {sym} LONG skipped — BTC bearish compass")
                    continue
                if vel_dir == 'SHORT' and btc_above_ema:
                    print(f"[Velocity] 🪂 {sym} SHORT skipped — BTC bullish compass")
                    continue

                # שלוף מחיר נוכחי
                try:
                    ticker = exchange.fetch_ticker(sym)
                    price  = ticker['last']
                except Exception:
                    continue

                label = "🚀 Rocket" if vel_dir == 'LONG' else "🪂 Cliff"
                div_tag = " ⚡RSI-Div" if rsi_div else ""
                print(f"[Velocity] {label}{div_tag} {sym} — Move={move_pct:.1f}% Vol={vol_ratio:.1f}× ₿OK")
                open_cliff_trade(sym, price, vel_dir, move_pct, vol_ratio, rsi_div)
                existing_syms.add(sym)
                time.sleep(1)

        except Exception as e:
            print(f"[Velocity] Error: {e}")


def sol_watch_loop():
    """
    Thread 5 — מעקב SOL כל 15 דקות.
    לוגיקה חדשה (Momentum Breakout):
      • ABORT  — BTC/USDT 15m price < EMA20 (מגמה שלילית)
      • EXECUTE — SOL 1H close > recent 4H high (breakout מאושר)
      • WAIT   — אחרת
    שולח התראה רק כשיש שינוי במצב.
    """
    SOL_WATCH_INTERVAL = 15 * 60   # כל 15 דקות

    last = {
        'decision'        : None,   # 'EXECUTE' | 'WAIT' | 'ABORT'
        'btc_above_ema'   : None,   # True/False — BTC > EMA20 15m
        'breakout'        : None,   # True/False — SOL 1H > 4H high
    }

    print("[SOL Watch] Loop started — checking every 15 min")

    while True:
        time.sleep(SOL_WATCH_INTERVAL)
        if _bot_paused:
            continue
        try:
            # ── נתוני BTC ──────────────────────────────────────────────────
            btc_above_ema = _btc_above_ema20_15m()

            # ── נתוני SOL ──────────────────────────────────────────────────
            df_sol        = get_data('SOL/USDT', timeframe='1h', limit=5)
            current_price = float(df_sol['close'].iloc[-1])

            breakout, sol_1h_close, h4_high = _sol_1h_breakout_above_4h_high()

            # RSI 4H לצרכי display בלבד
            df_sol_4h = get_data('SOL/USDT', timeframe='4h', limit=20)
            rsi_s = ta.rsi(df_sol_4h['close'], length=14)
            rsi   = round(float(rsi_s.iloc[-1]), 2) if rsi_s is not None else None

            # ── החלטה ──────────────────────────────────────────────────────
            if not btc_above_ema:
                decision = 'ABORT'      # BTC במגמה שלילית
            elif breakout:
                decision = 'EXECUTE'    # 1H פרץ מעל 4H high
            else:
                decision = 'WAIT'

            # ── בדיקת שינויים ──────────────────────────────────────────────
            changes       = []
            prev_decision = last['decision']

            if last['decision'] is not None and decision != last['decision']:
                changes.append(f"📌 החלטה: `{last['decision']}` → `{decision}`")

            if last['btc_above_ema'] is not None and btc_above_ema != last['btc_above_ema']:
                if btc_above_ema:
                    changes.append("🟢 BTC חזר מעל EMA20 15m — מגמה חיובית ✅")
                else:
                    changes.append("🔴 BTC ירד מתחת EMA20 15m — מגמה שלילית ⛔")

            if last['breakout'] is not None and breakout != last['breakout']:
                if breakout:
                    changes.append(f"🚀 SOL 1H פרץ מעל 4H High\\! `${sol_1h_close:.3f}` > `${h4_high:.3f}` ✅")
                else:
                    changes.append(f"📉 SOL 1H ירד חזרה מתחת 4H High \\(`${h4_high:.3f}`\\) ❌")

            # ── עדכון מצב ──────────────────────────────────────────────────
            last['decision']      = decision
            last['btc_above_ema'] = btc_above_ema
            last['breakout']      = breakout

            # ── שלח הודעה רק אם יש שינוי ──────────────────────────────────
            if not changes:
                if VERBOSE_LOG:
                    print(f"[SOL Watch] No change — {decision} | breakout={breakout} | BTC_EMA={btc_above_ema}")
                continue

            sl      = round(current_price * 0.965, 3)
            tp      = round(current_price * 1.105, 3)   # TP 10.5%
            now_str = now_il().strftime('%H:%M')
            dec_emoji     = {'EXECUTE': '🟢 ✅', 'WAIT': '🟡 ⏳', 'ABORT': '🔴 ⛔'}.get(decision, '⏳')
            change_lines  = "\n".join(f"  {c}" for c in changes)
            btc_label     = "✅ מעל EMA20" if btc_above_ema else "⛔ מתחת EMA20"
            breakout_label = f"✅ פרץ \\(`${sol_1h_close:.3f}` > `${h4_high:.3f}`\\)" if breakout \
                             else f"⏳ ממתין \\(4H High: `${h4_high:.3f}`\\)"

            msg = (
                f"🔔 *SOL Watch — שינוי זוהה\\!*\n"
                f"⏰ {now_str}\n"
                f"{'─' * 26}\n\n"
                f"{change_lines}\n\n"
                f"💰 מחיר SOL: `${current_price:.3f}`\n"
                f"🔍 RSI 4H: `{rsi}`\n"
                f"₿  BTC 15m EMA20: {btc_label}\n"
                f"📈 Breakout 1H>4H: {breakout_label}\n\n"
                f"🎯 *החלטה: {decision}* {dec_emoji}\n"
            )

            if decision == 'EXECUTE':
                msg += (
                    f"\n📐 *רמות עסקה:*\n"
                    f"  🛑 SL: `${sl}` \\(\\-3\\.5%\\)\n"
                    f"  🎯 TP: `${tp}` \\(\\+10\\.5%\\)\n"
                    f"  📍 Trailing: {TRAIL_PCT}%\n\n"
                    f"_/sol לניתוח מלא_"
                )
            elif decision == 'ABORT':
                msg += f"\n_⛔ BTC במגמה שלילית — לא נכנסים_"
            else:
                msg += f"\n_⏳ ממתין לפריצה מעל 4H High: `${h4_high:.3f}`_"

            send_msg(msg)
            print(f"[SOL Watch] Alert sent — {decision} | breakout={breakout} | BTC_EMA={btc_above_ema}")

            # ── רישום עסקה אוטומטי כש-WATCH מזהה מעבר ל-EXECUTE ──────────────
            if decision == 'EXECUTE' and prev_decision != 'EXECUTE':
                sl_w = round(current_price * 0.965, 3)
                tp_w = round(current_price * 1.105, 3)
                _register_sol_trade(current_price, sl_w, tp_w, rsi)

        except Exception as e:
            print(f"[SOL Watch] Error: {e}")


def major_watch_loop():
    """
    Thread — מעקב מטבעות גדולים כל 5 דקות (Momentum Breakout).
    אותה לוגיקה כמו SOL Watch אבל ל-8 מטבעות גדולים:
      BTC, ETH, BNB, XRP, SOL, ADA, AVAX, DOGE.
    שולח התראה רק כשמצב מטבע מסוים משתנה.
    כשיש EXECUTE: פותח עסקת Swing אוטומטית.
    הרצה ראשונה מיידית — אין המתנה כלל.
    """
    global _major_watch_state
    INTERVAL   = 5 * 60   # 5 דקות — היה 15
    first_run  = True

    last = {sym: {'decision': None, 'trend_ok': None, 'breakout': None}
            for sym in MAJOR_WATCH_COINS}
    _major_watch_state = last

    print(f"[Major Watch] Loop started — tracking {len(MAJOR_WATCH_COINS)} coins every 5 min", flush=True)

    while True:
        if not first_run:
            time.sleep(INTERVAL)
        first_run = False
        if _bot_paused:
            continue
        try:
            btc_above_ema = _btc_above_ema20_15m()   # BTC trend filter (לשאר המטבעות)
            now_str       = now_il().strftime('%H:%M')

            for symbol in MAJOR_WATCH_COINS:
                try:
                    ticker_name = symbol.replace('/USDT', '')
                    breakout, price_1h, h4_high, rsi = _coin_1h_breakout_above_4h_high(symbol)

                    # BTC — פילטר עצמי (EMA50 4H במקום EMA20 15m)
                    if symbol == 'BTC/USDT':
                        try:
                            df_btc4h = get_data('BTC/USDT', timeframe='4h', limit=60)
                            ema50    = ta.ema(df_btc4h['close'], length=50)
                            trend_ok = float(df_btc4h['close'].iloc[-1]) > float(ema50.iloc[-1]) \
                                       if ema50 is not None else True
                        except Exception:
                            trend_ok = True
                    else:
                        trend_ok = btc_above_ema

                    # החלטה
                    if not trend_ok:
                        decision = 'ABORT'
                    elif breakout:
                        decision = 'EXECUTE'
                    else:
                        decision = 'WAIT'

                    prev          = last[symbol]
                    prev_decision = prev['decision']
                    is_first_seen = (prev_decision is None)   # הרצה ראשונה לסמל זה
                    changes       = []

                    if is_first_seen:
                        # הרצה ראשונה — מדווחים רק על EXECUTE (פריצה קיימת עם תחילת הבוט)
                        if decision == 'EXECUTE':
                            changes.append(f"🚀 *זוהה פריצה קיימת בהפעלת הבוט\\!*")
                            changes.append(f"  `${price_1h:.5g}` > 4H High `${h4_high:.5g}`")
                    else:
                        if decision != prev_decision:
                            changes.append(f"📌 החלטה: `{prev_decision}` → `{decision}`")

                        if prev['trend_ok'] is not None and trend_ok != prev['trend_ok']:
                            if trend_ok:
                                changes.append("🟢 מגמה חיובית — BTC EMA חזר ✅")
                            else:
                                changes.append("🔴 מגמה שלילית — BTC מתחת EMA ⛔")

                        if prev['breakout'] is not None and breakout != prev['breakout']:
                            if breakout:
                                changes.append(f"🚀 פריצה מעל 4H High\\! `${price_1h:.5g}` > `${h4_high:.5g}` ✅")
                            else:
                                changes.append(f"📉 ירד מתחת 4H High \\(`${h4_high:.5g}`\\) ❌")

                    # עדכון מצב
                    last[symbol]['decision']  = decision
                    last[symbol]['trend_ok']  = trend_ok
                    last[symbol]['breakout']  = breakout
                    last[symbol]['price']     = price_1h
                    last[symbol]['h4_high']   = h4_high
                    last[symbol]['rsi']       = rsi

                    if not changes:
                        if VERBOSE_LOG:
                            print(f"[Major Watch] {ticker_name}: no change — {decision}")
                        continue

                    # ── בניית הודעה ────────────────────────────────────────────
                    dec_emoji      = {'EXECUTE': '🟢 ✅', 'WAIT': '🟡 ⏳', 'ABORT': '🔴 ⛔'}.get(decision, '⏳')
                    change_lines   = "\n".join(f"  {c}" for c in changes)
                    coin_icon      = MAJOR_WATCH_ICONS.get(ticker_name, '🔹')
                    trend_label    = "✅ מעל EMA" if trend_ok else "⛔ מתחת EMA"
                    breakout_label = (f"✅ פרץ \\(`${price_1h:.5g}` > `${h4_high:.5g}`\\)"
                                     if breakout
                                     else f"⏳ ממתין \\(4H High: `${h4_high:.5g}`\\)")

                    msg = (
                        f"🔔 *{coin_icon} {ticker_name} Watch — שינוי זוהה\\!*\n"
                        f"⏰ {now_str}\n"
                        f"{'─' * 26}\n\n"
                        f"{change_lines}\n\n"
                        f"💰 מחיר: `${price_1h:.5g}`\n"
                    )
                    if rsi is not None:
                        rsi_tag = "🔥 Overbought" if rsi > 70 else ("❄️ Oversold" if rsi < 30 else "")
                        msg += f"🔍 RSI 4H: `{rsi}` {rsi_tag}\n"
                    if symbol != 'BTC/USDT':
                        msg += f"₿  BTC 15m EMA20: {'✅ מעל' if btc_above_ema else '⛔ מתחת'}\n"
                    else:
                        msg += f"₿  BTC EMA50 4H: {trend_label}\n"
                    msg += (
                        f"📈 Breakout 1H>4H: {breakout_label}\n\n"
                        f"🎯 *החלטה: {decision}* {dec_emoji}\n"
                    )
                    if decision == 'ABORT':
                        msg += f"\n_⛔ מגמה שלילית — לא נכנסים_"
                    elif decision == 'WAIT':
                        msg += f"\n_⏳ ממתין לפריצה מעל 4H High: `${h4_high:.5g}`_"
                    else:
                        msg += f"\n_💡 /major לסיכום כל המטבעות_"

                    send_msg(msg)
                    print(f"[Major Watch] Alert: {ticker_name} → {decision} | breakout={breakout}")

                    # ── פתיחת עסקה אוטומטית ב-EXECUTE ─────────────────────────
                    if decision == 'EXECUTE' and prev_decision != 'EXECUTE':
                        threading.Thread(
                            target=open_demo_trade,
                            kwargs=dict(
                                symbol    = symbol,
                                direction = 'LONG',
                                price     = price_1h,
                                score     = 88,
                                reason    = f"Major Watch Breakout — 1H > 4H High (${h4_high:.5g})",
                                df_3h     = None,
                                atr       = round(price_1h * 0.012, 6),
                                timeframe = '1H',
                                tf_reason = f"פריצה מעל 4H High",
                                rsi       = rsi,
                                fng_v     = None,
                            ),
                            daemon=True,
                        ).start()

                except Exception as coin_err:
                    print(f"[Major Watch] Error for {symbol}: {coin_err}")

        except Exception as e:
            print(f"[Major Watch] Loop error: {e}")


def watch_loop():
    """
    Thread נפרד — בודק כל מטבע ב-Watch List כל 15 דקות.
    שולח עדכון גם אם אין שינוי גדול (ק 5 נקודות — silent).
    """
    WATCH_INTERVAL = 15 * 60   # 15 דקות
    WATCH_TTL      = 24 * 3600  # פג תוקף אחרי 24 שעות

    while True:
        time.sleep(WATCH_INTERVAL)
        with watch_lock:
            to_remove = []
            items = list(watch_list.items())

        for symbol, entry in items:
            # בדיקת פג תוקף
            expires_ts = entry.get('expires_at', 0)
            if time.time() > expires_ts:
                with watch_lock:
                    watch_list.pop(symbol, None)
                send_msg(
                    f"🔭 *Watch פג תוקף — {symbol.replace('/',  '\\/')}*\n"
                    f"_24 שעות עברו — הוסף שוב עם /watch_"
                )
                continue

            _run_watch_check(symbol, entry, silent=True)


def research_loop():
    """
    Dr. Sniper Autonomous Research — רץ כל 120 דקות ב-PROD בלבד.
    מאחסן נתונים מ-CoinGecko, exchange, DeFiLlama, web ומחזיר המלצות.
    שימוש ידני: /research (עובד גם ב-DEV).
    """
    if not IS_DEPLOYED:
        print("⚠️  [DEV] Research loop DISABLED (IS_DEPLOYED=False). Use /research manually.", flush=True)
        return
    print("[Research] Loop started — running every 120 minutes (first run in 30min)", flush=True)
    time.sleep(30 * 60)   # wait 30 min after startup before first run
    while True:
        if _bot_paused:
            time.sleep(60)
            continue
        try:
            claude_research.run_claude_research(
                open_trades_count=len(active_trades),
                daily_pnl=daily_stats.get('total_pnl', 0.0),
                fng=_fng_cache.get('value', 50),
                max_trades=MAX_TRADES,
            )
        except Exception as _rl_e:
            print(f"[Research] Loop error: {_rl_e}", flush=True)
        time.sleep(480 * 60)   # 3 runs/day - enough samples to score the engine


def scan_loop():
    """
    רץ בThread נפרד.
    סורק Top 15 Gainers (LONG) + Top 15 Losers (SHORT) פעם בשעה.
    מפעיל Professional Scoring System — מינימום 85 נקודות לאיתות.
    רץ רק ב-Production Deployment. בסביבת הפיתוח — /scan ידני בלבד.
    """
    if not IS_DEPLOYED:
        print("⚠️  [DEV] Auto-scan loop DISABLED (IS_DEPLOYED=False).")
        print("    Use /scan in Telegram or test manually. Prod bot handles automated scans.")
        return   # יוצא מהפונקציה — אין לולאה אוטומטית ב-dev

    print("Scan loop started — scanning every 60 minutes")
    while True:
        if _bot_paused:
            time.sleep(60)
            continue
        try:
            check_daily_report()
            now_str = now_il().strftime('%H:%M:%S')
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

            # ── Claude Sandbox Mode (Extreme Fear בלבד) ─────────────────────
            sandbox_results = []
            if fng_v_loop < EXTREME_FEAR_THRESHOLD and bubble_watch_list:
                print(f"[Sandbox] Extreme Fear (FNG={fng_v_loop}) — running educational Claude analysis...")
                sandbox_results = claude_sandbox_analysis(
                    bubble_watch_list, btc_regime, fng_v_loop, fng_lbl_loop
                )
                if sandbox_results:
                    print(f"[Sandbox] Analysis complete: {len(sandbox_results)} coin(s) analyzed")

            # ── סיכום סריקה ──
            now       = now_il().strftime('%H:%M')
            next_scan = (now_il() + timedelta(hours=1)).strftime('%H:%M')
            pnl_today = round(daily_stats.get('total_pnl', 0), 2)
            pnl_icon  = "📈" if pnl_today >= 0 else "📉"

            fng_emoji_loop = ("😱" if fng_v_loop < 13 else
                              "😨" if fng_v_loop < 25 else
                              "😟" if fng_v_loop < 40 else
                              "😐" if fng_v_loop < 60 else
                              "😊" if fng_v_loop < 75 else "🤑")
            ks_note = " 🔒 Kill\\-Switch" if fng_v_loop < EXTREME_FEAR_THRESHOLD else ""

            summary  = f"✅ *סריקה הושלמה — {now}*\n\n"
            summary += f"🔍 נסרקו: *{total_scanned}* מטבעות\n"
            summary += f"📊 איתותים שנמצאו: *{signals_found}*\n"
            summary += f"{fng_emoji_loop} Fear & Greed: *{fng_v_loop}* — _{fng_lbl_loop}_{ks_note}\n"
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
                    for b in bubble_watch_list          # כל המטבעות — ללא חיתוך
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
                sys_msg = f"Extreme Fear: FNG={fng_v_loop} — חוקים קבועים פעילים, ממשיך לסרוק"
            elif signals_found == 0 and not is_direction_allowed('LONG')[0]:
                _l_why = is_direction_allowed('LONG')[1]
                _s_ok, _s_why = is_direction_allowed('SHORT')
                _blk = '\u05d7\u05e1\u05d5\u05dd'
                sys_msg = 'LONG ' + _blk + ' \u2014 ' + _l_why + (
                    ' | SHORT ' + _blk + ' \u2014 ' + _s_why if not _s_ok
                    else ' | SHORT \u05de\u05d5\u05ea\u05e8 (\u05e1\u05e3 \u05d0\u05e4\u05e7\u05d8\u05d9\u05d1\u05d9 \u05d2\u05d1\u05d5\u05d4)')
            elif signals_found == 0 and not is_direction_allowed('SHORT')[0]:
                sys_msg = 'SHORT \u05d7\u05e1\u05d5\u05dd \u2014 ' + is_direction_allowed('SHORT')[1]
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
                total_scanned    = total_scanned,
                signals_found    = signals_found,
                fng_value        = fng_v_loop,
                fng_label        = fng_lbl_loop,
                btc_regime       = btc_regime,
                all_rejections   = all_rejections,
                system_message   = sys_msg,
                scan_start_ts    = scan_start_ts,
                bubble_watch     = bubble_watch_list,
                sandbox_analysis = sandbox_results,
            )

            # ── דוח Drive ב-12:00 ──
            _maybe_run_drive_audit()

        except Exception as e:
            print(f"Scan loop error: {e}")

        time.sleep(3600)

# --- הלולאה הראשית ---

def reconcile_with_exchange():
    """
    Authoritative startup sync: rebuild active_trades from Bitget's open
    positions. Local entries that match an open position keep their stored
    metadata (sl/tp/score/etc.); positions on the exchange with no local
    record are imported with conservative defaults; local entries with no
    matching open position are dropped (they closed while we were offline).
    """
    global active_trades
    try:
        positions = exchange.fetch_positions()
    except Exception as e:
        print(f"[SYNC] reconciled 0 positions from Bitget — fetch_positions failed: {e}", flush=True)
        return

    # Index live positions by symbol
    live: dict[str, dict] = {}
    for p in positions:
        try:
            contracts = float(p.get('contracts') or 0)
        except (TypeError, ValueError):
            contracts = 0.0
        sym = p.get('symbol')
        if contracts <= 0 or not sym:
            continue
        live[sym] = p

    with trades_lock:
        local_by_sym = {t.get('symbol'): t for t in active_trades if t.get('symbol')}
        rebuilt: list[dict] = []
        imported: list[str] = []

        for sym, pos in live.items():
            # Extract authoritative financial fields from live position
            try:
                entry = float(pos.get('entryPrice') or pos.get('info', {}).get('openPriceAvg') or 0)
            except (TypeError, ValueError):
                entry = 0.0
            side = (pos.get('side') or 'long').upper()
            direction = 'LONG' if side.startswith('L') else 'SHORT'
            try:
                contracts = float(pos.get('contracts') or 0)
            except (TypeError, ValueError):
                contracts = 0.0
            try:
                lev = float(pos.get('leverage') or LEVERAGE)
            except (TypeError, ValueError):
                lev = float(LEVERAGE)
            pos_size = round(entry * contracts, 2) if entry and contracts else POSITION_SIZE
            margin = round(pos_size / lev, 2) if lev else MARGIN

            if sym in local_by_sym:
                local = local_by_sym[sym]
                # Start from authoritative live position fields, then overlay
                # optional metadata from local record (sl/tp/score/strategy/...).
                # This ensures financial fields (entry/size/side) are always
                # accurate even if the bot restarted mid-trade.
                # After a partial close or TP1, local pos_size represents the
                # lifecycle basis used by the monitor (the final remainder is a
                # percentage of this value). The exchange reports only the
                # already-reduced remainder, so replacing it here would apply
                # the TP1/partial multiplier a second time after restart.
                lifecycle_managed = bool(
                    local.get('partial_25_triggered')
                    or local.get('tp1_triggered')
                    or local.get('phase') == 'trailing'
                )
                lifecycle_pos_size = local.get('pos_size', pos_size) if lifecycle_managed else pos_size
                lifecycle_margin = local.get('margin', margin) if lifecycle_managed else margin
                trade = {
                    'symbol':        sym,
                    'direction':     direction,
                    'entry':         entry,
                    'pos_size':      lifecycle_pos_size,
                    'margin':        lifecycle_margin,
                    'leverage':      lev,
                    'current_price': local.get('current_price', entry),
                }
                # Overlay optional metadata that only the bot tracks
                _META_KEYS = (
                    'sl', 'tp', 'tp1', 'score', 'strategy', 'track',
                    'timeframe', 'open_time', 'opened_at', 'scalp',
                    'atr', 'fng_at_entry', 'score_breakdown', 'imported',
                    # Lifecycle/financial state must survive an exchange reconciliation.
                    'phase', 'tp1_triggered', 'tp1_pnl', 'tp1_price',
                    'partial_25_triggered', 'partial_25_pnl',
                    'peak_price', 'trailing_sl', 'be_triggered',
                    'fee_accounting', 'entry_fee_usd', 'fees_paid_usd',
                    'gross_pnl_usd', 'net_pnl_usd', 'fee_rate_pct',
                    'market_regime', 'validation_trial', 'validation_trial_id',
                )
                for k in _META_KEYS:
                    if k in local:
                        trade[k] = local[k]
                rebuilt.append(trade)
                continue

            # Import position with no local record (opened while bot was down)
            rebuilt.append({
                'symbol':        sym,
                'direction':     direction,
                'entry':         entry,
                'sl':            None,
                'tp':            None,
                'tp1':           None,
                'score':         0,
                'strategy':      'imported',
                'pos_size':      pos_size,
                'margin':        margin,
                'leverage':      lev,
                'open_time':     now_il().isoformat(timespec='seconds'),
                'imported':      True,
                'current_price': entry,
            })
            imported.append(sym)

        # ── VIRTUAL / PAPER trading: never drop local trades ─────────────────
        # Bitget returns 0 real positions for paper trades, so "dropped" would
        # wipe all valid virtual positions on every bot restart / deployment.
        # Local state is the source of truth; trades close only via SL/TP/BE/Manual.
        # We only ADD newly-imported positions from the exchange (e.g. manual orders).
        dropped = [s for s in local_by_sym if s not in live]
        if dropped:
            print(
                f"[SYNC] ⚠️  virtual trading — keeping {len(dropped)} local trade(s) "
                f"not found on exchange: {dropped}",
                flush=True,
            )
        # Re-add any local trades that weren't matched to a live position
        for sym in dropped:
            rebuilt.append(local_by_sym[sym])

        active_trades = rebuilt

    save_active_trades()
    print(
        f"[SYNC] reconciled {len(live)} positions from Bitget "
        f"(kept={len(rebuilt) - len(imported)}, imported={len(imported)}, preserved_virtual={len(dropped)})",
        flush=True,
    )
    if imported:
        print(f"[SYNC] imported symbols: {imported}", flush=True)


# Bumping BOOTSTRAP_VERSION forces a one-time reset on next startup.
# v3: Task-38 simplification — clear active trades, carry $13.05 realized P&L forward.
# Stored in Object Storage so each version only ever resets once across all deploys.
BOOTSTRAP_VERSION = "v3_simplification_2026_05_17"


def maybe_bootstrap_baseline():
    """Delegates to database_manager.reset_to_clean_state() — single bootstrap authority."""
    db.reset_to_clean_state()


def main():
    keep_alive()
    maybe_bootstrap_baseline() # ← אם זו הפעלה ראשונה לגרסה הזו — איפוס ל-$200
    load_active_trades()       # ← שחזור עסקאות פעילות (Object Storage → disk)  [MUST be before load_wallet]
    _load_audit_log()          # ← שחזור Audit Log (Object Storage → disk)
    _load_validation_trial()   # ← ניסוי נפרד של 100 עסקאות, נשמר גם אחרי הפעלה מחדש
    reconcile_with_exchange()  # ← בנייה מחדש של active_trades מהעמדות הפתוחות ב-Bitget [MUST be before load_wallet]
    load_wallet()              # ← טעינת ארנק — חייב לאחר reconcile כדי ש-locked יהיה מדויק

    # Thread 1 — Telegram polling
    polling_thread = threading.Thread(target=start_telegram_polling, daemon=True)
    polling_thread.start()

    # Thread 1b — Polling watchdog (restarts polling if it silently freezes)
    watchdog_thread = threading.Thread(target=_polling_watchdog_loop, daemon=True)
    watchdog_thread.name = "PollingWatchdog"
    watchdog_thread.start()

    # Thread 2 — מעקב עסקאות כל 60 שניות
    monitor_thread = threading.Thread(target=trade_monitor_loop, daemon=True)
    monitor_thread.start()

    # Thread 3 — סריקת איתותים כל שעה
    scan_thread = threading.Thread(target=scan_loop, daemon=True)
    scan_thread.start()

    # Thread 4 — Watch List: מעקב מטבעות ספציפיים כל 15 דקות
    watch_thread = threading.Thread(target=watch_loop, daemon=True)
    watch_thread.start()

    # Thread 5 — SOL Watch: מעקב RSI + BB כל 15 דקות, התראה על שינוי
    sol_watch_thread = threading.Thread(target=sol_watch_loop, daemon=True)
    sol_watch_thread.start()

    # Thread 6 — Scalp Scanner: פעיל בלבד כשFNG < 13 (Extreme Fear), כל 5 דקות
    scalp_scan_thread = threading.Thread(target=scalp_scan_loop, daemon=True)
    scalp_scan_thread.start()

    # Thread 7 — Top 10 Breakout: BTC EMA20 + 1H>4H High → LONG אוטומטי, כל 15 דקות
    top10_breakout_thread = threading.Thread(target=top10_breakout_loop, daemon=True)
    top10_breakout_thread.start()

    # Thread 8 — Bubble Watch Active Scanner: מטבעות >10% שינוי → ניתוח מלא → כניסה אם ≥90
    bubble_watch_thread = threading.Thread(target=bubble_watch_scan_loop, daemon=True)
    bubble_watch_thread.start()

    # Thread 9 — High-Velocity: נר 5m >2.5% + Vol >4× → LONG/SHORT מיידי, כל 2 דקות
    cliff_hanger_thread = threading.Thread(target=cliff_hanger_loop, daemon=True)
    cliff_hanger_thread.start()

    # Thread 10 — Major Coins Watch: BTC/ETH/BNB/XRP/SOL/ADA/AVAX/DOGE — כל 15 דקות
    major_watch_thread = threading.Thread(target=major_watch_loop, daemon=True)
    major_watch_thread.start()

    # Thread 11 — Dr. Sniper Research: CoinGecko + TA + DeFiLlama + web, כל 120 דקות
    claude_research.set_send_msg_fn(send_msg)
    claude_research.set_execute_trade_fn(_open_research_trade)
    claude_research.set_context_fns(
        get_open_trades=lambda: len(active_trades),
        get_daily_pnl=lambda: daily_stats.get('total_pnl', 0.0),
        get_fng=lambda: _fng_cache.get('value', 50),
    )
    research_thread = threading.Thread(target=research_loop, daemon=True)
    research_thread.name = "DrSniperResearch"
    research_thread.start()

    if IS_DEPLOYED:
        send_msg(
            "🟢 *SYSTEM READY — Clean Base Rules 2026*\n"
            f"{'─' * 30}\n\n"
            f"💰 *יתרה:* ${wallet.get('balance', STARTING_BALANCE):.2f}\n"
            f"📊 *P&L ממומש:* ${wallet.get('total_pnl', 0):.2f}\n\n"
            "⚙️ *פרמטרים:*\n"
            f"  📌 מרג'ין: *${MARGIN}* | מינוף: *{LEVERAGE}×*\n"
            f"  🎯 מקסימום עסקאות: *{MAX_TRADES}*\n"
            f"  🛑 SL: *{SL_PCT}%* | TP1: *{TP1_PCT}%* \\(75%\\) | TP2: *{TP2_PCT}%*\n"
            "  📍 Trailing: *1\\.5%* מהשיא \\(מופעל ב\\-TP1\\)\n\n"
            "⚡ *לולאות פעילות:*\n"
            "  🔍 סריקה:         כל *60 דקות* ✅\n"
            "  📡 Top20 Breakout: כל *15 דקות* ✅\n"
            "  🫧 Bubble Watch:   כל *15 דקות* ✅\n"
            "  ⚡ High-Velocity:  כל *2 דקות* ✅\n"
            "  📍 מעקב SL/TP:    כל *60 שניות* ✅\n"
            "  🔬 Dr\\. Sniper:    כל *120 דקות* ✅\n\n"
            f"📋 /home — תפריט ראשי\n"
            f"🖥 [פתח דאשבורד]({DASHBOARD_URL})"
        )
    else:
        # DEV: רק הדפסה לקונסול — לא שולחים הודעה לטלגרם כדי לא לבלבל
        print("[DEV] Bot started in Development Mode — no Telegram startup message sent.", flush=True)

    # Thread הראשי נשאר ער
    while True:
        time.sleep(3600)

def _sigterm_handler(signum, frame):
    """סגירה נקייה כשרפליט מכבה את הפרסום — מונע 409 בפרסום הבא."""
    print("[PROD] SIGTERM received — stopping polling and exiting cleanly.", flush=True)
    try:
        bot.stop_polling()
    except Exception:
        pass
    sys.exit(0)

signal.signal(signal.SIGTERM, _sigterm_handler)

if __name__ == "__main__":
    # Early startup marker — written before anything else so we can detect crashes
    import time as _boot_time
    _boot_ts = _boot_time.strftime('%Y-%m-%d %H:%M:%S UTC', _boot_time.gmtime())
    try:
        with open('/tmp/bot_startup.log', 'w') as _sf:
            _sf.write(f"bot.py started at {_boot_ts}\nREPLIT_DEPLOYMENT={os.environ.get('REPLIT_DEPLOYMENT','')}\n")
    except Exception:
        pass
    print(f"[BOT] Starting bot.py at {_boot_ts} — DEPLOYMENT={os.environ.get('REPLIT_DEPLOYMENT','')}", flush=True)

    try:
        main()
    except Exception as _boot_err:
        import traceback as _tb
        _crash_msg = _tb.format_exc()
        print(f"[FATAL] Bot crashed at startup:\n{_crash_msg}", flush=True)
        try:
            with open('/tmp/bot_crash.log', 'w') as _cf:
                _cf.write(f"Crashed at {_boot_ts}\n\n{_crash_msg}")
        except Exception:
            pass
        raise
