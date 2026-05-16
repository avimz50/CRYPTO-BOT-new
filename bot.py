import os
import io
import json
import signal
import sys

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
    get_fear_greed, get_fng_mode,
    sentiment_check, set_sentiment_thresholds,
    score_symbol, detect_fvg, detect_order_blocks,
    detect_flag, detect_bb_squeeze, detect_volume_buildup,
    detect_rsi_divergence, score_candles,
    calc_risk_position, get_dynamic_sl,
    calculate_position_size, check_trade_viability,
    momentum_gate,
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
import state_store  # Replit DB persistence (durable across deploys)
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
print("[BOOT] ccxt exchange OK.", flush=True)

bot = telebot.TeleBot(os.environ['TELEGRAM_TOKEN'])
CHAT_ID = os.environ['CHAT_ID']
print("[BOOT] Telegram bot OK.", flush=True)

# ENERGY_GEO, HOT_CANDIDATES_FILE, ACTIVE_TRADES_FILE, WALLET_FILE, AUDIT_LOG_FILE,
# STARTING_BALANCE, DASHBOARD_URL → config.py (from config import *)

# ─── Fear & Greed Index — cache גלובלי (מתרענן כל שעה) ───────────────────────
_fng_cache = {'value': 50, 'label': 'Neutral', 'ts': 0}

# ─── Kill-Switch State Tracker ─────────────────────────────────────────────────
# None = לא ידוע (הפעלה ראשונה) | True = פעיל | False = כבוי
_kill_switch_active: bool | None = None

# get_fear_greed() → moved to market_logic.py

# ─── Global Sentiment Thresholds — loaded via config.load_fng_settings() ──────
_fng_loaded            = load_fng_settings()   # from config import *
EXTREME_FEAR_THRESHOLD = _fng_loaded['extreme_fear']  # Kill-Switch: אין עסקאות חדשות בכלל
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
# Claude Filter Disabled as Judge — Technical Hunter Mode Active.
# Claude מספק הערת סיכון בלבד. ציון ≥ 60 + BTC BULL = כניסה חובה.

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
            model="claude-3-haiku-20240307",
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


def sniper_claude_check(symbol: str, score: int, direction: str,
                        df_4h, df_1h, vol_ratio: float,
                        price: float, fng_v: int,
                        btc_regime: str) -> tuple[bool, str, str]:
    """
    Sniper Exception — מאמת 4 תנאים לחריגה מ-Kill-Switch ב-Extreme Fear:
      1. ציון ≥ SNIPER_MIN_SCORE (95)
      2. מחיר תוך SNIPER_EMA_PCT (5%) מ-EMA200 בגרף 4H
      3. Volume לפחות SNIPER_VOL_MIN (2.5×) הממוצע
      4. Claude מחזיר STRONG BUY

    מחזיר (ok: bool, verdict: str, reason: str)
    """
    # ── תנאי 1: ציון מינימום ─────────────────────────────────────────────────
    if score < SNIPER_MIN_SCORE:
        return False, "SCORE TOO LOW", f"Score={score} < {SNIPER_MIN_SCORE} required"

    # ── תנאי 2: מרחק EMA200 ≤ 5% ────────────────────────────────────────────
    try:
        ema200_s = ta.ema(df_4h['close'], length=200)
        if ema200_s is None or ema200_s.dropna().empty:
            return False, "EMA200 ERROR", "EMA200 calculation failed"
        ema200 = float(ema200_s.iloc[-1])
        ema_dist = abs(price - ema200) / ema200 * 100
    except Exception as e:
        return False, "EMA200 ERROR", f"EMA200 error: {str(e)[:40]}"

    if ema_dist > SNIPER_EMA_PCT:
        return False, "OVEREXTENDED", f"EMA200 dist {ema_dist:.1f}% > {SNIPER_EMA_PCT}% (chasing pump)"

    # ── תנאי 3: Volume ≥ 2.5× ───────────────────────────────────────────────
    if vol_ratio < SNIPER_VOL_MIN:
        return False, "LOW VOLUME", f"Volume {vol_ratio:.2f}x < {SNIPER_VOL_MIN}x required"

    # ── תנאי 4: Claude → STRONG BUY ─────────────────────────────────────────
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return False, "NO API KEY", "Anthropic key not set — cannot confirm Sniper"

    try:
        rsi_4h = float(ta.rsi(df_4h['close'], length=14).iloc[-1])
        rsi_1h_str = ""
        if df_1h is not None:
            try:
                rsi_1h = float(ta.rsi(df_1h['close'], length=14).iloc[-1])
                rsi_1h_str = f" | RSI 1H: {rsi_1h:.1f}"
            except Exception:
                pass

        prompt = (
            f"SNIPER EXCEPTION — Extreme Fear Market (FNG={fng_v})\n"
            f"This trade bypasses the Kill-Switch (FNG<{SNIPER_MIN_SCORE}) only on STRONG BUY.\n\n"
            f"Symbol: {symbol} | Direction: {direction} | BTC Regime: {btc_regime}\n"
            f"Technical Score: {score}/100 (threshold ≥ {SNIPER_MIN_SCORE})\n"
            f"Price: {price:.6g} | EMA200 (4H): {ema200:.6g} | Distance: {ema_dist:.1f}%\n"
            f"RSI 4H: {rsi_4h:.1f}{rsi_1h_str}\n"
            f"Volume vs avg: {vol_ratio:.2f}x (threshold ≥ {SNIPER_VOL_MIN}x)\n\n"
            f"RESPOND WITH EXACTLY ONE LINE — choose one:\n"
            f"STRONG BUY — genuinely exceptional setup, all technicals align, safe to trade in Extreme Fear\n"
            f"APPROVE — setup is good but not exceptional enough to override Extreme Fear Kill-Switch\n"
            f"REJECT — do NOT trade this in Extreme Fear conditions\n\n"
            f"Criteria for STRONG BUY (ALL must be true):\n"
            f"- Price near EMA200 (confirmed dynamic support, not a pump)\n"
            f"- RSI not overbought (LONG: RSI<60, SHORT: RSI>40)\n"
            f"- Volume surge confirms real institutional participation\n"
            f"- Score breakdown shows trend + momentum + volume alignment\n"
            f"Be EXTREMELY conservative — Extreme Fear means systemic risk. "
            f"STRONG BUY is reserved for once-in-a-cycle setups only."
        )

        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=40,
            messages=[{"role": "user", "content": prompt}]
        )
        verdict_raw = resp.content[0].text.strip().upper()
        print(f"  [Sniper] Claude raw: {verdict_raw}")

        if verdict_raw.startswith("STRONG BUY"):
            reason = f"Score={score}, EMA dist={ema_dist:.1f}%, Vol={vol_ratio:.2f}x"
            return True, "STRONG BUY", reason
        elif verdict_raw.startswith("APPROVE"):
            return False, "APPROVE (not STRONG BUY)", "Claude approved but not exceptional enough for Kill-Switch bypass"
        else:
            return False, "REJECT", "Claude rejected — not suitable for Extreme Fear"

    except Exception as e:
        print(f"  [Sniper] Claude error: {e} — conservative fallback: REJECT")
        return False, "ERROR", f"Claude error — conservative REJECT: {str(e)[:50]}"


# IS_DEPLOYED / GEMINI_URL / GEMINI_KEY → config.py (from config import *)

# LEVERAGE / MARGIN / POSITION_SIZE → config.py (from config import *)

# רשימה למעקב אחרי עסקאות דמו פתוחות
active_trades      = []
trades_lock        = threading.RLock()   # מגן מ-race conditions בין Threads

# לוג עסקאות סגורות (48 שעות אחרונות) לדוח ה-Drive
closed_trades_log  = []

# Audit Log — מתמיד ל-trade_audit.json (100 עסקאות אחרונות)
trade_audit_log: list[dict] = []

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
    'date':         date.today(),
    'close_reasons': {'TP': 0, 'TP1+Trail': 0, 'Trailing': 0, 'SL': 0, 'BE': 0, 'Manual': 0},
}

# שמירת תאריך הדוח האחרון שנשלח
last_daily_report_date = None

# SCAN_REPORT_FILE → config.py (from config import *)

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
    Sandbox Mode — ניתוח חינוכי בלבד כשה-Kill-Switch פעיל.
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
                f"NO trades are being opened (Kill-Switch ACTIVE, FNG={fng_v}).\n\n"
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
                f"Line 1: APPROVE or REJECT (single word — would you trade this if Kill-Switch was OFF?)\n"
                f"Line 2: 1-sentence analysis of the 3-TF RSI alignment and whether the move is overextended\n"
                f"Line 3: ENTRY_ZONE: [low_price] - [high_price]  "
                f"(the ideal buy/short zone using EMA200 or Fib levels; use the pre-calculated values above)\n"
                f"Line 4: ENTRY_REASON: [one phrase, e.g. 'EMA200 4H retest' or 'Fib 0.618 pullback']\n"
                f"Line 5: RSI_WAIT: [exact RSI condition before entering, e.g. 'Wait for RSI 15m to drop below 45']\n\n"
                f"Use ONLY the 5 lines above. No extra text."
            )

            client   = _anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-3-haiku-20240307",
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

    # Sentiment impact sentence — Hunter Mode: BTC Regime is the master, FNG is informational only
    btc_r_now = get_btc_regime()
    if fng_value < 20 and btc_r_now == 'BULL':
        sentiment_note = f"Fear & Greed={fng_value} (Extreme Fear) — ⚡ BTC BULL Compass: Kill-Switch מבוטל! סורק בחופשיות"
    elif fng_value < 20:
        sentiment_note = f"Fear & Greed={fng_value} (Extreme Fear) — BTC BEAR: Kill-Switch פעיל (ממתין לסיגנל BTC)"
    elif fng_value <= 30:
        sentiment_note = f"Fear & Greed={fng_value} (Fear) — Hunter Mode: RSI וSL רגילים (לא מוגבל)"
    elif fng_value >= 75:
        sentiment_note = f"Fear & Greed={fng_value} (Extreme Greed) — Greed: פוזיציה צומצמה ל-60%"
    elif fng_value >= 60:
        sentiment_note = f"Fear & Greed={fng_value} (Greed) — זהירות קלה"
    else:
        sentiment_note = f"Fear & Greed={fng_value} ({fng_label}) — מצב ניטרלי"

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
        snapshot = list(active_trades)
    return flask_jsonify({
        'updated': now_il().strftime('%H:%M:%S'),
        'count':   len(snapshot),
        'trades':  snapshot,
    })

@flask_app.route('/api/wallet')
def api_wallet():
    data = dict(wallet)
    unrealized = _unrealized_cached()
    # Post-restart fallback: if trades exist but none have a fresh current_price
    # yet, serve the last-persisted unrealized P&L from wallet.json instead of 0.
    _no_prices = active_trades and all('current_price' not in t for t in active_trades)
    if unrealized == 0 and _no_prices:
        unrealized = wallet.get('unrealized_pnl', 0.0)
    data['equity']           = _equity_cached()
    data['available_balance'] = round(wallet.get('balance', STARTING_BALANCE), 2)
    data['locked_balance']   = round(sum(t.get('margin', MARGIN) for t in active_trades), 2)
    data['unrealized_pnl']   = unrealized
    data['active_count']     = len(active_trades)
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

@flask_app.route('/api/active_trades')
def api_active_trades():
    """Alias for /api/trades — used by the dashboard in production."""
    return api_trades()

@flask_app.route('/api/status')
def api_status():
    """Aggregate status snapshot — combines wallet + FNG + active-trade count.
    /api/fng and /api/last_scan are already defined in keep_alive.py (same Flask app).
    Uses cached equity (no live API call) — fast response for dashboard polling.
    """
    from keep_alive import _fng_ka
    fng_v   = _fng_ka.get('value') or 50
    fng_lbl = _fng_ka.get('label') or 'Neutral'
    return flask_jsonify({
        'connected':     True,
        'exchange':      'Bitget VIRTUAL',
        'equity':        _equity_cached(),
        'starting':      STARTING_BALANCE,
        'fng_value':     fng_v,
        'fng_label':     fng_lbl,
        'active_trades': len(active_trades),
        'max_trades':    MAX_TRADES,
        'updated':       now_il().strftime('%H:%M:%S'),
    })

@flask_app.route('/api/sync', methods=['POST'])
def api_sync():
    """Dashboard SYNC button — שולח /status לטלגרם. ללא אימות (internal only).
    NOTE: handle_status runs in a background thread to avoid blocking the Flask server.
    _get_unrealized_pnl() is called INSIDE the thread, not in the request handler.
    """
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
            'extreme_fear': {'min': 5,  'max': 25, 'desc': 'Kill-Switch — אין עסקאות חדשות'},
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
            price = exchange.fetch_ticker(symbol)['last']
            close_trade(trade, reason='Manual', close_price=price)
            send_msg(f"✋ *Make סגר עסקה* — `{symbol}` @ `{price:.6g}`")
            print(f"[Make→Bot] close_trade: {symbol} @ {price}")
            return flask_jsonify({'ok': True, 'action': 'trade_closed', 'price': price})
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

@flask_app.route('/api/tg_hook', methods=['POST'])
def api_tg_hook():
    """Telegram webhook endpoint — used in production instead of polling."""
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
    global MAX_TRADES
    data = flask_request.get_json(force=True, silent=True) or {}
    try:
        v = int(data.get('max_trades', MAX_TRADES))
    except (ValueError, TypeError):
        return flask_jsonify({'ok': False, 'error': 'max_trades חייב להיות מספר שלם 1–5'}), 400
    if not (1 <= v <= 5):
        return flask_jsonify({'ok': False, 'error': f'max_trades חייב להיות 1–5 (קיבלתי {v})'}), 400
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

def send_msg(text):
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
        if t.get('direction') == 'LONG':
            total += (curr - entry) / entry * pos
        else:
            total += (entry - curr) / entry * pos
    return round(total, 2)

def _get_equity():
    """Total equity = available_balance + locked_margin + unrealized_pnl."""
    locked     = sum(t.get('margin', MARGIN) for t in active_trades)
    unrealized = _get_unrealized_pnl()
    return round(wallet.get('balance', STARTING_BALANCE) + locked + unrealized, 2)

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
        if t.get('direction') == 'LONG':
            total += (curr - entry) / entry * pos
        else:
            total += (entry - curr) / entry * pos
    return round(total, 2)

def _equity_cached() -> float:
    """Equity מהיר מ-cache — ללא קריאת API.
    If no trade has a fresh current_price yet (e.g. immediately after a bot
    restart), fall back to the last value persisted in wallet.json so the
    dashboard never shows a stale/zero equity during the first 60-second gap."""
    locked     = sum(t.get('margin', MARGIN) for t in active_trades)
    unrealized = _unrealized_cached()
    # Detect the post-restart window: active trades exist but none have been
    # priced yet (current_price absent → _unrealized_cached returns 0).
    if unrealized == 0 and active_trades and all('current_price' not in t for t in active_trades):
        return round(wallet.get('equity', wallet.get('balance', STARTING_BALANCE) + locked), 2)
    return round(wallet.get('balance', STARTING_BALANCE) + locked + unrealized, 2)

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
        'trades_opened':  0,
        'equity_history': [{'t': now_il().strftime('%m/%d %H:%M'), 'eq': STARTING_BALANCE}],
    }
    wallet = state_store.load_state('wallet', WALLET_FILE, default)
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
    try:
        locked = sum(t.get('margin', MARGIN) for t in active_trades)
        snapshot = {
            **wallet,
            'locked_balance':    round(locked, 2),
            'available_balance': round(wallet.get('balance', STARTING_BALANCE), 2),
        }
        state_store.save_state('wallet', snapshot, WALLET_FILE)
    except Exception as e:
        print(f"Wallet save error: {e}", flush=True)

def wallet_deduct(amount: float = MARGIN):
    """קיזוז מרג'ין בפתיחת עסקה. amount=MARGIN רגיל, MARGIN*0.5 ל-Sniper."""
    wallet['balance']       = round(wallet.get('balance', STARTING_BALANCE) - amount, 2)
    wallet['trades_opened'] = wallet.get('trades_opened', 0) + 1
    _append_equity_point()
    save_wallet()

def wallet_credit(pnl_usd: float, amount: float = MARGIN):
    """זיכוי מרג'ין + P&L בסגירת עסקה. amount צריך להתאים ל-wallet_deduct."""
    wallet['balance']   = round(wallet.get('balance', STARTING_BALANCE) + amount + pnl_usd, 2)
    wallet['total_pnl'] = round(wallet.get('total_pnl', 0.0) + pnl_usd, 2)
    if pnl_usd >= 0:
        wallet['total_wins']   = wallet.get('total_wins', 0) + 1
    else:
        wallet['total_losses'] = wallet.get('total_losses', 0) + 1
    _append_equity_point()
    save_wallet()


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
def place_order(trade: dict, margin: float = MARGIN) -> bool:
    """
    רושם עסקה חדשה:
      • בדיקת כפילות גלובלית — One Trade Per Symbol (חסין לכל סקאנר)
      • מוסיף ל-active_trades (עם trades_lock)
      • מנכה מרג'ין מהארנק (wallet_deduct)
      • שומר active_trades ל-disk (save_active_trades)
    מחזיר True בהצלחה, False אם נחסם (כפילות / יתרה).
    """
    symbol = trade.get('symbol', '')

    # ══ GLOBAL GUARDS — אטומי תחת trades_lock ══════════════════════════
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
        # ✅ Passed all guards — add to list
        active_trades.append(trade)
    # ═══════════════════════════════════════════════════════════════════
    wallet_deduct(margin)
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


def _log_closed_trade(trade: dict, close_reason: str, pnl_usd: float, close_price: float = None):
    """מוסיף עסקה סגורה ל-closed_trades_log + Audit Log מתמיד."""
    global closed_trades_log, trade_audit_log
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
        'pnl_usd':         round(pnl_usd, 2),
        'opened_at':       trade.get('opened_at', ''),
        'closed_at':       closed_at,
        # ── Entry Context (Audit) ────────────────────────────────────────
        'fng_at_entry':    trade.get('fng_at_entry'),
        'atr':             trade.get('atr', 0.0),
        'duration_min':    duration_m,
        'sniper':          trade.get('sniper', False),
        'scalp':           trade.get('scalp', False),
        'hunter_mode':     trade.get('hunter_mode', False),
        # ── Auto-generated lesson ────────────────────────────────────────
        'lesson': _generate_lesson(
            close_reason, pnl_usd, duration_m,
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
    שלב 1: שליפת כל זוגות USDT מ-Bitget (ווליום $1M+)
    שלב 2: Top 15 Gainers (LONG) + Top 15 Losers (SHORT)
    מחזיר: (gainers_list, losers_list)
    """
    try:
        if VERBOSE_LOG:
            print("[Scan] Fetching all tickers for hot candidates...")
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
        print(f"Hot candidates error: {e}")
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
        if VERBOSE_LOG:
            print(f"[BTC] Regime={regime} price={price:.0f} EMA50={ema50:.0f} ({pct:+.2f}%)")
        return regime
    except Exception as e:
        print(f"BTC regime check failed: {e} — defaulting to NEUTRAL")
        return 'NEUTRAL'


# Cache ל-BTC Parabolic Bull check (15 דקות TTL)
_btc_parabolic_cache: dict = {'ts': 0.0, 'result': False, 'rsi': 0.0, 'ema': 0.0}

def is_btc_parabolic_bull() -> tuple[bool, float, float]:
    """
    האם BTC נמצא בעלייה פרבולית?
    תנאי: BTC 4H מעל EMA200 AND BTC RSI(1H) > 60
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
        result   = (price_4h > ema200) and (rsi_1h > 60)
        _btc_parabolic_cache = {'ts': now_ts, 'result': result, 'rsi': rsi_1h, 'ema': ema200}
        if result:
            print(f"[BTC Compass] 🐂 PARABOLIC BULL — {price_4h:.0f} > EMA200={ema200:.0f} + RSI1H={rsi_1h:.1f}>60 → SHORTs חסומים")
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
            print(f"[BTC Trend] 🟢 STRONG UPTREND — ${price:,.0f} > EMA200_1H={ema200_1h:,.0f} & EMA200_4H={ema200_4h:,.0f} → SHORTs מוגבלים (Score>{STRONG_UPTREND_SHORT_MIN_SCORE})")
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
    """P&L ($) על חצי פוזיציה ($250) לפי % מרחק מהכניסה."""
    return round(POSITION_SIZE / 2 * dist_pct / 100, 2)


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


def is_hunter_mode(fng_v: int, change_24h: float = 0.0) -> tuple:
    """
    🎯 Precision Hunter — מזהה מתח שוק גבוה:
      • FNG ≥ 70 (Greed/Extreme Greed)
      • OR נכס עלה/ירד >15% ב-24h
    מחזיר: (is_hunter: bool, reason: str)
    """
    if fng_v >= HUNTER_FNG_THRESHOLD:
        return True, f"FNG={fng_v} ≥ {HUNTER_FNG_THRESHOLD} (Greed)"
    if abs(change_24h) >= HUNTER_PUMP_PCT_24H:
        sign = "⬆️" if change_24h > 0 else "⬇️"
        return True, f"24h שינוי {sign}{abs(change_24h):.1f}% > {HUNTER_PUMP_PCT_24H}%"
    return False, ""


# calc_risk_position, get_dynamic_sl → moved to market_logic.py


def track_badge(track: str) -> str:
    """אמוג'י + תווית מסלול לטלגרם."""
    return "⚡ Scalp" if track == 'Scalp' else "🌊 Swing"


def open_demo_trade(symbol, price, reason, df_3h=None,
                    direction='LONG', score=0, atr=0, timeframe='4H', tf_reason='',
                    rsi=None, ema200=None, fng_v=None, sniper_mode=False,
                    ob_found=None, ob_high=None, ob_low=None, df_ob=None,
                    df_1h=None):
    """
    פותח עסקת דמו — 🌊 מסלול Swing.
    ATR-based targets (1H): SL = 2×ATR | TP1 = 1.5×ATR (→ BE auto) | TP2 = 3×ATR
    Dynamic position sizing: risk TRADE_RISK_PCT% of balance per trade.
    timeframe: '4H' / '1H' — גרף הכניסה שנבחר אדפטיבית
    fng_v: ערך FNG שכבר חושב ב-scan (כדי לא לשאול שוב)
    sniper_mode: True → Half-Size Entry (50% מגודל הפוזיציה הרגיל)
    ob_found/ob_high/ob_low: pre-computed OB zone (pass from scan to align with scoring df)
    df_ob: fallback df for OB detection if ob_found not pre-supplied (uses df_3h if None)
    df_1h: 1H OHLCV dataframe used to compute ATR-based targets
    """
    track = 'Swing'

    # ── נפח + שינוי 24h (קריאה אחת) ─────────────────────────────────────────
    vol_usd, change_24h = fetch_symbol_ticker_info(symbol)
    if vol_usd > 0 and vol_usd < SWING_TRACK_VOL_MIN:
        print(f"[SWING] נפח נמוך עבור {symbol}: ${vol_usd/1e6:.1f}M < $10M — מדלג")
        send_msg(
            f"⚠️ *נפח נמוך מדי — {symbol.replace('/USDT','')}*\n"
            f"נפח 24h: ${vol_usd/1e6:.1f}M | מינימום Swing: ${SWING_TRACK_VOL_MIN/1e6:.0f}M\n"
            f"_העסקה נדחתה — נזילות לא מספקת_"
        )
        return

    # ── FNG + גודל פוזיציה לפי 1.5% סיכון ─────────────────────────────────
    if fng_v is None:
        fng_v, _, _ = sentiment_check("open_trade")

    # ── 🎯 Hunter Mode Detection ──────────────────────────────────────────────
    hunter, hunter_reason = is_hunter_mode(fng_v, change_24h)
    if hunter:
        send_msg(
            f"🎯 *Hunter Mode פעיל — {symbol.replace('/USDT','')}*\n"
            f"⚠️ מתח שוק גבוה: _{hunter_reason}_\n"
            f"עובר למצב Precision Hunter — מקבל רק עסקאות עם RR 1:3 ומעלה."
        )
        print(f"  [HUNTER MODE] {symbol}: {hunter_reason} → RR min={HUNTER_MIN_RR}")

    equity       = _get_equity()
    balance_snap = wallet.get('balance', STARTING_BALANCE)

    # ── Get SL/TP percentages from FNG Mode (ignore old margin/pos/lev values) ─
    _, _, _, sl_pct, tp1_pct, tp_pct = calc_risk_position('Swing', equity, fng_v=fng_v)

    # ── SL דינמי — לפי סוג נכס / FNG / ATR ──────────────────────────────────
    dyn_sl = get_dynamic_sl(symbol, price, atr, fng_v)
    if dyn_sl != sl_pct:
        # TP ו-TP1 מתכוונן יחסית לשינוי ב-SL כדי לשמור RR
        ratio = dyn_sl / sl_pct if sl_pct > 0 else 1.0
        tp1_pct = round(tp1_pct * ratio, 2)
        tp_pct  = round(tp_pct  * ratio, 2)
        sl_pct  = dyn_sl

    # FNG Mode — min_rr דינמי (BE נחשב מ-TP1 עכשיו)
    fng_mode = get_fng_mode(fng_v)
    print(f"  [FNG MODE] {fng_mode['emoji']} {fng_mode['name']} (FNG={fng_v}) "
          f"| SL={sl_pct}% TP1={tp1_pct}% TP={tp_pct}% | BE@50%→TP1")

    # ── ATR-Based Dynamic Targets ────────────────────────────────────────────
    # SL  = ATR_SL_MULT  × ATR(1H)   — slow timeframe → stable stop
    # TP1 = ATR_TP1_MULT × ATR(15m)  — fast timeframe → quicker partial + BE
    # TP2 = SL_dist × TARGET_RR_RATIO (RR-based final target, default 1:2)
    atr_1h = 0.0
    if df_1h is not None and USE_ATR_TARGETS:
        try:
            _atr_s = ta.atr(df_1h['high'], df_1h['low'], df_1h['close'], length=ATR_PERIOD)
            if _atr_s is not None and len(_atr_s.dropna()) > 0:
                _atr_v = float(_atr_s.dropna().iloc[-1])
                if _atr_v > 0:
                    atr_1h = _atr_v

                    # Fetch 15m ATR for the faster TP1 trigger
                    atr_15m = 0.0
                    try:
                        df_15m = get_data(symbol, timeframe=ATR_TP1_TF, limit=ATR_PERIOD * 4)
                        _atr15_s = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=ATR_PERIOD)
                        if _atr15_s is not None and len(_atr15_s.dropna()) > 0:
                            atr_15m = float(_atr15_s.dropna().iloc[-1])
                    except Exception as _atr15_e:
                        print(f"  [ATR Targets] 15m ATR fetch failed, falling back to 1H for TP1: {_atr15_e}")
                    if atr_15m <= 0:
                        atr_15m = atr_1h  # safe fallback

                    sl_dist  = ATR_SL_MULT  * atr_1h
                    tp1_dist = ATR_TP1_MULT * atr_15m       # TP1 = 1.0 × ATR(15m) → BE trigger + partial close
                    tp_dist  = sl_dist * TARGET_RR_RATIO    # TP2 at RR (default 1:2)
                    sl_pct   = round(sl_dist  / price * 100, 4)
                    tp1_pct  = round(tp1_dist / price * 100, 4)
                    tp_pct   = round(tp_dist  / price * 100, 4)
                    tp1_rr   = (tp1_dist / sl_dist) if sl_dist > 0 else 0
                    print(f"  [ATR Targets] 1H ATR={atr_1h:.6g} 15m ATR={atr_15m:.6g} | "
                          f"SL={sl_pct:.2f}% TP1={tp1_pct:.2f}%(1:{tp1_rr:.2f} • {ATR_TP1_MULT}×ATR15m) "
                          f"TP2={tp_pct:.2f}%(1:{TARGET_RR_RATIO:.0f})")
        except Exception as _atr_e:
            print(f"  [ATR Targets] fallback to FNG%: {_atr_e}")

    # Hunter Mode — RR multiples only when ATR is unavailable
    if hunter and atr_1h == 0.0:
        tp1_pct = round(sl_pct * HUNTER_TP1_RR, 2)
        tp_pct  = round(sl_pct * HUNTER_MIN_RR, 2)
        print(f"  [HUNTER] Swing TP1={tp1_pct}% TP={tp_pct}% (SL={sl_pct}%)")

    # ── SL / TP / BE price levels ─────────────────────────────────────────────
    if atr_1h == 0.0:                # fallback: compute distances from %
        sl_dist  = price * sl_pct  / 100
        tp_dist  = price * tp_pct  / 100
        tp1_dist = price * tp1_pct / 100
    # Feature 4: Auto BE at 50% of the way to TP1 (risk-free trade when price moves halfway)
    be_dist = tp1_dist * 0.50

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

    # ── Dynamic Position Sizing — fixed $RISK_PER_TRADE_USD at risk on actual SL ─
    pos_size, effective_margin, leverage, risk_usd = calculate_position_size(
        balance=balance_snap,
        risk_pct=TRADE_RISK_PCT,
        entry_price=price,
        stop_loss_price=sl_price,
        margin_cap=MARGIN,
        risk_usd_override=RISK_PER_TRADE_USD,
        min_pos_size=MIN_POSITION_USD,
    )
    print(f"  [RiskSizing] balance=${balance_snap:.2f} | risk_fixed=${RISK_PER_TRADE_USD:.2f} | "
          f"pos=${pos_size:.2f} | margin=${effective_margin:.2f} | {leverage}x | SL={sl_pct:.2f}%")

    # Size adjustments: Sniper (half-size) and Sentiment (60% in greed)
    if sniper_mode:
        pos_size         = round(pos_size         * SNIPER_MARGIN_MULT, 2)
        effective_margin = round(effective_margin  * SNIPER_MARGIN_MULT, 2)
        risk_usd         = round(risk_usd          * SNIPER_MARGIN_MULT, 2)
        print(f"  [SNIPER] Half-size → pos=${pos_size:.2f} margin=${effective_margin:.2f}")

    if fng_v >= GREED_THRESHOLD and not sniper_mode:
        pos_size         = round(pos_size * 0.60, 2)
        effective_margin = round(pos_size / leverage, 2)
        risk_usd         = round(risk_usd * 0.60, 2)
        print(f"  [SENTIMENT] GREED ({fng_v}) → Swing פוזיציה צומצמה ל-${pos_size:.2f}")

    # ── Wallet balance check ──────────────────────────────────────────────────
    if balance_snap < effective_margin:
        print(f"WALLET: insufficient balance (${balance_snap:.2f}) — skipping {symbol}")
        send_msg(f"⚠️ *יתרה נמוכה* — אין מספיק להפקדת מרג'ין\nנדרש: ${effective_margin:.0f} | יש: ${balance_snap:.2f}")
        return

    # ── Profitability Gate: Net Profit at final TP after round-trip fees ────────
    # With dynamic RR (TP1=1:1, TP2=TARGET_RR_RATIO), check viability against TP2
    trade_ok, net_profit_usd, rr_ratio, veto_reason = check_trade_viability(
        pos_size=pos_size,
        entry_price=price,
        tp1_price=tp_price,   # Check against final TP (1:2 RR), not TP1 (1:1)
        sl_price=sl_price,
        direction=direction,
        min_net_profit=MIN_NET_PROFIT_USD,
        fee_pct=ROUND_TRIP_FEE_PCT,
        min_rr=MIN_PROFIT_RR,
    )
    if not trade_ok:
        print(f"[VIABILITY] ❌ {symbol} {direction} — {veto_reason}")
        send_msg(
            f"⚠️ *Trade Rejected — {symbol.replace('/USDT','')}*\n\n"
            f"🚫 {veto_reason}\n"
            f"Pos=${pos_size:.0f} | SL={sl_pct:.1f}% | TP={tp_pct:.1f}%(1:{TARGET_RR_RATIO:.0f})"
        )
        return

    # ── Hunter / FNG full-TP RR check (hard guard on final TP, not TP1) ──────
    est_profit_tp  = round(abs(tp_price  - price) / price * pos_size, 2)
    est_profit_tp1 = round(abs(tp1_price - price) / price * pos_size, 2)
    est_loss_sl    = round(abs(sl_price  - price) / price * pos_size, 2)
    rr_full       = round(est_profit_tp / est_loss_sl, 2) if est_loss_sl > 0 else 0
    required_rr   = HUNTER_MIN_RR if hunter else fng_mode['min_rr']

    if rr_full < required_rr:
        _rr_mode_label = '(Hunter)' if hunter else f'({fng_mode["name"]})'
        print(f"[SWING] RR_full={rr_full:.2f} < {required_rr} {_rr_mode_label} — {symbol} נדחה")
        if hunter:
            send_msg(
                f"❌ *Trade Rejected: {symbol.replace('/USDT','')}*\n"
                f"Full-TP RR: 1:{rr_full} | Min (Hunter): 1:{int(HUNTER_MIN_RR)}\n"
                f"🎯 _Hunter Mode Active — Precision entries only_"
            )
        else:
            send_msg(
                f"⚠️ *RR נמוך — {symbol.replace('/USDT','')}*\n"
                f"RR: {rr_full:.2f} | מינימום: {required_rr:.0f}\n"
                f"_העסקה נדחתה — יחס סיכון/תשואה לא מספיק_"
            )
        return

    max_risk_usd = round(risk_usd, 2)
    risk_pct_eq  = round(risk_usd / equity * 100, 2)

    # Hunter Mode: TP1 hit → auto-BE (flag stored in trade)
    hunter_be_on_tp1 = hunter   # בעסקות Hunter, TP1 מפעיל BE אוטומטית

    # ── Order Block — use pre-supplied values if available, else detect ───────
    # Pre-supplied values (ob_found not None) come from the scan site using df_1h,
    # matching the same dataframe that score_symbol used — so the zone shown on
    # the dashboard is exactly the one that contributed +3 to the score.
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
        'symbol':          symbol,
        'entry':           price,
        'sl':              sl_price,
        'tp':              tp_price,
        'tp1':             tp1_price,
        'be_lvl':          be_price,
        'sl_pct':          sl_pct,
        'tp_pct':          tp_pct,
        'direction':            direction,
        'phase':                'initial',
        'be_triggered':         False,
        'tp1_triggered':        False,
        'partial_25_triggered': False,
        'tp1_pnl':              0.0,
        'peak_price':           price,
        'trailing_sl':          None,
        'score':                score,
        'atr':                  round(atr, 6),
        'atr_1h':               round(atr_1h, 8),
        'timeframe':       timeframe,
        'rsi':             round(rsi, 2) if rsi is not None else None,
        'ema200':          round(ema200, 6) if ema200 is not None else None,
        'score_breakdown': reason,
        'opened_at':       now_il().isoformat(timespec='seconds'),
        'pos_size':        pos_size,
        'margin':          effective_margin,
        'leverage':        leverage,
        'fng_at_entry':    fng_v,
        'sniper':          sniper_mode,
        'track':           track,            # 🌊 Swing
        'vol_usd':         round(vol_usd),
        'change_24h':      round(change_24h, 2),
        'slippage_pct':    0.0,
        'hunter_mode':     hunter,           # 🎯 Precision Hunter
        'hunter_be_on_tp1': hunter_be_on_tp1,
        'ob_found':        ob_found,
        'ob_high':         ob_high,
        'ob_low':          ob_low,
        'ob_type':         ('Bullish' if direction == 'LONG' else 'Bearish') if ob_found else None,
    }
    # ── Risk/Reward Summary Log — printed before every opened trade ──────────
    print(f"  [Trade] Risking ${risk_usd:.2f} to make ${net_profit_usd:.2f}. "
          f"Expected Net Profit: ${net_profit_usd:.2f}. RR (TP1): 1:{rr_ratio:.2f}")
    place_order(trade, effective_margin)

    dir_header = get_direction_header(direction)
    tip        = get_momentum_tip(direction)
    emoji      = "🟢" if direction == 'LONG' else "🔴"
    score_bar  = "█" * (score // 10) + "░" * (10 - score // 10)

    if not tf_reason:
        tf_reason = f"טרנד חזק ב-{timeframe}" if timeframe == '4H' else f"פריצה ב-{timeframe} (4H חלש)"
    tf_icon = "📊" if timeframe == '4H' else ("⏱️" if timeframe == '1H' else "⚡")

    hunter_tag = "🎯 *PRECISION HUNTER MODE* | " if hunter else ""
    rr_label   = f"RR 1:{int(HUNTER_MIN_RR)}" if hunter else f"RR 1:{rr_ratio}"
    be_note    = " ← TP1 מפעיל BE אוטומטי!" if hunter else " ← אחרי מבנה ברור"
    mode_tag   = f"{fng_mode['emoji']} *Mode: {fng_mode['name']}* (FNG={fng_v})"

    ticker_base  = symbol.split('/')[0].upper()
    symbol_spaced = " ".join(list(ticker_base))
    dir_icon     = "🟢 L O N G" if direction == 'LONG' else "🔴 S H O R T"
    sniper_tag   = "🎯 Sniper · " if sniper_mode else ""
    hunter_line  = f"⚠️ _{hunter_reason}_\n" if hunter else ""
    free_cash    = round(wallet.get('balance', 0) - effective_margin, 2)
    asset_class  = "Major" if ticker_base in MAJOR_COINS else "Altcoin"

    atr_tag = f" | 📐 ATR={atr_1h:.4g}" if atr_1h > 0 else ""
    msg = (
        f"*{dir_icon}  |  {symbol_spaced}*\n"
        f"{hunter_line}"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 *ניקוד:* `{score}/100` | {sniper_tag}{fng_mode['emoji']} {fng_mode['name']} | {asset_class}{atr_tag}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 כניסה:  `{price:.6g}`\n"
        f"🛑 SL:     `{sl_price:.6g}` (-{sl_pct:.2f}%)\n"
        f"🎯 TP1:    `{tp1_price:.6g}` (+{tp1_pct:.2f}%) → 🔒 BE auto\n"
        f"🎯 TP2:    `{tp_price:.6g}` (+{tp_pct:.2f}%)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ סיכון: `${est_loss_sl}` | 💰 רווח(TP1): `${est_profit_tp1}` | (TP2): `${est_profit_tp}`\n"
        f"💵 פנוי בארנק: `${free_cash:.2f}`"
    )

    # אם df_3h לא סופק — שולפים 1H בעצמנו (למשל Major Watch מעביר None)
    # limit=250 כדי ש-EMA200 יהיה מחושב נכון (צריך לפחות 200 נרות)
    if df_3h is None:
        try:
            df_3h = get_data(symbol, timeframe='1h', limit=250)
        except Exception as _fe:
            print(f"[SWING] chart fetch fallback failed: {_fe}")
            df_3h = None

    chart_buf = generate_chart(df_3h, symbol, price, sl_price, tp_price, direction) \
                if df_3h is not None else None
    send_chart_alert(chart_buf, symbol, msg)
    print(f"[SWING] Trade opened: {symbol} {direction} @ {price:.6g} | SL={sl_pct}% TP={tp_pct}% | "
          f"{leverage}x | margin=${effective_margin:.2f} | Risk=${max_risk_usd:.2f} ({risk_pct_eq:.2f}%) | "
          f"NetProfit@TP1=${net_profit_usd:.2f}")


def track_trades():
    """
    בודק כל עסקה פעילה כל 60 שניות.
    תומך ב-LONG וב-SHORT.
    """
    global active_trades, daily_stats, _daily_circuit_notified

    if daily_stats['date'] != date.today():
        daily_stats = {
            'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'date': date.today(),
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

    for trade in active_trades[:]:
        try:
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
            half          = pos_size / 2                          # חצי פוזיציה
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
                scalp_pos   = trade.get('pos_size', SCALP_POS_SIZE)   # גודל מהעסקה
                scalp_mar   = trade.get('margin',   SCALP_MARGIN)     # מרג'ין מהעסקה
                scalp_lev   = trade.get('leverage', SCALP_LEVERAGE)
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

                # ── Scalp Hunter Mode: TP1 → auto-BE + go trailing ───────────
                if trade.get('hunter_be_on_tp1') and not trade.get('tp1_triggered'):
                    tp1_hit_s = ((direction == 'LONG' and current_price >= trade.get('tp1', float('inf'))) or
                                 (direction == 'SHORT' and current_price <= trade.get('tp1', 0)))
                    if tp1_hit_s:
                        half_pnl    = round(scalp_pos / 2 * (current_price - entry) / entry, 2) if direction == 'LONG' \
                                      else round(scalp_pos / 2 * (entry - current_price) / entry, 2)
                        trade['tp1_triggered'] = True
                        trade['tp1_pnl']       = half_pnl
                        trade['sl']            = entry        # BE immediata
                        trade['be_triggered']  = True
                        daily_stats['total_pnl'] += half_pnl
                        send_msg(
                            f"🎯 *Scalp TP1 הושג — {sym.replace('/USDT','')}!* [Hunter Mode]\n"
                            f"מחיר: `{current_price:.6g}` | {direction} | {tbadge}\n"
                            f"50% נסגרו · ✅ *Profit at TP1: +${half_pnl}*\n"
                            f"🔒 *SL הועבר ל-BE אוטומטית!* `{entry:.6g}`\n"
                            f"🎯 _Precision Hunter: שאר 50% ממשיכים ל-TP 1:3_\n"
                            f"📈 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
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
                    daily_stats['total_pnl'] += scalp_pnl_usd
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
                    daily_stats['total_pnl'] += cliff_pnl_usd
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

                # ── 0a. STAGNATION EXIT — אם תזת המומנטום לא התממשה ב-4 שעות ──────
                # רלוונטי רק ל-Sniper/Breakout/SOL (לא Scalp/Cliff שיש להם timeout משלהם)
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
                            daily_stats['total_pnl'] += stag_pnl
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
                    pnl_usd  = round(sign * pos_size * dist_pct / 100, 2)
                    pnl_pct_r = round(sign * dist_pct * LEVERAGE, 1)
                    icon     = "📈" if pnl_usd >= 0 else "📉"
                    if pnl_usd >= 0:
                        daily_stats['wins']  += 1
                    else:
                        daily_stats['losses'] += 1
                    daily_stats['total_pnl'] += pnl_usd
                    daily_stats['close_reasons']['Trailing'] += 1
                    wallet_credit(pnl_usd, trade.get('margin', MARGIN))
                    _log_closed_trade(trade, 'Trailing', pnl_usd, current_price)
                    ref = trade['peak_price']
                    eq  = _get_equity()
                    slip = trade.get('slippage_pct', 0.0)
                    send_msg(
                        f"📍 *Trailing Stop נגע — {sym}*\n"
                        f"{'שיא' if direction=='LONG' else 'שפל'}: `{ref:.6g}` → יציאה: `{current_price:.6g}`\n"
                        f"{icon} *P&L: {pnl_usd:+.2f}$ ({pnl_pct_r:+.1f}% על מרג'ין)* | {tbadge}\n"
                        f"💼 {t_leverage}x Isolated · Trailing {TRAIL_PCT}%\n"
                        f"📊 Slippage: {slip:.2f}% (Demo)\n"
                        f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`\n"
                        f"{icon} סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()
                    continue

                # ── חישוב רמת BE (Entry + 0.1% לLONG / Entry - 0.1% לSHORT) ──
                be_lock_price = round(entry * (1 + BE_LOCK_BUFFER_PCT / 100), 8) \
                                if direction == 'LONG' \
                                else round(entry * (1 - BE_LOCK_BUFFER_PCT / 100), 8)

                # 1a. Greed Early BE — FNG≥70: BE at +2% (immediately, before TP1)
                if not trade['be_triggered'] and fng_v_mgr >= GREED_THRESHOLD:
                    greed_profit_pct = abs(current_price - entry) / entry * 100
                    if profit_dir(current_price) and greed_profit_pct >= GREED_EARLY_BE_PCT:
                        trade['sl']           = be_lock_price
                        trade['be_triggered'] = True
                        print(f"  [SENTIMENT] GREED EARLY BE: {sym} SL→{be_lock_price:.6g} @ {current_price:.6g} (+{greed_profit_pct:.2f}%, FNG={fng_v_mgr})")
                        send_msg(
                            f"🔒 *Greed Early BE — {sym}*\n"
                            f"מחיר: `{current_price:.6g}` (+{greed_profit_pct:.2f}% רווח)\n"
                            f"SL הועבר ל: `{be_lock_price:.6g}` (+{BE_LOCK_BUFFER_PCT}% מעל כניסה) 🛡️\n"
                            f"(FNG={fng_v_mgr} — מצב חמדנות) | ההון מוגן!"
                        )

                # 1b. Break Even סטנדרטי — ב-+2% רווח, SL → Entry+0.1%
                if not trade['be_triggered'] and be_hit(current_price):
                    trade['sl']           = be_lock_price
                    trade['be_triggered'] = True
                    send_msg(
                        f"🔒 *Break Even מופעל — {sym}*\n"
                        f"מחיר: `{current_price:.6g}` (+{BE_BUFFER_PCT}% מהכניסה)\n"
                        f"SL הועבר ל: `{be_lock_price:.6g}` (+{BE_LOCK_BUFFER_PCT}% מעל כניסה)\n"
                        f"💼 {LEVERAGE}x Isolated · ההון מוגן ✅"
                    )

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
                        daily_stats['total_pnl'] += partial_pnl
                        wallet_credit(partial_pnl, quarter_margin)
                        save_active_trades()
                        print(f"  [Partial25] {sym}: סגר 25% @ {current_price:.6g} "
                              f"(שיא={trade['peak_price']:.6g} ירד {drop_from_peak:.1f}%) "
                              f"PnL={partial_pnl:+.2f}$")
                        send_msg(
                            f"⚡ *Partial Close 25% — {sym}*\n\n"
                            f"המחיר הגיע ל\\+{peak_profit_pct:.1f}% ואז ירד {drop_from_peak:.1f}% מהשיא\n"
                            f"סגרנו 25% מהפוזיציה @ `{current_price:.6g}`\n"
                            f"💰 P&L חלקי: *{partial_pnl:+.2f}$*\n"
                            f"75% נשאר פתוח · SL: `{trade['sl']:.6g}`"
                        )

                # 2. TP1 — סגור 50%, הפעל Trailing (+ auto-BE ב-Hunter Mode)
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

                    # TP1 hit → always move SL to Break Even (entry price)
                    trade['sl']           = entry
                    trade['be_triggered'] = True
                    hunter_label = " [Hunter Mode]" if trade.get('hunter_be_on_tp1') else ""
                    send_msg(
                        f"🎯 *TP1 הושג — {sym}!*{hunter_label}\n"
                        f"מחיר: `{current_price:.6g}` | {direction} | {tbadge}\n"
                        f"50% נסגרו · ✅ *Profit at TP1: +${tp1_pnl}* (+{tp1_pct_r}%)\n"
                        f"🔒 *SL הועבר ל-BE אוטומטית!* `{entry:.6g}` — הון מוגן\n"
                        f"📍 Trailing SL: `{trade['trailing_sl']:.6g}` | שאר 50% ממשיכים ל-TP2\n"
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
                        daily_stats['total_pnl'] -= loss
                        daily_stats['close_reasons']['SL'] += 1
                        wallet_credit(-loss, _trade_margin)   # מרג'ין חוזר פחות ההפסד
                        _log_closed_trade(trade, 'SL', -loss, current_price)
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
                    wallet_credit(total, trade.get('margin', MARGIN))
                    _log_closed_trade(trade, 'TP', total, current_price)
                    eq = _get_equity()
                    est_tp_full = round(abs(current_price - entry) / entry * half, 2)
                    slip = trade.get('slippage_pct', 0.0)
                    send_msg(
                        f"✅ *TP מלא הושג — {sym}!* 🎉\n"
                        f"מחיר: `{current_price:.6g}` | {direction} | {tbadge}\n"
                        f"שאר 50% נסגרו · ✅ *Profit at TP: +${est_tp_full}*\n"
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
                    wallet_credit(total, trade.get('margin', MARGIN))
                    _log_closed_trade(trade, 'TP1+Trail', total, current_price)
                    eq = _get_equity()
                    ref_price = trade['peak_price']
                    slip = trade.get('slippage_pct', 0.0)
                    send_msg(
                        f"📍 *Trailing Stop נגע — {sym}*\n"
                        f"{'שיא' if direction=='LONG' else 'שפל'}: `{ref_price:.6g}` → יציאה: `{current_price:.6g}`\n"
                        f"50% נסגרו: {icon} *{half_pnl:+}$* | {tbadge}\n"
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

    send_msg(msg)

    # ── פירוט עסקאות סגורות היום ─────────────────────────────────────────────
    today_str = now_il().strftime('%Y-%m-%d')
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
        if t.get('tp1_triggered'):
            total_floating += round(t.get('tp1_pnl', 0) + _ps / 2 * p_pct / 100, 2)
        else:
            total_floating += round(_ps * p_pct / 100, 2)

    float_icon = "📈" if total_floating >= 0 else "📉"
    realized   = round(wallet.get('total_pnl', 0.0), 2)
    total_bal  = round(wallet.get('starting', STARTING_BALANCE) + realized + total_floating, 2)

    # ── FNG Mode summary ──
    try:
        _fng_hb, _lbl_hb = get_fear_greed()
        _mode_hb = get_fng_mode(_fng_hb)
        mode_line = (
            f"🧭 Sentiment: *{_lbl_hb}* ({_fng_hb}) — "
            f"{_mode_hb['emoji']} Mode: *{_mode_hb['name']}*\n"
            f"   SL {_mode_hb['sl']}% · TP1 {_mode_hb['tp1']}% · TP {_mode_hb['tp']}% · BE {_mode_hb['be']}%"
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
        wallet_credit(net_pnl, trade.get('margin', MARGIN))
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

@bot.callback_query_handler(func=lambda call: call.data.startswith('close_'))
def handle_close_button(call):
    """מטפל בלחיצה על כפתור '❌ Close Position' — סוגר את העסקה מיד."""
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

        # P&L נטו
        if trade.get('tp1_triggered'):
            raw_pct_cb = (current_price - entry) / entry * 100
            half_pnl_cb = round(_ps_cb / 2 * (raw_pct_cb if direction_cb == 'LONG' else -raw_pct_cb) / 100, 2)
            net_pnl_cb  = round(trade.get('tp1_pnl', 0) + half_pnl_cb, 2)
        else:
            raw_pct_cb = (current_price - entry) / entry * 100
            pnl_pct_cb = raw_pct_cb if direction_cb == 'LONG' else -raw_pct_cb
            net_pnl_cb = round(_ps_cb * pnl_pct_cb / 100, 2)

        with trades_lock:
            active_trades.remove(trade)
        wallet_credit(net_pnl_cb, trade.get('margin', MARGIN))
        _log_closed_trade(trade, 'Manual', net_pnl_cb, current_price)
        daily_stats['close_reasons']['Manual'] += 1
        if net_pnl_cb >= 0:
            daily_stats['wins'] += 1
        else:
            daily_stats['losses'] += 1
        daily_stats['total_pnl'] = round(daily_stats.get('total_pnl', 0) + net_pnl_cb, 2)
        save_active_trades()
        eq = _get_equity()

        pnl_icon = "📈" if net_pnl_cb >= 0 else "📉"
        send_msg(
            f"🚪 *Button Close — {symbol}*\n"
            f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
            f"{pnl_icon} P&L: *${net_pnl_cb:+.2f}*\n"
            f"💼 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance',0):.2f}`"
        )
        # מחק את הכפתור מההודעה המקורית
        try:
            bot.edit_message_reply_markup(call.message.chat.id,
                                          call.message.message_id,
                                          reply_markup=None)
        except Exception:
            pass
        print(f"[ButtonClose] {symbol} closed via button at {current_price}, P&L={net_pnl_cb:+.2f}")

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

        # SL / TP — סטנדרטי
        sl_pct = SL_PCT_FIXED   # 3.5%
        tp_pct = TP_PCT_FIXED   # 10.5%
        if direction == 'LONG':
            sl_price  = round(entry_price * (1 - sl_pct / 100), 8)
            tp_price  = round(entry_price * (1 + tp_pct / 100), 8)
            tp1_price = round(entry_price * (1 + TP1_PCT_FIXED / 100), 8)
            be_price  = round(entry_price * (1 + BE_BUFFER_PCT  / 100), 8)
        else:
            sl_price  = round(entry_price * (1 + sl_pct / 100), 8)
            tp_price  = round(entry_price * (1 - tp_pct / 100), 8)
            tp1_price = round(entry_price * (1 - TP1_PCT_FIXED / 100), 8)
            be_price  = round(entry_price * (1 - BE_BUFFER_PCT  / 100), 8)

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
            'sniper':          False,
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


@bot.message_handler(commands=['audit'])
def handle_audit(message):
    """שולח דוח ניתוח AI מלא (Gemini) — פירוט עסקאות + המלצות."""
    send_msg("🤖 _מריץ ניתוח Gemini... עד 30 שניות_")
    try:
        from gdrive_reporter import run_audit_upload
        run_audit_upload(
            active_trades, wallet, closed_trades_log,
            send_telegram=send_msg
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
            model="claude-3-haiku-20240307",
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

    # SL/TP על-פי Evening SOL Strategy
    sl_pct  = round(abs(price - sl) / price * 100, 2)   # ~3.5%
    tp_pct  = round(abs(tp - price) / price * 100, 2)   # ~5.0%
    tp1_pct = tp_pct                                      # SOL: אין split — TP = TP1
    tp1_price = tp
    be_price  = round(price * 1.02, 6)                   # BE at +2%

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
        'fng_at_entry':    None,
        'sniper':          False,
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

            # margin: 10% מהיתרה, או 7% אם יתרה < $30
            margin_pct = 0.07 if avail < 30 else 0.10
            margin     = max(round(avail * margin_pct, 2), 5.0)

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
                f"💰 יתרה: ${avail:.2f} | מרג'ין: ${margin:.0f} ({margin_pct*100:.0f}%) | Slots: {open_slots}\n"
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
                current_avail = wallet.get('balance', STARTING_BALANCE)
                current_margin = max(round(current_avail * margin_pct, 2), 5.0)
                open_breakout_trade(
                    symbol    = sym,
                    price     = price,
                    margin    = current_margin,
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
    /slots 1–5   — מגדיר מקסימום slots (עסקאות פתוחות בו-זמנית)
    """
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

    if not (1 <= v <= 5):
        send_msg("❌ הערך חייב להיות בין 1 ל-5.")
        return

    old        = MAX_TRADES
    MAX_TRADES = v
    _save_config()
    n_open     = len(active_trades)
    bar        = '🟢' * n_open + '⬜' * max(0, MAX_TRADES - n_open)

    if v < n_open:
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
        status = "🔴 Kill-Switch פעיל — אין עסקאות חדשות"
    elif fng_v <= FEAR_THRESHOLD:
        status = "🟠 Fear — RSI<30 ל-LONG, SL+1%"
    elif fng_v >= GREED_THRESHOLD:
        status = "🟢 Greed — פוזיציה 60% + BE מוקדם"
    else:
        status = "🟡 Neutral — מסחר רגיל"

    send_msg(
        f"📊 *הגדרות מדד הפחד — FNG*\n"
        f"{'─' * 28}\n\n"
        f"📡 *מדד נוכחי:* {fng_v} ({fng_lbl})\n"
        f"⚡ *סטטוס:* {status}\n\n"
        f"*⚙️ ספים פעילים:*\n"
        f"  🔴 Kill-Switch: FNG < *{EXTREME_FEAR_THRESHOLD}* (טווח: 5–25)\n"
        f"  🟠 Fear:        FNG ≤ *{FEAR_THRESHOLD}* (טווח: 15–45)\n"
        f"  🟢 Greed:       FNG ≥ *{GREED_THRESHOLD}* (טווח: 55–85)\n\n"
        f"*🔧 לשינוי:*\n"
        f"  `/setfng extreme 15` — שנה Kill-Switch\n"
        f"  `/setfng fear 25`    — שנה Fear\n"
        f"  `/setfng greed 75`   — שנה Greed"
    )


@bot.message_handler(commands=['setfng'])
def handle_setfng(message):
    """שינוי סף מדד הפחד. שימוש: /setfng extreme|fear|greed <ערך>"""
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
        'extreme': (5,  25,  'Kill-Switch'),
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
        f"  🔴 Kill-Switch: FNG < *{EXTREME_FEAR_THRESHOLD}*\n"
        f"  🟠 Fear:        FNG ≤ *{FEAR_THRESHOLD}*\n"
        f"  🟢 Greed:       FNG ≥ *{GREED_THRESHOLD}*\n\n"
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
        f"  /report     — דוח יומי מלא (סטטיסטיקות)\n"
        f"  /audit      — דוח ניתוח AI מלא (Gemini)\n"
        f"  /scanreport — דוח סריקה אחרון\n"
        f"  /ping       — בדיקת חיות הבוט\n\n"
        f"📊 *הגדרות מדד הפחד (FNG)*\n"
        f"  /fng                  — הצג ספים נוכחיים + טווחים מותרים\n"
        f"  /setfng extreme 15    — שנה Kill-Switch (5–25)\n"
        f"  /setfng fear 25       — שנה Fear (15–45)\n"
        f"  /setfng greed 75      — שנה Greed (55–85)\n\n"
        f"📰 *חדשות וניתוח*\n"
        f"  /news <טקסט>      — הדבק פוסט מערוץ → Gemini מנתח + השפעה על עסקאות\n\n"
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

            # ── Sandbox Mode (Kill-Switch פעיל) ───────────────────────────────
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
            ks_note_m = (
                " ⚡ BTC BULL — Hunter Active" if fng_v_m < EXTREME_FEAR_THRESHOLD and btc_regime_now == 'BULL'
                else " 🔒 Kill\\-Switch" if fng_v_m < EXTREME_FEAR_THRESHOLD and btc_regime_now != 'BULL'
                else ""
            )
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
                sys_msg_m = (
                    f"אף מטבע לא הגיע לציון {MIN_SCORE}/100. הטוב ביותר: {best_m['symbol']} עם {best_m['best_score']}/100" if best_m
                    else f"אף מטבע לא עמד בסף {MIN_SCORE}/100"
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
    PROD: רושם Telegram Webhook — Telegram שולח updates ל-/api/tg_hook.
          לא צריך polling; מחסל את בעיית ה-long-polling ב-Replit production.
    DEV:  infinity_polling רגיל (webhook לא זמין מ-localhost).
    """
    is_deployed = bool(os.environ.get('REPLIT_DEPLOYMENT', ''))
    mode_label  = "PROD" if is_deployed else "DEV"
    token       = os.environ.get('TELEGRAM_TOKEN', '')

    if is_deployed:
        # ── PROD: WEBHOOK MODE ──────────────────────────────────────────────
        print(f"[PROD] Webhook mode — ממתין 35s לסיום boot...", flush=True)
        time.sleep(35)

        # בנה את ה-URL מ-REPLIT_DOMAINS (Replit מגדיר אוטומטית בפרודקשן)
        domains = os.environ.get('REPLIT_DOMAINS', '')
        if domains:
            first_domain = domains.split(',')[0].strip()
            webhook_url = f"https://{first_domain}/api/tg_hook"
        else:
            webhook_url = "https://crypto-bot-bymzrkhy.replit.app/api/tg_hook"

        print(f"[PROD] Setting webhook → {webhook_url}", flush=True)

        # מחק webhook קיים
        r1 = _tg_api_call(token, "deleteWebhook", {"drop_pending_updates": True})
        print(f"[PROD] deleteWebhook: {r1}", flush=True)
        time.sleep(2)

        # רשום webhook חדש
        r2 = _tg_api_call(token, "setWebhook", {
            "url": webhook_url,
            "drop_pending_updates": True,
            "allowed_updates": ["message", "callback_query"],
        })
        print(f"[PROD] setWebhook: {r2}", flush=True)

        if r2.get('ok'):
            print(f"[PROD] ✅ Webhook active — Telegram יישלח updates ל-{webhook_url}", flush=True)
        else:
            print(f"[PROD] ❌ setWebhook failed: {r2}", flush=True)
        return  # no polling loop — updates arrive via /api/tg_hook

    # ── DEV: POLLING MODE ───────────────────────────────────────────────────
    print(f"[DEV] Polling mode — ממתין 10s...", flush=True)
    time.sleep(10)

    # נקה webhook קודם (עם timeout מפורש)
    r = _tg_api_call(token, "deleteWebhook", {"drop_pending_updates": True})
    print(f"[DEV] deleteWebhook: {r}", flush=True)

    while True:
        global _polling_last_activity
        _polling_last_activity = time.time()
        try:
            print(f"[DEV] infinity_polling starting...", flush=True)
            bot.infinity_polling(
                timeout=20,
                long_polling_timeout=5,
                logger_level=None,
            )
            print(f"[DEV] infinity_polling returned — restarting loop", flush=True)
        except Exception as e:
            err_str = str(e)
            print(f"[DEV] Polling exception: {err_str[:200]}", flush=True)
            if '409' in err_str:
                print(f"⚠️  [DEV] 409 Conflict — ממתין 30s...", flush=True)
                time.sleep(30)
                _tg_api_call(token, "deleteWebhook", {"drop_pending_updates": True})
            elif '401' in err_str:
                print(f"❌  [DEV] 401 Unauthorized — TELEGRAM_TOKEN שגוי?", flush=True)
                time.sleep(60)
            else:
                print(f"[DEV] Polling error — restart in 5s: {e}", flush=True)
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

    # ── Sentiment Kill-Switch (Extreme Fear < EXTREME_FEAR_THRESHOLD) ────────
    fng_v_scan, fng_lbl_scan, fng_action = sentiment_check("scan")

    # BTC COMPASS OVERRIDE: אם ה-BTC Regime הוא BULL — הקומפס ינצח את ה-Kill-Switch.
    # "הזדמנויות הטובות ביותר קורות בזמן פחד קיצוני + BTC בשבירה"
    btc_overrides_killswitch = (btc_regime == 'BULL')
    kill_switch_active = (fng_v_scan < EXTREME_FEAR_THRESHOLD) and not btc_overrides_killswitch

    if btc_overrides_killswitch and fng_v_scan < EXTREME_FEAR_THRESHOLD:
        print(f"⚡ BTC COMPASS OVERRIDE: FNG={fng_v_scan} (Extreme Fear) אך BTC BULL — Kill-Switch מבוטל! ממשיך לסרוק...")
    if kill_switch_active:
        print(f"SENTIMENT KILL-SWITCH: FNG={fng_v_scan} < {EXTREME_FEAR_THRESHOLD} — בודק Sniper Exception לכל מועמד...")

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

    # ── BTC Compass Parabolic Bull Filter — EMA200(4H) + RSI(1H)>60 ────────────
    if direction == 'SHORT':
        _parabolic, _rsi_1h, _ema200 = is_btc_parabolic_bull()
        if _parabolic:
            print(f"BTC COMPASS VETO: PARABOLIC BULL — RSI1H={_rsi_1h:.1f}>60 above EMA200 → skipping {len(candidates)} SHORT candidates")
            for c in candidates:
                rejected_out.append({
                    'symbol': c['symbol'], 'direction': direction, 'best_score': 0,
                    'reason': f'BTC Parabolic Bull (EMA200 4H above + RSI1H={_rsi_1h:.1f}>60) — SHORTs חסומים',
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

        # ── Slow-Movers Blacklist — Adaptive Sniper ignores low-momentum coins ──
        if symbol in SLOW_MOVERS:
            print(f"  [SLOW_MOVERS] {symbol} — blacklisted (low momentum), skipping")
            if rejected_out is not None:
                rejected_out.append({
                    'symbol': symbol, 'direction': direction, 'best_score': 0,
                    'reason': 'Slow-Mover Blacklist (TRX/ADA — low momentum)',
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

            print(f"Scoring {symbol} [{direction}] @ {price:.6g} [4H]")
            score, breakdown, atr = score_symbol(df_4h, df_1h, symbol, direction, fng_v=fng_v_scan)
            score_4h  = score
            score_1h  = 0
            score_15m = 0
            best_score     = score
            best_breakdown = breakdown
            chosen_tf = '4H'
            chosen_df = df_4h
            tf_reason = 'טרנד חזק בגרף 4H'

            # ── Sniper Exception — Kill-Switch פעיל בלבד ─────────────────────
            if kill_switch_active:
                _vol_avg_s   = df_4h['volume'].iloc[-12:-2].mean()
                _vol_ratio_s = float(df_4h['volume'].iloc[-2] / _vol_avg_s) if _vol_avg_s > 0 else 0.0
                sniper_ok, sniper_verdict, sniper_reason = sniper_claude_check(
                    symbol=symbol, score=score_4h, direction=direction,
                    df_4h=df_4h, df_1h=df_1h, vol_ratio=_vol_ratio_s,
                    price=price, fng_v=fng_v_scan, btc_regime=btc_regime,
                )
                if sniper_ok:
                    print(f"  🎯 SNIPER EXCEPTION: {symbol} — bypassing Kill-Switch! ({sniper_reason})")
                    _sniper_notif = (
                        f"🎯 *Sniper Entry \\— Kill\\-Switch Override\\!*\n\n"
                        f"{'🟢' if direction == 'LONG' else '🔴'} `{symbol}` {direction} · 4H\n"
                        f"📊 ציון: *{score_4h}/100* \\(≥ {SNIPER_MIN_SCORE}\\)\n"
                        f"📐 {sniper_reason.replace('-', '\\-').replace('.', '\\.').replace('(', '\\(').replace(')', '\\)').replace('=', '\\=')}\n\n"
                        f"⚠️ _Extreme Fear Market — FNG\\={fng_v_scan}_\n"
                        f"💰 _Half\\-Size Entry: מרג'ין \\${MARGIN * SNIPER_MARGIN_MULT:.0f} במקום \\${MARGIN:.0f}_"
                    )
                    send_msg(_sniper_notif)
                    _s_ob_f, _s_ob_h, _s_ob_l = False, None, None
                    try:
                        _s_ob_f, _s_ob_h, _s_ob_l, _ = detect_order_blocks(df_1h, direction, lookback=50)
                        if not (_s_ob_f and _s_ob_h and _s_ob_h > 0 and _s_ob_l > 0):
                            _s_ob_f, _s_ob_h, _s_ob_l = False, None, None
                    except Exception:
                        pass
                    open_demo_trade(
                        symbol, price, f"Sniper Exception: {sniper_reason}",
                        df_4h, direction=direction,
                        score=score_4h, atr=atr,
                        timeframe='4H', tf_reason='Sniper Kill-Switch Override',
                        fng_v=fng_v_scan, sniper_mode=True,
                        ob_found=_s_ob_f, ob_high=_s_ob_h, ob_low=_s_ob_l,
                    )
                    found += 1
                else:
                    print(f"  [Sniper] ❌ {symbol}: {sniper_verdict} — {sniper_reason}")
                    rejected_out.append({
                        'symbol': symbol, 'direction': direction, 'best_score': score_4h,
                        'reason': f'Kill-Switch + Sniper failed: {sniper_verdict}',
                        'scores': {'4H': score_4h, '1H': 0, '15m': 0},
                    })
                continue  # skip normal flow when Kill-Switch active

            # ── שלב 2: אם 4H לא מספיק — נסה 1H ──
            if score < MIN_SCORE:
                df_15m = get_data_cached(symbol, timeframe='15m', limit=250)
                score_1h, breakdown_1h, atr_1h = score_symbol(df_1h, df_15m, symbol, direction, fng_v=fng_v_scan)
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
                    score_15m, breakdown_15m, atr_15m = score_symbol(df_15m, df_1h, symbol, direction, fng_v=fng_v_scan)
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

            # ── Priority Score Logging — compare with open trades when slots full ─
            if at_capacity:
                if score >= MIN_SCORE and active_trades:
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

            if score >= MIN_SCORE:
                # ── Extreme Fear: LONG מותנה — Adaptive Sniper ────────────────────
                # בפחד קיצוני (FNG < EXTREME_FEAR_THRESHOLD) ו-BTC לא BULL:
                # LONG דורש ציון > EXTREME_FEAR_LONG_MIN_SCORE (95) — מאוד סלקטיבי
                # SHORT — מועדף ולא מוגבל
                if (direction == 'LONG'
                        and fng_v_scan < EXTREME_FEAR_THRESHOLD
                        and not btc_overrides_killswitch
                        and score < EXTREME_FEAR_LONG_MIN_SCORE):
                    print(f"  [EXTREME_FEAR_FILTER] {symbol} LONG rejected: "
                          f"FNG={fng_v_scan}<{EXTREME_FEAR_THRESHOLD}, "
                          f"Score={score}<{EXTREME_FEAR_LONG_MIN_SCORE} (need >{EXTREME_FEAR_LONG_MIN_SCORE} in fear)")
                    rejected_out.append({
                        'symbol': symbol, 'direction': direction, 'best_score': score,
                        'reason': f'Extreme Fear Filter: LONG requires score>{EXTREME_FEAR_LONG_MIN_SCORE} (FNG={fng_v_scan})',
                        'scores': {'4H': score_4h, '1H': score_1h, '15m': score_15m},
                    })
                    continue

                # ── Strong Uptrend Bias — BTC > EMA200 על 1H+4H: SHORT דורש ציון גבוה ────
                if direction == 'SHORT':
                    _sup, _ema1h, _ema4h = is_btc_strong_uptrend()
                    if _sup and score < STRONG_UPTREND_SHORT_MIN_SCORE:
                        print(f"  [TREND BIAS] {symbol} SHORT rejected — BTC Strong Uptrend (1H+4H>EMA200), score={score}<{STRONG_UPTREND_SHORT_MIN_SCORE}")
                        rejected_out.append({
                            'symbol': symbol, 'direction': 'SHORT', 'best_score': score,
                            'reason': f'Trend Bias: BTC Strong Uptrend — SHORT requires score>{STRONG_UPTREND_SHORT_MIN_SCORE} (got {score})',
                            'scores': {'4H': score_4h, '1H': score_1h, '15m': score_15m},
                        })
                        continue

                # חישוב RSI ו-EMA200 רגע לפני פתיחה לשמירה בדוח
                try:
                    _last_rsi   = ta.rsi(chosen_df['close'], length=14).iloc[-1]
                    _last_ema   = ta.ema(chosen_df['close'], length=200).iloc[-1]
                except Exception:
                    _last_rsi = _last_ema = None

                # ── Fear Filter RSI: REMOVED — Adaptive Sniper uses RSI_VETO_LONG (85) only ──
                # FNG sentiment controlled via EXTREME_FEAR_LONG_MIN_SCORE for LONGs.

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
                # Claude Filter Disabled as Judge — Technical Hunter Mode Active.
                # ציון ≥ MIN_SCORE + BTC Regime = כניסה. Claude = הערה בלבד.
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
                open_demo_trade(
                    symbol, price, breakdown,
                    chosen_df, direction=direction,
                    score=score, atr=atr,
                    timeframe=chosen_tf, tf_reason=tf_reason,
                    rsi=_last_rsi, ema200=_last_ema,
                    fng_v=fng_v_scan,
                    ob_found=_ob_f, ob_high=_ob_h, ob_low=_ob_l,
                    df_1h=df_1h,
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
    now_hour = now_il().hour
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
        ema_ok    = "✅" if ema_dist is not None and ema_dist <= SNIPER_EMA_PCT else "⚠️"
        vol_ok    = "✅" if vol_ratio and vol_ratio >= SNIPER_VOL_MIN else "—"

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
            if fng_v < EXTREME_FEAR_THRESHOLD and btc_r == 'BULL':
                msg += f"\n⚡ _BTC BULL Compass מבטל Kill\\-Switch \\(FNG\\={fng_v}\\) — בסריקה הבאה ייפתח_"
            elif fng_v < EXTREME_FEAR_THRESHOLD:
                msg += f"\n⚠️ _Kill\\-Switch פעיל \\(FNG\\={fng_v}\\) — Sniper Exception יבדוק בסריקה_"
            else:
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

    with trades_lock:
        if any(t['symbol'] == symbol for t in active_trades):
            print(f"SCALP: {symbol} already in active_trades — skip")
            return
        scalp_count = sum(1 for t in active_trades if t.get('scalp'))
    if scalp_count >= MAX_SCALP_TRADES:
        print(f"SCALP: max scalp trades ({MAX_SCALP_TRADES}) reached — skip {symbol}")
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

    # ── 🎯 Hunter Mode Detection ──────────────────────────────────────────────
    fng_v_now, _, _ = sentiment_check("scalp_hunter")
    hunter, hunter_reason = is_hunter_mode(fng_v_now, change_24h)
    if hunter:
        send_msg(
            f"🎯 *Hunter Mode פעיל — {symbol.replace('/USDT','')} (Scalp)*\n"
            f"⚠️ מתח שוק גבוה: _{hunter_reason}_\n"
            f"Precision Hunter — מקבל רק עסקאות RR 1:3 ומעלה."
        )
        print(f"  [HUNTER SCALP] {symbol}: {hunter_reason} → RR min={HUNTER_MIN_RR}")

    # ── גודל פוזיציה לפי 1.5% סיכון מהון ────────────────────────────────────
    equity = _get_equity()
    eff_margin, pos_size, leverage, sl_pct, tp1_pct, tp_pct = calc_risk_position('Scalp', equity)

    if wallet.get('balance', 0) < eff_margin:
        print(f"SCALP: insufficient balance (${wallet.get('balance', 0):.2f}) — skip {symbol}")
        return

    # Hunter Mode: TP1 = 1:1 RR, TP = 1:3 RR
    if hunter:
        tp1_pct = round(sl_pct * HUNTER_TP1_RR, 2)
        tp_pct  = round(sl_pct * HUNTER_MIN_RR, 2)
        print(f"  [HUNTER] Scalp TP1={tp1_pct}% TP={tp_pct}% (SL={sl_pct}%)")

    # ── מחירי SL / TP1 / TP ──────────────────────────────────────────────────
    sl_dist  = price * sl_pct  / 100
    tp1_dist = price * tp1_pct / 100
    tp_dist  = price * tp_pct  / 100

    if direction == 'LONG':
        sl_price  = round(price - sl_dist,  8)
        tp1_price = round(price + tp1_dist, 8)
        tp_price  = round(price + tp_dist,  8)
        be_price  = round(price + tp1_dist * SCALP_TRACK_BE_TRIGGER, 8)
    else:
        sl_price  = round(price + sl_dist,  8)
        tp1_price = round(price - tp1_dist, 8)
        tp_price  = round(price - tp_dist,  8)
        be_price  = round(price - tp1_dist * SCALP_TRACK_BE_TRIGGER, 8)

    # ── בדיקת RR (מינימום 1:2 רגיל / 1:3 ב-Hunter Mode) ────────────────────
    est_profit = round(abs(tp_price  - price) / price * pos_size, 2)
    est_loss   = round(abs(sl_price  - price) / price * pos_size, 2)
    rr_ratio   = round(est_profit / est_loss, 2) if est_loss > 0 else 0

    required_rr = HUNTER_MIN_RR if hunter else MIN_RR_RATIO
    if rr_ratio < required_rr:
        print(f"[SCALP] RR={rr_ratio:.2f} < {required_rr} {'(Hunter)' if hunter else ''} — {symbol} נדחה")
        if hunter:
            send_msg(
                f"❌ *Trade Rejected: {symbol.replace('/USDT','')} (Scalp)*\n"
                f"Current RR: 1:{rr_ratio} | Min requirement in tense market: 1:{int(HUNTER_MIN_RR)}\n"
                f"🎯 _Hunter Mode Active — Precision entries only_"
            )
        return

    max_risk_usd = round(pos_size * sl_pct / 100, 2)
    risk_pct_eq  = round(max_risk_usd / equity * 100, 2)

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
        'sniper':          False,
        'scalp':           True,
        'scalp_opened_ts': time.time(),
        'track':           track,
        'vol_usd':         round(vol_usd),
        'change_24h':      round(change_24h, 2),
        'slippage_pct':    0.0,
        'hunter_mode':     hunter,
        'hunter_be_on_tp1': hunter,
    }
    place_order(trade, eff_margin)

    be_trigger_pct = round(tp1_pct * SCALP_TRACK_BE_TRIGGER, 2)
    emoji     = "🟢" if direction == 'LONG' else "🔴"
    dir_label = "Quick-Long (Dip Buy)" if direction == 'LONG' else "Scalp-Short (Bubble)"
    scalp_msg = (
        f"⚡ *{dir_label}: {symbol.replace('/USDT', '')} {emoji}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 כניסה: `{price:.6g}`\n"
        f"🛑 SL:    `{sl_price:.6g}` (-{sl_pct}%)\n"
        f"🔒 BE:    `{be_price:.6g}` (+{be_trigger_pct}%)\n"
        f"🎯 TP1:   `{tp1_price:.6g}` (+{tp1_pct}%)\n"
        f"🎯 TP:    `{tp_price:.6g}` (+{tp_pct}%)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💼 {leverage}x · ${eff_margin:.0f} · ⚖️ RR 1:{rr_ratio} · ⏱ {SCALP_MAX_DURATION_MIN}m"
    )
    try:
        df_scalp = get_data(symbol, timeframe='1h', limit=80)
        chart_buf = generate_chart(df_scalp, symbol, price, sl_price, tp1_price, direction) \
                    if df_scalp is not None else None
    except Exception:
        chart_buf = None
    send_chart_alert(chart_buf, symbol, scalp_msg)
    print(f"[SCALP] {direction}: {symbol} @ {price:.6g} | SL={sl_pct}% TP={tp_pct}% BE@{be_trigger_pct}% | {leverage}x margin=${eff_margin} risk=${max_risk_usd} ({risk_pct_eq}%)")


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
    ישיר — ללא Kill-Switch, BTC filter, או 4H/1H אישור.
    """
    global active_trades

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
        'atr':             0.0,
        'timeframe':       '5m',
        'rsi':             None,
        'ema200':          None,
        'score_breakdown': conviction_txt,
        'opened_at':       now_il().isoformat(timespec='seconds'),
        'pos_size':        CLIFF_POS_SIZE,
        'margin':          CLIFF_MARGIN,
        'fng_at_entry':    None,
        'sniper':          False,
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
    עוקף Kill-Switch (Mean Reversion, לא trend-following).
    """
    print("Thread 6 (Scalp Scanner) started.")
    time.sleep(90)   # המתן שה-bot יתייצב לפני הסריקה הראשונה

    while True:
        try:
            fng_v, fng_lbl, _ = sentiment_check("scalp_scan")
            if fng_v is None:
                time.sleep(SCALP_SCAN_INTERVAL_WAIT)
                continue

            # Scalp scanner is ONLY active during Extreme Fear (FNG < threshold)
            # In normal market (WAIT mode), sleep longer to save CPU
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
    """
    try:
        df = get_data('BTC/USDT', timeframe='15m', limit=30)
        ema20 = ta.ema(df['close'], length=20)
        if ema20 is None:
            return True   # fallback permissive
        btc_close = float(df['close'].iloc[-1])
        btc_ema   = float(ema20.iloc[-1])
        return btc_close > btc_ema
    except Exception:
        return True   # fallback permissive


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
RSI_VETO_SHORT                = 15   # RSI מינימום ל-SHORT (מתחת = oversold; lowered to allow high-momentum sells)
BREAKOUT_MIN_VOL              = 1.5  # volume ratio מינימלי (150% מהממוצע = 50% מעל)
RSI_VETO_BREAKOUT_LONG        = 78   # RSI מקסימום ל-LONG בפריצה עם נפח גבוה (≥1.5x)
BREAKOUT_FNG_REDUCED_MARGIN_MAX  = 20    # FNG ≤ 20 → מרג'ין מוקטן ב-20%
BREAKOUT_FNG_REDUCED_MARGIN_MULT = 0.80  # מכפיל מרג'ין בפחד קיצוני (FNG 10-20)
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
    margin   = 10% מהיתרה הפנויה (מחושב ע"י הקורא).
    pos_size = margin × LEVERAGE (10x).
    SL=3.5% | TP1=5% | TP=10.5% (RR 1:3) | Trailing=2.5%.
    """
    if wallet.get('balance', STARTING_BALANCE) < margin:
        print(f"[Breakout] insufficient balance for {symbol} — skip")
        return

    pos_size  = round(margin * LEVERAGE, 2)
    sl_pct    = SL_PCT_FIXED    # 3.5%
    tp_pct    = TP_PCT_FIXED    # 10.5%
    tp1_pct   = TP1_PCT_FIXED   # 5.0%
    be_pct    = BE_BUFFER_PCT   # 2.0%

    if direction == 'LONG':
        sl_price  = round(price * (1 - sl_pct  / 100), 8)
        tp_price  = round(price * (1 + tp_pct  / 100), 8)
        tp1_price = round(price * (1 + tp1_pct / 100), 8)
        be_price  = round(price * (1 + be_pct  / 100), 8)
        level_lbl = f"4H High `${h4_level:.6g}`"
        dir_emoji = "🚀"
        dir_label = "LONG"
        sl_sign   = "-"; tp_sign = "+"
    else:  # SHORT
        sl_price  = round(price * (1 + sl_pct  / 100), 8)
        tp_price  = round(price * (1 - tp_pct  / 100), 8)
        tp1_price = round(price * (1 - tp1_pct / 100), 8)
        be_price  = round(price * (1 - be_pct  / 100), 8)
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
        'phase':           'initial',
        'be_triggered':    False,
        'tp1_triggered':   False,
        'tp1_pnl':         0.0,
        'peak_price':      price,
        'trailing_sl':     None,
        'score':           95,
        'atr':             atr_val,
        'timeframe':       'Breakout',
        'rsi':             round(rsi, 2) if rsi is not None else None,
        'ema200':          None,
        'score_breakdown': reason,
        'opened_at':       now_il().isoformat(timespec='seconds'),
        'pos_size':        pos_size,
        'margin':          margin,
        'fng_at_entry':    fng_v,
        'sniper':          False,
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
                signal, price, h4_level, rsi, vol_ratio = _coin_breakout_full(sym, direction)
                if not signal:
                    continue

                # RSI Filter — Breakout with high volume allows RSI up to 78
                if direction == 'LONG':
                    _rsi_limit = RSI_VETO_BREAKOUT_LONG if vol_ratio >= VOL_EMA_BYPASS_MULT else RSI_VETO_LONG
                    if rsi is not None and rsi > _rsi_limit:
                        print(f"[Top10 Breakout] {sym} LONG RSI veto ({rsi:.0f} > {_rsi_limit})")
                        continue
                else:  # SHORT
                    if rsi is not None and rsi < RSI_VETO_SHORT:
                        print(f"[Top10 Breakout] {sym} SHORT RSI veto ({rsi:.0f} < {RSI_VETO_SHORT})")
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
                margin = max(round(avail * 0.10, 2), 5.0)
                # FNG Awareness: FNG 10-20 + LONG → מרג'ין מוקטן ב-20% (Extreme Fear caution)
                if direction == 'LONG' and fng_v is not None and fng_v <= BREAKOUT_FNG_REDUCED_MARGIN_MAX:
                    margin = max(round(margin * BREAKOUT_FNG_REDUCED_MARGIN_MULT, 2), 5.0)
                    print(f"[Top10 Breakout] FNG={fng_v} ≤ {BREAKOUT_FNG_REDUCED_MARGIN_MAX} → margin reduced to ${margin:.2f}")
                open_breakout_trade(
                    symbol    = c['symbol'],
                    price     = c['price'],
                    margin    = margin,
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

            # ── Claude Sandbox Mode (Kill-Switch פעיל בלבד) ─────────────────
            sandbox_results = []
            if fng_v_loop < EXTREME_FEAR_THRESHOLD and bubble_watch_list:
                print(f"[Sandbox] Kill-Switch active (FNG={fng_v_loop}) — running educational Claude analysis...")
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
            if sym in local_by_sym:
                # Keep existing local metadata (sl/tp/score/strategy/...)
                rebuilt.append(local_by_sym[sym])
                continue

            # Import missing position with conservative defaults
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

        dropped = [s for s in local_by_sym if s not in live]
        active_trades = rebuilt

    save_active_trades()
    print(
        f"[SYNC] reconciled {len(live)} positions from Bitget "
        f"(kept={len(live) - len(imported)}, imported={len(imported)}, dropped={len(dropped)})",
        flush=True,
    )
    if imported:
        print(f"[SYNC] imported symbols: {imported}", flush=True)
    if dropped:
        print(f"[SYNC] dropped stale local symbols: {dropped}", flush=True)


# Bumping BOOTSTRAP_VERSION forces a one-time reset of wallet/trades/audit to the
# baseline below on the next startup. Increment manually whenever you want to wipe
# durable state (e.g. fresh capital, schema change). Stored in Object Storage so
# each version only ever resets once across all deploys.
BOOTSTRAP_VERSION = "v2_200usd_2026_05_15"


def maybe_bootstrap_baseline():
    """
    On first boot for a given BOOTSTRAP_VERSION, force the durable store to a
    fresh $200 / zero-trades / zero-audit baseline. Subsequent boots see the
    version marker and skip the reset, so live state is preserved.
    """
    marker = state_store.os_get('bootstrap_version')
    if marker == BOOTSTRAP_VERSION:
        print(f"[STATE] bootstrap up-to-date ({BOOTSTRAP_VERSION}) — keeping live state", flush=True)
        return

    print(
        f"[STATE] bootstrap {marker!r} → {BOOTSTRAP_VERSION!r}: forcing $200 baseline reset",
        flush=True,
    )
    fresh_wallet = {
        'balance':         STARTING_BALANCE,
        'starting':        STARTING_BALANCE,
        'total_pnl':       0.0,
        'trades_opened':   0,
        'total_wins':      0,
        'total_losses':    0,
        'locked_balance':  0.0,
        'available_balance': STARTING_BALANCE,
        'unrealized_pnl':  0.0,
        'equity_history':  [{'t': now_il().strftime('%m/%d %H:%M'), 'eq': STARTING_BALANCE}],
    }
    state_store.reset_state('wallet',         fresh_wallet,                                          WALLET_FILE)
    state_store.reset_state('active_trades',  {'updated': now_il().strftime('%H:%M:%S'), 'count': 0, 'trades': []}, ACTIVE_TRADES_FILE)
    state_store.reset_state('trade_audit',    {'updated': now_il().isoformat(timespec='seconds'), 'count': 0, 'trades': []}, AUDIT_LOG_FILE)
    state_store.os_set('bootstrap_version', BOOTSTRAP_VERSION)


def main():
    keep_alive()
    maybe_bootstrap_baseline() # ← אם זו הפעלה ראשונה לגרסה הזו — איפוס ל-$200
    load_wallet()              # ← טעינת ארנק וירטואלי (Object Storage → disk → default)
    load_active_trades()       # ← שחזור עסקאות פעילות (Object Storage → disk)
    _load_audit_log()          # ← שחזור Audit Log (Object Storage → disk)
    reconcile_with_exchange()  # ← בנייה מחדש של active_trades מהעמדות הפתוחות ב-Bitget

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

    if IS_DEPLOYED:
        send_msg(
            "🟢 *SYSTEM READY — Aggressive Hunter 2026*\n"
            f"{'─' * 30}\n\n"
            "💰 *יתרה:* $200\\.00 \\(איפוס מלא\\)\n"
            "📊 *P&L:* $0\\.00\n\n"
            "⚙️ *פרמטרים:*\n"
            "  📌 מרג'ין: *$50* | מינוף: *10×*\n"
            "  🎯 מקסימום עסקאות: *3*\n"
            "  🛑 SL: *2\\.5%* | TP1: *3%* \\(50%\\) | TP2: *10%*\n"
            "  📍 Trailing: *1%* מהשיא \\(מופעל ב-\\+1\\.5%\\)\n\n"
            "⚡ *לולאות פעילות:*\n"
            "  🔍 סריקה:         כל *60 דקות* ✅\n"
            "  📡 Top20 Breakout: כל *15 דקות* ✅\n"
            "  🫧 Bubble Watch:   כל *15 דקות* ✅\n"
            "  ⚡ High-Velocity:  כל *2 דקות* ✅\n"
            "  📍 מעקב SL/TP:    כל *60 שניות* ✅\n\n"
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
