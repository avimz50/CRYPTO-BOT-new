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
from zoneinfo import ZoneInfo
from keep_alive import keep_alive, app as flask_app
from flask import jsonify as flask_jsonify
import gdrive_reporter

# ── אזור זמן ישראל — ZoneInfo עובד גם ב-Production ──
_IL_TZ = ZoneInfo('Asia/Jerusalem')

def now_il() -> datetime:
    """מחזיר datetime נוכחי בשעון ישראל — עובד ב-dev וב-production."""
    return datetime.now(_IL_TZ)

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
})
print("[BOOT] ccxt exchange OK.", flush=True)

bot = telebot.TeleBot(os.environ['TELEGRAM_TOKEN'])
CHAT_ID = os.environ['CHAT_ID']
print("[BOOT] Telegram bot OK.", flush=True)

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
        prev_val = _fng_cache['value']
        _fng_cache.update({'value': int(d['value']), 'label': d['value_classification'], 'ts': now})
        if _fng_cache['value'] != prev_val:   # הדפס רק אם הערך השתנה
            print(f"[FNG] {prev_val} → {_fng_cache['value']} – {_fng_cache['label']}")
    except Exception as e:
        print(f"[FNG] שגיאת רענון: {e}")
    return _fng_cache['value'], _fng_cache['label']

# ─── Global Sentiment Thresholds ──────────────────────────────────────────────
EXTREME_FEAR_THRESHOLD = 13   # Kill-Switch: אין עסקאות חדשות בכלל
FEAR_THRESHOLD         = 30   # Fear Filter: RSI<30 ל-LONG + SL+1%
GREED_THRESHOLD        = 70   # Greed Filter: פוזיציה ×60% + BE@+2%
GREED_EARLY_BE_PCT     = 2.0  # % רווח להפעלת BE מוקדם בחמדנות
FEAR_EXTRA_SL_PCT      = 1.0  # % נוסף ל-SL בתנאי פחד

# ─── Daily Circuit Breaker ─────────────────────────────────────────────────────
DAILY_LOSS_LIMIT            = -30.0   # -$30 = 15% מ-$200 יתרת פתיחה
_daily_circuit_notified: bool = False  # מונע ריבוי הודעות על אותו אירוע

_last_sentiment_action: str = ""

def sentiment_check(context: str = "scan"):
    """
    בודק את מצב הסנטימנט.
    מחזיר (fng_v:int, label:str, action:str).
    מדפיס רק כשה-regime משתנה (Low-Resource mode).
    """
    global _last_sentiment_action
    fng_v, lbl = get_fear_greed()
    if fng_v < EXTREME_FEAR_THRESHOLD:
        action = f"KILL-SWITCH (FNG={fng_v})"
    elif fng_v <= FEAR_THRESHOLD:
        action = f"FEAR FILTER (FNG={fng_v})"
    elif fng_v >= GREED_THRESHOLD:
        action = f"GREED FILTER (FNG={fng_v})"
    else:
        action = f"NEUTRAL (FNG={fng_v})"
    if action.split('(')[0] != _last_sentiment_action.split('(')[0]:
        print(f"[Sentiment] {_last_sentiment_action or 'START'} → {action} [{context}]")
        _last_sentiment_action = action
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
# ═══════════════════════════════════════════════════════════════

SECTOR_MAP: dict[str, str] = {
    # Layer 1
    'ETH': 'L1', 'SOL': 'L1', 'AVAX': 'L1', 'APT': 'L1', 'NEAR': 'L1',
    'SUI': 'L1', 'SEI': 'L1', 'ATOM': 'L1', 'DOT': 'L1', 'ADA': 'L1',
    'TRX': 'L1', 'TON': 'L1', 'FTM': 'L1', 'ONE': 'L1', 'ALGO': 'L1',
    # Layer 2
    'MATIC': 'L2', 'ARB': 'L2', 'OP': 'L2', 'IMX': 'L2', 'ZK': 'L2',
    'STRK': 'L2', 'MANTA': 'L2', 'BLAST': 'L2', 'METIS': 'L2',
    # DeFi
    'UNI': 'DeFi', 'AAVE': 'DeFi', 'CRV': 'DeFi', 'MKR': 'DeFi',
    'COMP': 'DeFi', 'SNX': 'DeFi', 'BAL': 'DeFi', 'SUSHI': 'DeFi',
    'JUP': 'DeFi', 'DYDX': 'DeFi', 'GMX': 'DeFi', 'ENA': 'DeFi',
    # AI & Data
    'FET': 'AI', 'AGIX': 'AI', 'OCEAN': 'AI', 'RENDER': 'AI', 'TAO': 'AI',
    'WLD': 'AI', 'ALT': 'AI', 'GRT': 'AI',
    # Gaming & Metaverse
    'AXS': 'Gaming', 'SAND': 'Gaming', 'MANA': 'Gaming', 'ENJ': 'Gaming',
    'GALA': 'Gaming', 'ILV': 'Gaming', 'YGG': 'Gaming',
    # Meme
    'DOGE': 'Meme', 'SHIB': 'Meme', 'PEPE': 'Meme', 'FLOKI': 'Meme',
    'BONK': 'Meme', 'WIF': 'Meme', 'BOME': 'Meme',
    # Exchange Tokens
    'BNB': 'CEX', 'OKB': 'CEX', 'CRO': 'CEX', 'KCS': 'CEX', 'GT': 'CEX',
    'HT': 'CEX', 'BGB': 'CEX',
    # BTC Ecosystem
    'BTC': 'BTC', 'WBTC': 'BTC', 'STX': 'BTC', 'ORDI': 'BTC',
    # Oracle / Data
    'LINK': 'Oracle', 'BAND': 'Oracle', 'TRB': 'Oracle', 'API3': 'Oracle',
}


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
# Claude AI Final Filter — GO / NO-GO per signal
# ═══════════════════════════════════════════════════════════════

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
    מסנן סופי מוסדי: שולח נתוני האיתות ל-Claude 3 Haiku.
    מחזיר (go: bool, reason: str).
    breakdown הוא STRING (פלט של score_symbol).
    אם המפתח לא מוגדר / שגיאת API → GO כברירת מחדל (לא חוסם עסקאות).
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        print(f"  [Claude] ANTHROPIC_API_KEY לא מוגדר — דילוג על פילטר")
        return True, "Claude filter skipped (no API key)"

    try:
        import anthropic as _anthropic

        # breakdown הוא string מ-score_symbol — משתמשים ישירות
        breakdown_str = str(breakdown) if breakdown else "N/A"

        # RSI context בכל הטיים-פריימים
        rsi_parts = []
        if rsi_4h  is not None: rsi_parts.append(f"4H={rsi_4h:.1f}")
        if rsi_1h  is not None: rsi_parts.append(f"1H={rsi_1h:.1f}")
        if rsi_15m is not None: rsi_parts.append(f"15m={rsi_15m:.1f}")
        rsi_str = "  |  ".join(rsi_parts) if rsi_parts else "N/A"

        vol_str = f"{volume_ratio:.2f}× avg" if volume_ratio is not None else "N/A"
        chg_str = f"{change_24h:+.2f}%" if change_24h is not None else "N/A"

        prompt = (
            f"You are a senior crypto quant risk validator at a professional trading desk. "
            f"Make a strict GO/NO-GO decision on this trade signal.\n\n"
            f"=== SIGNAL ===\n"
            f"Symbol: {symbol}  |  Direction: {direction}  |  Entry TF: {timeframe}\n"
            f"Score: {score}/100  |  Entry Price: {price:.6g}\n\n"
            f"=== MARKET CONTEXT ===\n"
            f"BTC Regime: {btc_regime}\n"
            f"Fear & Greed Index: {fng_v} ({fng_lbl})\n"
            f"24h Price Change: {chg_str}\n"
            f"Bot P&L today: ${daily_pnl:.2f}  (circuit breaker at -$30)\n\n"
            f"=== TECHNICAL DATA ===\n"
            f"Multi-TF RSI: {rsi_str}\n"
            f"Volume vs 10-bar avg: {vol_str}\n"
            f"Score Breakdown: {breakdown_str}\n\n"
            f"=== HARD REJECTION RULES ===\n"
            f"- Reject if any RSI > 72 on a LONG (overbought confirmation)\n"
            f"- Reject if any RSI < 28 on a SHORT (oversold confirmation)\n"
            f"- Reject if 24h change > 15% (chasing a pump/dump)\n"
            f"- Reject if volume < 0.8× avg (no real participation)\n\n"
            f"=== RISK PARAMETERS ===\n"
            f"$500 notional | $50 margin | 10× leverage\n"
            f"SL: 3.5% | TP1: 5.0% (50% close) | TP Full: 10.5% (RR 1:3)\n\n"
            f"Respond with EXACTLY one line: 'GO: <1 sentence reason>' or 'NO-GO: <1 sentence reason>'"
        )

        client = _anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=100,
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
        print(f"  [Claude] שגיאה: {e} — ממשיך ללא פילטר (GO)")
        return True, f"Claude error (fallback GO): {str(e)[:60]}"


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


# --- זיהוי סביבה (Dev vs Production) ---
# ב-Replit Deployments מוגדר REPLIT_DEPLOYMENT=1 אוטומטית.
# בסביבת הפיתוח (workspace) הוא לא מוגדר → IS_DEPLOYED=False.
# כך הסריקה האוטומטית רצה רק ב-prod, ואין הודעות כפולות בטלגרם.
IS_DEPLOYED = bool(os.environ.get('REPLIT_DEPLOYMENT', ''))

# --- פרמטרי מינוף (דמו) ---
LEVERAGE       = 10          # מינוף 10x
MARGIN         = 50          # בטחון ($) לכל עסקה
POSITION_SIZE  = MARGIN * LEVERAGE   # $500 נשלט

# רשימה למעקב אחרי עסקאות דמו פתוחות
active_trades      = []
trades_lock        = threading.RLock()   # מגן מ-race conditions בין Threads

# לוג עסקאות סגורות (48 שעות אחרונות) לדוח ה-Drive
closed_trades_log  = []

# ─── Watch List — מעקב מטבעות ספציפיים כל 15 דקות ───────────────────────────
# מבנה: { 'SOL/USDT': {'direction':'LONG','added_at':..., 'last_score':0, 'expires_at':...} }
watch_list: dict = {}
watch_lock = threading.Lock()

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
        'updated': now_il().strftime('%H:%M:%S'),
        'count':   len(snapshot),
        'trades':  snapshot,
    })

@flask_app.route('/api/wallet')
def api_wallet():
    data = dict(wallet)
    locked              = sum(t.get('margin', MARGIN) for t in active_trades)
    unrealized          = _get_unrealized_pnl()
    data['equity']          = _get_equity()
    data['available_balance']= round(wallet.get('balance', STARTING_BALANCE), 2)
    data['locked_balance']  = round(locked, 2)
    data['unrealized_pnl']  = unrealized
    data['active_count']    = len(active_trades)
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
    """Floating P&L של כל העסקאות הפתוחות לפי current_price."""
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

def _get_equity():
    """Total equity = available_balance + locked_margin + unrealized_pnl."""
    locked     = sum(t.get('margin', MARGIN) for t in active_trades)
    unrealized = _get_unrealized_pnl()
    return round(wallet.get('balance', STARTING_BALANCE) + locked + unrealized, 2)

def _append_equity_point():
    hist = wallet.setdefault('equity_history', [])
    hist.append({'t': now_il().strftime('%m/%d %H:%M'), 'eq': _get_equity()})
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
            'equity_history': [{'t': now_il().strftime('%m/%d %H:%M'), 'eq': STARTING_BALANCE}],
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
    _append_equity_point()
    save_wallet()


# ─────────────────────────────────────────────────────────────────
#  place_order() — פונקציה מאוחדת לרישום עסקה
#  כל נתיבי הפתיחה (Auto / Manual / Scalp / SOL) מדווחים דרכה.
# ─────────────────────────────────────────────────────────────────
def place_order(trade: dict, margin: float = MARGIN) -> bool:
    """
    רושם עסקה חדשה:
      • מוסיף ל-active_trades (עם trades_lock)
      • מנכה מרג'ין מהארנק (wallet_deduct)
      • שומר active_trades ל-disk (save_active_trades)
    מחזיר True בהצלחה.
    הבודק-יתרה ובדיקת-כפילות הם אחריות הקורא לפני הקריאה.
    """
    with trades_lock:
        active_trades.append(trade)
    wallet_deduct(margin)
    save_active_trades()
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
        'closed_at':    now_il().isoformat(timespec='seconds'),
    }
    closed_trades_log.append(record)
    # שמור רק 48 שעות אחרונות
    cutoff = now_il().timestamp() - 48 * 3600
    closed_trades_log = [
        t for t in closed_trades_log
        if datetime.fromisoformat(t['closed_at']).timestamp() >= cutoff
    ]

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
    """שומר את רשימת העסקאות הפעילות לקובץ JSON לדאשבורד."""
    try:
        with trades_lock:
            snapshot = list(active_trades)
        data = {
            'updated': now_il().strftime('%H:%M:%S'),
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
        if VERBOSE_LOG:
            print(f"[BTC] Regime={regime} price={price:.0f} EMA50={ema50:.0f} ({pct:+.2f}%)")
        return regime
    except Exception as e:
        print(f"BTC regime check failed: {e} — defaulting to NEUTRAL")
        return 'NEUTRAL'

# ═══════════════════════════════════════════════════════════════
# מנוע ניקוד מקצועי — Professional Scoring System
# ═══════════════════════════════════════════════════════════════

MIN_SCORE  = 90   # סף מינימום לפתיחת עסקה (90 = alignment כמעט מושלם)
MAX_TRADES = 5    # מקסימום עסקאות פתוחות במקביל
RSI_VETO_LONG  = 70   # Anti-FOMO: RSI מעל 70 = לא קונים (overbought ceiling)
RSI_VETO_SHORT = 28   # RSI מתחת זה = לא מוכרים (oversold)
EMA_PROXIMITY_PCT = 2.5  # מחיר חייב להיות תוך 2.5% מ-EMA200 (Anti-Chase)
BE_BUFFER_PCT  = 2.0  # % עלייה/ירידה לפני הזזת SL ל-Break Even (50% מ-TP1=5%)
TRAIL_PCT      = 2.5  # % Trailing Stop מהשיא
SL_PCT_FIXED   = 3.5  # % SL קבוע (3h chart)
TP1_PCT_FIXED  = 5.0  # % TP1 קבוע — סגירת 50%
TP_PCT_FIXED   = 10.5 # % TP מלא — RR 1:3 (3 × 3.5%)

# ─── Sniper Exception — Override Kill-Switch under STRICT conditions ───────────
SNIPER_MIN_SCORE   = 95    # ציון מינימום 4H — מעל 90 הרגיל
SNIPER_EMA_PCT     = 5.0   # מחיר חייב תוך 5% מ-EMA200 (אין רדיפת פאמפים)
SNIPER_VOL_MIN     = 2.5   # Volume לפחות 2.5× הממוצע — אישור כניסה
SNIPER_MARGIN_MULT = 0.5   # Half-Size Entry: 50% מגודל הפוזיציה הרגיל

# ── Scalp Mode (High-Volatility Mean Reversion, FNG < EXTREME_FEAR_THRESHOLD) ──
SCALP_LEVERAGE          = 5
SCALP_MARGIN            = 15.0
SCALP_POS_SIZE          = SCALP_MARGIN * SCALP_LEVERAGE   # $75 controlled
SCALP_TP_PCT            = 3.0    # 3% profit target
SCALP_SL_PCT            = 1.5    # 1.5% stop loss
SCALP_MAX_DURATION_MIN  = 60     # force-close after 60 minutes
MAX_SCALP_TRADES        = 2      # max concurrent scalp trades
SCALP_BUBBLE_MIN_PCT    = 30.0   # scalp-short: coin up > 30% in 24h
SCALP_CRASH_MIN_PCT     = 20.0   # quick-long: coin down > 20% in 2h
SCALP_SHORT_RSI_THRESH  = 82.0   # RSI 15m must be > 82 for scalp-short
SCALP_LONG_RSI_THRESH   = 18.0   # RSI 15m must be < 18 for quick-long
SCALP_BOUNCE_PCT        = 1.0    # price must bounce 1% from 2h low
SCALP_SCAN_INTERVAL     = 300    # 5 min between scalp scans (normal)
SCALP_SCAN_INTERVAL_WAIT= 600    # 10 min when Kill-Switch active (resource savings)

# ── Top 10 Breakout Scan — /top10 command ─────────────────────────────────────
TOP10_SYMBOLS = [
    # Top-10 Market Cap
    'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT',
    'DOGE/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT', 'TRX/USDT',
    # Top 11-20 Expansion
    'ATOM/USDT', 'NEAR/USDT', 'APT/USDT', 'SUI/USDT', 'ARB/USDT',
    'OP/USDT',   'INJ/USDT',
    # Sector Bonus — AI + RWA
    'FET/USDT', 'RNDR/USDT', 'ONDO/USDT',
]

# Sector-priority coins: get moved to front of candidates list
SECTOR_PRIORITY_SYMBOLS = {'FET/USDT', 'RNDR/USDT', 'ONDO/USDT'}

# ── Low-Resource Logging ──────────────────────────────────────────────────────
# False = only critical events (Entry, Exit, Errors) are printed.
# True  = verbose per-symbol scoring breakdown (debugging only).
VERBOSE_LOG             = False


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
            if VERBOSE_LOG:
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
            if VERBOSE_LOG:
                print(f"  [{symbol}] NaN in indicators")
            return 0, "NaN values", 0

        # ════════════════════════════════════════════════
        # ANTI-FOMO HARD VETOES (לפני כל ניקוד)
        # ════════════════════════════════════════════════

        # ── וטו 1: EMA200 Proximity — Anti-Chase ──
        ema_gap_pct = (price - ema200_v) / ema200_v * 100
        if direction == 'LONG' and ema_gap_pct > EMA_PROXIMITY_PCT:
            return 0, f"EMA200 chase veto ({ema_gap_pct:.1f}% above EMA200)", atr_v
        if direction == 'SHORT' and ema_gap_pct < -EMA_PROXIMITY_PCT:
            return 0, f"EMA200 chase veto ({abs(ema_gap_pct):.1f}% below EMA200)", atr_v

        # ── וטו 2: Wick Rejection — Anti-False Breakout ──
        last_c  = df_3h.iloc[-2]
        c_body  = abs(last_c['close'] - last_c['open'])
        c_upper = last_c['high'] - max(last_c['close'], last_c['open'])
        c_lower = min(last_c['close'], last_c['open']) - last_c['low']
        if direction == 'LONG' and c_upper > c_body and c_body > 0:
            return 0, f"Wick rejection LONG (upper wick {c_upper:.4g} > body {c_body:.4g})", atr_v
        if direction == 'SHORT' and c_lower > c_body and c_body > 0:
            return 0, f"Wick rejection SHORT (lower wick {c_lower:.4g} > body {c_body:.4g})", atr_v

        # ════════════════════════════════
        # 1. TREND — 30 נקודות
        # ════════════════════════════════
        t1h  = price > ema200_v  if direction == 'LONG' else price < ema200_v
        t15m = price > ema200_15v if direction == 'LONG' else price < ema200_15v

        t_pts = 0
        if t1h:        t_pts += 20
        if t1h and t15m: t_pts += 10
        score += t_pts
        parts.append(f"Trend={t_pts}/30")
        if VERBOSE_LOG:
            print(f"  [{symbol}] {direction} | Trend={t_pts} "
                  f"(4H={'✓' if t1h else '✗'} 1H={'✓' if t15m else '✗'})")

        # ════════════════════════════════
        # 2. MOMENTUM (MACD) — 25 נקודות
        # ════════════════════════════════
        macd_ok = (macd_v > sig_v)  if direction == 'LONG' else (macd_v < sig_v)
        hist_ok = (hist_v > hist_p) if direction == 'LONG' else (hist_v < hist_p)

        m_pts = 0
        if macd_ok: m_pts += 15
        if hist_ok: m_pts += 10
        score += m_pts
        parts.append(f"MACD={m_pts}/25")
        if VERBOSE_LOG:
            print(f"  [{symbol}] {direction} | MACD={m_pts} "
                  f"(aligned={'✓' if macd_ok else '✗'} hist={'✓' if hist_ok else '✗'})")

        # ════════════════════════════════
        # 3. RSI STRENGTH — 20 נקודות
        # ════════════════════════════════

        # וטו קשה — RSI קיצוני = פסילה מוחלטת
        if direction == 'LONG' and rsi_v > RSI_VETO_LONG:
            return 0, f"Anti-FOMO RSI veto ({rsi_v:.1f} > {RSI_VETO_LONG} ceiling)", atr_v
        if direction == 'SHORT' and rsi_v < RSI_VETO_SHORT:
            return 0, f"RSI veto ({rsi_v:.1f} oversold)", atr_v

        if direction == 'LONG':
            rsi_ideal = 50 <= rsi_v <= 65
            rsi_ok    = 45 <= rsi_v <= 70
        else:
            rsi_ideal = 35 <= rsi_v <= 50
            rsi_ok    = 30 <= rsi_v <= 55

        r_pts = 20 if rsi_ideal else (10 if rsi_ok else 0)
        score += r_pts
        parts.append(f"RSI={r_pts}/20(={rsi_v:.0f})")
        if VERBOSE_LOG:
            print(f"  [{symbol}] {direction} | RSI={r_pts} (rsi={rsi_v:.1f})")

        # ════════════════════════════════
        # 4. BOLLINGER + VOLUME — 15 נקודות
        # ════════════════════════════════
        bb_ok  = (price > bb_mid) if direction == 'LONG' else (price < bb_mid)
        vol_ok = vol_rat >= 1.2

        b_pts = 0
        if bb_ok:  b_pts += 10
        if vol_ok: b_pts += 5
        score += b_pts
        parts.append(f"BB+Vol={b_pts}/15")
        if VERBOSE_LOG:
            print(f"  [{symbol}] {direction} | BB+Vol={b_pts} "
                  f"(bb={'✓' if bb_ok else '✗'} vol×{vol_rat:.1f}={'✓' if vol_ok else '✗'})")

        # ════════════════════════════════
        # 5. CANDLES (נרות יפניים) — 10 נקודות
        # ════════════════════════════════
        c_pts, pattern_name = score_candles(df_3h, direction)
        score += c_pts
        parts.append(f"Candles={c_pts}/10({pattern_name})")
        if VERBOSE_LOG:
            print(f"  [{symbol}] {direction} | Candles={c_pts} ({pattern_name})")

        # ════════════════════════════════════
        # 6. FEAR & GREED INDEX — ±5 נקודות
        # ════════════════════════════════════
        fng_v, fng_lbl = get_fear_greed()
        if direction == 'LONG':
            if   fng_v < 25: fng_adj = +5
            elif fng_v < 45: fng_adj = +2
            elif fng_v > 75: fng_adj = -5
            elif fng_v > 55: fng_adj = -2
            else:            fng_adj =  0
        else:
            if   fng_v > 75: fng_adj = +5
            elif fng_v > 55: fng_adj = +2
            elif fng_v < 25: fng_adj = -5
            elif fng_v < 45: fng_adj = -2
            else:            fng_adj =  0
        score = max(0, min(100, score + fng_adj))
        sign  = f"+{fng_adj}" if fng_adj >= 0 else str(fng_adj)
        parts.append(f"FNG={fng_v}({sign})")
        if VERBOSE_LOG:
            print(f"  [{symbol}] {direction} | FNG={fng_v} adj={sign}")

        breakdown = " | ".join(parts) + f"  →  TOTAL={score}/100"
        # Only print when a signal qualifies (score ≥ threshold)
        if score >= MIN_SCORE:
            print(f"[Score] 🟢 {symbol} {direction} SCORE={score}/100 | {breakdown}")
        elif VERBOSE_LOG:
            print(f"[Score] 🔴 {symbol} {direction} {score}/100 — skip")
        return score, breakdown, atr_v

    except Exception as e:
        print(f"[Score] ⚠️ {symbol} error: {e}")
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
                    rsi=None, ema200=None, fng_v=None, sniper_mode=False):
    """
    פותח עסקת דמו עם SL/TP קבועים.
    SL=3.5% | TP1=5% (סגירת 50%) | TP=10.5% (RR 1:3) | BE=2%
    timeframe: '4H' / '1H' — גרף הכניסה שנבחר אדפטיבית
    fng_v: ערך FNG שכבר חושב ב-scan (כדי לא לשאול שוב)
    sniper_mode: True → Half-Size Entry (50% מגודל הפוזיציה הרגיל)
    """
    # ── Sniper Mode: מרג'ין מינימלי ──────────────────────────────────────────
    effective_margin = MARGIN * SNIPER_MARGIN_MULT if sniper_mode else MARGIN

    # בדיקת יתרה — אין לפתוח עסקה אם אין מספיק כסף
    if wallet.get('balance', STARTING_BALANCE) < effective_margin:
        print(f"WALLET: insufficient balance (${wallet.get('balance', 0):.2f}) — skipping {symbol}")
        send_msg(f"⚠️ *יתרה נמוכה* — אין מספיק להפקדת מרג'ין\nנדרש: ${effective_margin:.0f} | יש: ${wallet.get('balance', 0):.2f}")
        return

    # ── Sentiment Rules: position size & SL ──────────────────────────────────
    if fng_v is None:
        fng_v, _, _ = sentiment_check("open_trade")
    pos_size = POSITION_SIZE * SNIPER_MARGIN_MULT if sniper_mode else POSITION_SIZE   # Sniper=Half-Size
    sl_pct   = SL_PCT_FIXED    # 3.5%
    if fng_v >= GREED_THRESHOLD and not sniper_mode:
        pos_size = round(POSITION_SIZE * 0.60)   # ×60% — Greed Filter (לא חל על Sniper)
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
        'opened_at':       now_il().isoformat(timespec='seconds'),
        'pos_size':        pos_size,        # נשמר לחישובי P&L בניהול עסקאות
        'margin':          effective_margin, # מרג'ין בפועל ($50 רגיל / $25 Sniper)
        'fng_at_entry':    fng_v,           # FNG בזמן הכניסה
        'sniper':          sniper_mode,     # האם נפתח כ-Sniper Exception
    }
    place_order(trade, effective_margin)

    dir_header = get_direction_header(direction)
    tip        = get_momentum_tip(direction)
    emoji      = "🟢" if direction == 'LONG' else "🔴"
    score_bar  = "█" * (score // 10) + "░" * (10 - score // 10)

    # הסבר על הטיים-פריים שנבחר
    if not tf_reason:
        tf_reason = f"טרנד חזק ב-{timeframe}" if timeframe == '4H' else f"פריצה ב-{timeframe} (4H חלש)"
    tf_icon = "📊" if timeframe == '4H' else ("⏱️" if timeframe == '1H' else "⚡")

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
    if sniper_mode:
        msg += f"🎯 *Sniper Entry* — מרג'ין: ${effective_margin:.0f} \\(Half\\-Size\\) · נשלט: ${pos_size:.0f}\n"
    else:
        msg += f"💰 בטחון: ${effective_margin:.0f} · נשלט: ${pos_size:.0f}\n"
    msg += f"{'─' * 26}\n"
    msg += f"📊 *Expected P&L*\n"
    msg += f"✅ Est\\. Profit at TP: *+${est_profit_tp}*\n"
    msg += f"❌ Est\\. Loss at SL:   *\\-${est_loss_sl}*\n"
    msg += f"⚖️ Risk / Reward: *1 : {rr_ratio}*\n"
    msg += f"{'─' * 26}\n"
    msg += _wallet_opened_summary() + "\n\n"
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
    global active_trades, daily_stats, _daily_circuit_notified

    if daily_stats['date'] != date.today():
        daily_stats = {
            'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'date': date.today(),
            'close_reasons': {'TP': 0, 'TP1+Trail': 0, 'Trailing': 0, 'SL': 0, 'BE': 0, 'Manual': 0},
        }
        _daily_circuit_notified = False  # איפוס Circuit Breaker עם פתיחת יום חדש

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
            # SCALP PHASE — SL / TP / Time exit
            # ════════════════════════════════════════════
            if trade.get('phase') == 'scalp':
                raw_pnl_pct = (current_price - entry) / entry * 100
                scalp_pnl_pct = raw_pnl_pct if direction == 'LONG' else -raw_pnl_pct
                scalp_pnl_usd = round(SCALP_POS_SIZE * scalp_pnl_pct / 100, 2)

                elapsed_min = (time.time() - trade.get('scalp_opened_ts', time.time())) / 60

                tp_hit_s = (direction == 'LONG' and current_price >= trade['tp']) or \
                           (direction == 'SHORT' and current_price <= trade['tp'])
                sl_hit_s = (direction == 'LONG' and current_price <= trade['sl']) or \
                           (direction == 'SHORT' and current_price >= trade['sl'])
                time_exp = elapsed_min >= SCALP_MAX_DURATION_MIN

                if tp_hit_s or sl_hit_s or time_exp:
                    if tp_hit_s:
                        close_reason = 'Scalp-TP'
                        icon  = "✅"
                        label = f"🎯 TP נגע \\(\\+{SCALP_TP_PCT}%\\)"
                        daily_stats['wins'] += 1
                    elif sl_hit_s:
                        close_reason = 'Scalp-SL'
                        icon  = "❌"
                        label = f"🛑 SL נגע \\(\\-{SCALP_SL_PCT}%\\)"
                        daily_stats['losses'] += 1
                    else:
                        close_reason = 'Scalp-Time'
                        icon  = "⏱"
                        label = f"⏱ פג תוקף \\({elapsed_min:.0f} דקות\\)"
                        if scalp_pnl_usd >= 0:
                            daily_stats['wins'] += 1
                        else:
                            daily_stats['losses'] += 1

                    daily_stats['total_pnl'] += scalp_pnl_usd
                    wallet_credit(scalp_pnl_usd, SCALP_MARGIN)
                    _log_closed_trade(trade, close_reason, scalp_pnl_usd, current_price)
                    eq    = _get_equity()
                    emoji = "🟢" if direction == 'LONG' else "🔴"
                    pnl_icon = "📈" if scalp_pnl_usd >= 0 else "📉"
                    send_msg(
                        f"⚡ *Scalp סגור — {sym.replace('/USDT','')} {emoji}*\n"
                        f"{label}\n\n"
                        f"כניסה: `{entry:.6g}` → יציאה: `{current_price:.6g}`\n"
                        f"{pnl_icon} *P&L: ${scalp_pnl_usd:+.2f}* \\({scalp_pnl_pct:+.2f}%\\)\n"
                        f"💼 {SCALP_LEVERAGE}x · ${SCALP_MARGIN:.0f} מרג'ין\n"
                        f"📊 Equity: `${eq:.2f}` | יתרה: `${wallet.get('balance', 0):.2f}`\n"
                        f"{pnl_icon} סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    with trades_lock:
                        active_trades.remove(trade)
                    save_active_trades()
                continue   # skip all normal phase logic for scalp trades

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
                    wallet_credit(pnl_usd, trade.get('margin', MARGIN))
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
                        wallet_credit(0, trade.get('margin', MARGIN))   # מרג'ין חוזר, ללא P&L
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
                    wallet_credit(total, trade.get('margin', MARGIN))
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
                    wallet_credit(total, trade.get('margin', MARGIN))
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

@bot.message_handler(commands=['ping'])
def handle_ping(message):
    now        = now_il().strftime('%d/%m/%Y %H:%M:%S')
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
        'atr':             0.0,
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
    send_msg(
        f"✅ *SOL/USDT נרשמה כעסקה פעילה* 🟢\n\n"
        f"💵 כניסה: `{price:.3f}`\n"
        f"🛑 SL: `{sl:.3f}` (-{sl_pct}%)\n"
        f"🎯 TP: `{tp:.3f}` (+{tp_pct}%)\n"
        f"💼 {LEVERAGE}x · ${MARGIN:.0f} מרג'ין · ${POSITION_SIZE:.0f} נשלט\n\n"
        + _wallet_opened_summary() + "\n\n"
        + "_מעקב SL/TP פעיל — יתרה תעודכן אוטומטית_"
    )
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
        f"  /report     — דוח יומי מלא\n"
        f"  /scanreport — דוח סריקה אחרון\n"
        f"  /ping       — בדיקת חיות הבוט\n\n"
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
        f"  /top10     — סריקת Breakout על 20 מטבעות (כולל AI: FET/RNDR, RWA: ONDO)\n"
        f"  /fillslots — מלא slots פנויים מ-20 מטבעות (LONG/SHORT לפי FNG+BTC)\n"
        f"  /fillslots ETH FET RNDR — פתח מטבעות ספציפיים (AI/RWA קודמים)\n"
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
            ks_note_m = " 🔒 Kill\\-Switch" if fng_v_m < EXTREME_FEAR_THRESHOLD else ""
            send_msg(
                f"✅ *סריקה ידנית הושלמה*\n\n"
                f"🔍 נסרקו: *{total_scanned}* מטבעות\n"
                f"📊 איתותים: *{signals_found}*\n"
                f"{fng_emoji_m} Fear & Greed: *{fng_v_m}* — _{fng_lbl_m}_{ks_note_m}\n"
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
                              bubble_watch=bubble_watch_m,
                              sandbox_analysis=sandbox_m)
        except Exception as e:
            send_msg(f"❌ שגיאה בסריקה: {e}")
        finally:
            _scan_running = False

    threading.Thread(target=run_manual_scan, daemon=True).start()


def start_telegram_polling():
    """
    Polling הטלגרם:
    - DEV mode: לא מפעיל polling בכלל — Production bot מטפל בפקודות.
      send_msg() עובד תמיד (HTTP POST ישיר, לא דורש polling).
    - PROD mode: המתנה של 70 שניות + delete_webhook לפני polling,
      כדי להבטיח שה-instance הישן הסתיים לפני שמתחילים.
    """
    is_deployed = bool(os.environ.get('REPLIT_DEPLOYMENT', ''))

    if not is_deployed:
        print(
            "[DEV] Telegram polling SKIPPED — Production bot handles commands. "
            "send_msg() active (send-only mode)."
        )
        return   # לא מתחיל polling ב-dev — אין קונפליקט 409

    # נקה כל webhook קיים ו-pending updates לפני שמתחילים
    try:
        bot.delete_webhook(drop_pending_updates=True)
        print("[PROD] Webhook cleared, pending updates dropped.")
    except Exception as e:
        print(f"[PROD] delete_webhook error (non-fatal): {e}")

    print("[PROD] Starting Telegram polling now...")
    consecutive_409 = 0

    while True:
        try:
            bot.polling(non_stop=False, timeout=30, long_polling_timeout=30)
            consecutive_409 = 0
        except Exception as e:
            err_str = str(e)
            if '409' in err_str:
                consecutive_409 += 1
                # המתנה קצרה — instance ישן מסתיים תוך ~10-20 שניות
                wait = min(60, 15 * consecutive_409)
                print(
                    f"⚠️  Telegram 409 — instance conflict. "
                    f"ממתין {wait}s לפני retry #{consecutive_409}..."
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

    # ── Sentiment Kill-Switch (Extreme Fear < EXTREME_FEAR_THRESHOLD) ────────
    fng_v_scan, fng_lbl_scan, fng_action = sentiment_check("scan")
    kill_switch_active = fng_v_scan < EXTREME_FEAR_THRESHOLD
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

    found = 0
    for candidate in candidates:
        if len(active_trades) >= MAX_TRADES:
            print(f"Max trades ({MAX_TRADES}) reached — skipping rest of batch")
            break
        symbol = candidate['symbol']
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
                    open_demo_trade(
                        symbol, price, f"Sniper Exception: {sniper_reason}",
                        df_4h, direction=direction,
                        score=score_4h, atr=atr,
                        timeframe='4H', tf_reason='Sniper Kill-Switch Override',
                        fng_v=fng_v_scan, sniper_mode=True,
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

                # ── Claude AI Final Filter ───────────────────────────────────────
                claude_go, claude_reason = claude_filter(
                    symbol=symbol, direction=direction, score=score,
                    breakdown=breakdown, price=price, timeframe=chosen_tf,
                    btc_regime=btc_regime, fng_v=fng_v_scan, fng_lbl=fng_lbl_scan,
                    rsi_4h=_rsi_4h, rsi_1h=_rsi_1h, rsi_15m=_rsi_15m,
                    volume_ratio=_vol_ratio, change_24h=_change_24h,
                    daily_pnl=daily_stats.get('total_pnl', 0.0),
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
            if fng_v < EXTREME_FEAR_THRESHOLD:
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
    פותח עסקת Scalp — מינוף 5x, מרג'ין $15, SL 1.5%, TP 3%, תוקף 60 דקות.
    עוקפת את Kill-Switch כי היא Mean Reversion ולא trend-following.
    """
    global active_trades

    with trades_lock:
        if any(t['symbol'] == symbol for t in active_trades):
            print(f"SCALP: {symbol} already in active_trades — skip")
            return
        scalp_count = sum(1 for t in active_trades if t.get('scalp'))
    if scalp_count >= MAX_SCALP_TRADES:
        print(f"SCALP: max scalp trades ({MAX_SCALP_TRADES}) reached — skip {symbol}")
        return
    if wallet.get('balance', 0) < SCALP_MARGIN:
        print(f"SCALP: insufficient balance (${wallet.get('balance', 0):.2f}) — skip {symbol}")
        return

    sl_dist = price * SCALP_SL_PCT / 100
    tp_dist = price * SCALP_TP_PCT / 100
    if direction == 'LONG':
        sl_price = round(price - sl_dist, 8)
        tp_price = round(price + tp_dist, 8)
    else:
        sl_price = round(price + sl_dist, 8)
        tp_price = round(price - tp_dist, 8)

    trade = {
        'symbol':          symbol,
        'entry':           price,
        'sl':              sl_price,
        'tp':              tp_price,
        'tp1':             tp_price,
        'be_lvl':          tp_price,
        'sl_pct':          SCALP_SL_PCT,
        'tp_pct':          SCALP_TP_PCT,
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
        'pos_size':        SCALP_POS_SIZE,
        'margin':          SCALP_MARGIN,
        'fng_at_entry':    None,
        'sniper':          False,
        'scalp':           True,
        'scalp_opened_ts': time.time(),
    }
    place_order(trade, SCALP_MARGIN)

    emoji     = "🟢" if direction == 'LONG' else "🔴"
    dir_label = "Quick-Long (Dip Buy)" if direction == 'LONG' else "Scalp-Short (Bubble)"
    send_msg(
        f"⚡ *{dir_label}: {symbol.replace('/USDT', '')} {emoji}*\n"
        f"_High Volatility Mode — Mean Reversion_\n\n"
        f"💵 כניסה: `{price:.6g}`\n"
        f"🛑 SL: `{sl_price:.6g}` (-{SCALP_SL_PCT}%)\n"
        f"🎯 TP: `{tp_price:.6g}` (+{SCALP_TP_PCT}%)\n"
        f"⏱ תוקף: {SCALP_MAX_DURATION_MIN} דקות\n"
        f"💼 {SCALP_LEVERAGE}x · ${SCALP_MARGIN:.0f} מרג'ין · ${SCALP_POS_SIZE:.0f} נשלט\n\n"
        + _wallet_opened_summary() + "\n\n"
        + f"📋 _{reason}_"
    )
    print(f"SCALP {direction}: {symbol} @ {price:.6g} | SL={sl_price:.6g} TP={tp_price:.6g} | {reason}")


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

            print(f"[Scalp] FNG={fng_v} — scanning...")
            tickers = exchange.fetch_tickers()

            # ── SCALP-SHORT: Bubble Watch — עלה >30% ב-24h ───────────────────────
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
            for sym, ticker in tickers.items():
                if not sym.endswith('/USDT'):
                    continue
                with trades_lock:
                    if sum(1 for t in active_trades if t.get('scalp')) >= MAX_SCALP_TRADES:
                        break
                    if any(t['symbol'] == sym for t in active_trades):
                        continue

                price = ticker.get('last', 0)
                if price <= 0 or (ticker.get('quoteVolume') or 0) < 1_000_000:
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
    מחזיר (breakout:bool, sol_1h_close:float, recent_4h_high:float).
    breakout=True אם 1H close > highest high של 5 נרות 4H אחרונים שלמים.
    """
    try:
        df_4h = get_data('SOL/USDT', timeframe='4h', limit=10)
        df_1h = get_data('SOL/USDT', timeframe='1h', limit=5)
        # 5 נרות 4H שלמים (לא כולל הנר הנוכחי שעוד מתגבש)
        recent_4h_high = float(df_4h['high'].iloc[-6:-1].max())
        sol_1h_close   = float(df_1h['close'].iloc[-1])
        breakout       = sol_1h_close > recent_4h_high
        return breakout, sol_1h_close, recent_4h_high
    except Exception:
        return False, 0.0, 0.0


def _coin_1h_breakout_above_4h_high(symbol: str) -> tuple[bool, float, float, float | None]:
    """
    Generic Breakout Check — כל מטבע.
    מחזיר (breakout, price_1h, high_4h, rsi_4h).
    breakout=True אם 1H close > highest high של 5 נרות 4H שלמים אחרונים.
    """
    try:
        df_4h          = get_data(symbol, timeframe='4h', limit=10)
        df_1h          = get_data(symbol, timeframe='1h', limit=5)
        recent_4h_high = float(df_4h['high'].iloc[-6:-1].max())
        price_1h       = float(df_1h['close'].iloc[-1])
        breakout       = price_1h > recent_4h_high
        rsi_s          = ta.rsi(df_4h['close'], length=14)
        rsi            = round(float(rsi_s.iloc[-1]), 1) if (rsi_s is not None and not rsi_s.isna().all()) else None
        return breakout, price_1h, recent_4h_high, rsi
    except Exception:
        return False, 0.0, 0.0, None


# ── Breakout Strategy Constants ───────────────────────────────────────────────
BREAKOUT_FNG_LONG_MIN  = 20   # FNG מינימום ל-LONG (מתחת = פחד קיצוני, לא קונים)
BREAKOUT_FNG_SHORT_MAX = 65   # FNG מקסימום ל-SHORT (מעל = חמדנות, לא שורטים)
RSI_VETO_SHORT         = 35   # RSI מינימום ל-SHORT (מתחת = oversold, לא שורטים)
BREAKOUT_MIN_VOL       = 1.5  # volume ratio מינימלי (150% מהממוצע = 50% מעל)


def _coin_breakout_full(symbol: str, direction: str = 'LONG') -> tuple[bool, float, float, float | None, float]:
    """
    Breakout / Breakdown check + Volume ratio — לשימוש Top10 Auto-Loop.

    LONG:  breakout=True אם 1H close > highest HIGH של 5 נרות 4H שלמים.
    SHORT: breakout=True אם 1H close < lowest  LOW  של 5 נרות 4H שלמים.

    מחזיר (signal, price_1h, h4_level, rsi_4h, vol_ratio).
      h4_level = 4H High (LONG) / 4H Low (SHORT).
    """
    try:
        df_4h     = get_data(symbol, timeframe='4h', limit=25)
        df_1h     = get_data(symbol, timeframe='1h', limit=25)
        price_1h  = float(df_1h['close'].iloc[-1])

        if direction == 'LONG':
            h4_level = float(df_4h['high'].iloc[-6:-1].max())
            signal   = price_1h > h4_level
        else:  # SHORT
            h4_level = float(df_4h['low'].iloc[-6:-1].min())
            signal   = price_1h < h4_level

        rsi_s     = ta.rsi(df_4h['close'], length=14)
        rsi       = round(float(rsi_s.iloc[-1]), 1) if (rsi_s is not None and not rsi_s.isna().all()) else None
        vol_cur   = float(df_1h['volume'].iloc[-1])
        vol_avg   = float(df_1h['volume'].iloc[-21:-1].mean())
        vol_ratio = round(vol_cur / vol_avg, 2) if vol_avg > 0 else 1.0
        return signal, price_1h, h4_level, rsi, vol_ratio
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
        'atr':             0.0,
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
        f"{'─' * 26}\n"
        f"📍 כניסה: `${price:.6g}`\n"
        f"🔴 SL ({sl_sign}{sl_pct}%): `${sl_price:.6g}`\n"
        f"🎯 TP1 ({tp_sign}{tp1_pct}%): `${tp1_price:.6g}` ← 50%\n"
        f"🎯 TP  ({tp_sign}{tp_pct}%): `${tp_price:.6g}` ← RR 1:3\n"
        f"📍 Trailing: {TRAIL_PCT}% מהשיא\n"
        f"{'─' * 26}\n"
        f"📊 RSI 4H: *{rsi_str}* | Vol ×{vol_ratio:.1f}\n"
        f"{'🔺' if direction=='LONG' else '🔻'} {level_lbl} ✅\n"
        f"😨 FNG: *{fng_v}*\n"
        f"💼 {LEVERAGE}x · ${margin:.0f} מרג'ין · ${pos_size:.0f} נשלט\n\n"
        + _wallet_opened_summary()
    )
    send_msg(msg)
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
    TOP10_INTERVAL = 15 * 60
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

                # RSI Filter
                if direction == 'LONG':
                    if rsi is not None and rsi > RSI_VETO_LONG:
                        print(f"[Top10 Breakout] {sym} LONG RSI veto ({rsi:.0f} > {RSI_VETO_LONG})")
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
            #   1. Sector coins (AI/RWA) first — sector_bonus=0 sorts before 1
            #   2. Volume high (>1.5x average) → הוכח עניין אמיתי
            #   3. LONG: RSI as low as possible (not overbought)
            #      SHORT: RSI as high as possible (room to drop)
            def _sort_key(c):
                sector_bonus = 0 if c['symbol'] in SECTOR_PRIORITY_SYMBOLS else 1
                rsi_v        = c['rsi'] if c['rsi'] is not None else (999 if direction == 'LONG' else 0)
                vol_key      = -c['vol_ratio']  # higher vol = lower key = earlier
                if direction == 'LONG':
                    # prefer RSI in sweet-spot 55-70 (momentum without overbought)
                    rsi_key = abs(rsi_v - 62.5)   # closest to midpoint 62.5 = best
                else:
                    rsi_key = -rsi_v              # highest RSI first for SHORT
                return (sector_bonus, rsi_key, vol_key)

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

    if IS_DEPLOYED:
        send_msg(
            "🚀 *הבוט הופעל — Production*\n\n"
            "⚙️ *מצב הלולאות:*\n"
            "🔍 סריקת איתותים: כל *60 דקות* ✅\n"
            "📍 מעקב SL/TP:    כל *60 שניות* ✅\n\n"
            f"📋 /home — תפריט ראשי\n"
            f"🖥 [פתח דאשבורד]({DASHBOARD_URL})"
        )
    else:
        send_msg(
            "🛠 *הבוט הופעל — Development Mode*\n\n"
            "⚙️ *מצב הלולאות:*\n"
            "🔍 סריקה אוטומטית: *מושבתת* \\(רק /scan ידני\\)\n"
            "📍 מעקב SL/TP:    כל *60 שניות* ✅\n\n"
            "_כדי לא לקבל הודעות כפולות עם הבוט הפרוס_\n"
            f"📋 /home — תפריט ראשי\n"
            f"🖥 [פתח דאשבורד]({DASHBOARD_URL})"
        )

    # Thread הראשי נשאר ער
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    try:
        main()
    except Exception as _boot_err:
        import traceback as _tb
        _crash_msg = _tb.format_exc()
        print(f"[FATAL] Bot crashed at startup:\n{_crash_msg}", flush=True)
        try:
            with open('/tmp/bot_crash.log', 'w') as _cf:
                _cf.write(_crash_msg)
        except Exception:
            pass
        raise
