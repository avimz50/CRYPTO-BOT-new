"""
config.py — Clean Base 2026
All static constants, environment-sourced values, and JSON settings loaders.
Dynamic runtime state (MAX_TRADES, FNG thresholds) is initialised in bot.py
from the loaders below and mutated there at runtime via Telegram / API.
"""
import os
import json

# ── File Paths ─────────────────────────────────────────────────────────────────
HOT_CANDIDATES_FILE = 'artifacts/bot-dashboard/public/hot_candidates.json'
ACTIVE_TRADES_FILE  = 'artifacts/bot-dashboard/public/active_trades.json'
WALLET_FILE         = 'artifacts/bot-dashboard/public/wallet.json'
AUDIT_LOG_FILE      = 'artifacts/bot-dashboard/public/trade_audit.json'
VALIDATION_TRIAL_FILE = 'artifacts/bot-dashboard/public/validation_trial.json'
SCAN_REPORT_FILE    = 'artifacts/bot-dashboard/public/last_scan_results.json'
FNG_SETTINGS_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fng_settings.json')
CONFIG_FILE         = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

# ── Bot Identity ───────────────────────────────────────────────────────────────
STARTING_BALANCE = 200.0
DASHBOARD_URL    = 'https://python-script-bymzrkhy.replit.app/'

# ── Environment Flags ──────────────────────────────────────────────────────────
IS_DEPLOYED = bool(os.environ.get('REPLIT_DEPLOYMENT', ''))
GEMINI_URL  = os.environ.get('AI_INTEGRATIONS_GEMINI_BASE_URL', '')
GEMINI_KEY  = os.environ.get('AI_INTEGRATIONS_GEMINI_API_KEY', '')

# ── Position Sizing — Fixed ────────────────────────────────────────────────────
LEVERAGE      = 10
MARGIN        = 50.0         # $50 margin per trade
POSITION_SIZE = MARGIN * LEVERAGE  # $500 controlled

# ── Paper-trading cost model / validation trial ────────────────────────────────
# Estimated Bitget USDT perpetual taker fee. This is an explicit paper-trading
# assumption, not an exchange-reported account fee.
TAKER_FEE_RATE = 0.0006          # 0.06% per filled side
ESTIMATED_SLIPPAGE_RATE = 0.0002 # 0.02% per filled side
MIN_EXPECTED_PROFIT_USD = 15.0   # Minimum gross and net profit at TP1
MIN_ENTRY_RISK_REWARD = 2.0      # Final TP distance / SL distance
VALIDATION_TRIAL_TARGET = 200
VALIDATION_TRIAL_ENABLED = True
VALIDATION_STRONG_LONG_MIN_SCORE = 85

# ── Fixed SL / TP Percentages ──────────────────────────────────────────────────
SL_PCT  = 2.0   # Stop Loss   = 2% from entry
TP1_PCT = 1.0   # Take Profit 1 = 1% (triggers Break-Even + 75% close)
TP2_PCT = 4.0   # Take Profit 2 = 4% (final target, 1:2 RR)

# ── Circuit Breaker ────────────────────────────────────────────────────────────
DAILY_LOSS_LIMIT = -15.0     # -$15 = circuit breaker (3 max losses/day)

# ── Entry Restraint ────────────────────────────────────────────────────────────
# Avoid illiquid markets and repeated entries into a setup that has not reset.
MAX_ENTRY_SPREAD_PCT       = 0.8
SYMBOL_REENTRY_HOURS       = 12
SYMBOL_LOSS_REENTRY_HOURS  = 24
SWING_LOSS_STREAK_LIMIT    = 3
SWING_LOSS_PAUSE_HOURS     = 2

# ── Market Regime Filter (FNG + BTC 4H EMA20) ──────────────────────────────────
# BEARISH: FNG < 40 OR BTC below 4H EMA20  → LONGs blocked
# BULLISH: FNG > 60 AND BTC above 4H EMA20 → SHORTs blocked
# NEUTRAL: 40 ≤ FNG ≤ 60                   → both allowed, max 2 trades
REGIME_BEARISH_FNG       = 38   # FNG threshold below = bearish (<38 = Fear+Extreme Fear OR BTC<EMA20)
REGIME_BULLISH_FNG       = 60   # FNG threshold above = bullish
REGIME_NEUTRAL_MAX_TRADES = 2   # max concurrent trades in choppy/neutral market

# ── Scoring Thresholds ─────────────────────────────────────────────────────────
MIN_SCORE           = 75     # Entry threshold
RSI_VETO_LONG       = 62     # Hard veto — LONG forbidden if RSI > 62 (overbought)
RSI_VETO_SHORT      = 65     # Hard veto — SHORT forbidden if RSI < 65 (only short when clearly overbought)
EMA_PROXIMITY_PCT   = 10.0   # Anti-chase: max % above EMA200
VOL_EMA_BYPASS_MULT = 1.5    # Volume ≥ ×1.5 bypasses EMA proximity veto

# ── Trade Lifecycle ────────────────────────────────────────────────────────────
TRAIL_ACTIVATION_PCT = 2.0
BE_BUFFER_PCT        = 2.0
BE_LOCK_BUFFER_PCT   = 0.1
TRAIL_PCT            = 1.5
ATR_TRAIL_MULT       = 1.5
PARTIAL_25_TRIGGER   = 5.0
PARTIAL_25_DROP      = 1.0

# ── ATR Period (scoring only) ──────────────────────────────────────────────────
ATR_PERIOD = 14

# ── Coins Classification ───────────────────────────────────────────────────────
MAJOR_COINS = {'BTC', 'ETH', 'SOL'}

# ── Weekly Target ──────────────────────────────────────────────────────────────
WEEKLY_PROFIT_TARGET = 50.0

# ── Bollinger Band Squeeze ─────────────────────────────────────────────────────
BB_SQUEEZE_RATIO    = 0.50
BB_SQUEEZE_LOOKBACK = 20
BB_SQUEEZE_BREAKOUT = 0.005

# ── Scalp Track ───────────────────────────────────────────────────────────────
SCALP_TRACK_VOL_MIN    = 50_000_000
SCALP_TRACK_SL_PCT     = 2.0
SCALP_TRACK_TP1_PCT    = 4.0
SCALP_TRACK_TP_PCT     = 8.0
SCALP_TRACK_LEVERAGE   = 10
SCALP_TRACK_BE_TRIGGER = 0.50

# ── Swing Track ───────────────────────────────────────────────────────────────
SWING_TRACK_VOL_MIN  = 1_500_000
SWING_TRACK_SL_PCT   = 6.0
SWING_TRACK_TP1_PCT  = 6.0
SWING_TRACK_TP_PCT   = 12.0
SWING_TRACK_LEVERAGE = 10
SWING_TRACK_BE_PCT   = 4.0

# ── Scalp Mode (Mean-Reversion) ────────────────────────────────────────────────
ENABLE_SCALP_MODE      = False     # כבוי — לא סוחרים ב-Extreme Fear
SCALP_LEVERAGE         = 10        # locked — same as LEVERAGE
SCALP_MARGIN           = 50.0      # locked — same as MARGIN
SCALP_POS_SIZE         = SCALP_MARGIN * SCALP_LEVERAGE   # = $500
SCALP_TP_PCT           = 3.0
SCALP_SL_PCT           = 1.5
SCALP_MAX_DURATION_MIN = 48
MAX_SCALP_TRADES       = 2

# ── Stagnation Exit ────────────────────────────────────────────────────────────
STAGNATION_MIN_HOURS        = 4.0
STAGNATION_PROFIT_MIN_HOURS = 24.0
STAGNATION_RANGE_PCT        = 0.5

# ── Smart Timeout ───────────────────────────────────────────────────────────────
# סוגר עסקה שפתוחה >= N שעות ורווח < THRESHOLD% (הפסד / קרוב לאפס)
# אם הרווח >= THRESHOLD% — לא נוגעים, ה-BE/Trailing SL מטפל
SMART_TIMEOUT_HOURS          = 4.0   # שעות מקסימום לעסקה לא רווחית
SMART_TIMEOUT_MIN_PROFIT_PCT = 1.0   # סף רווח מינימלי להמשך החזקה

# ── Max Duration — עצירה מוקדמת לעסקות Swing/Breakout ────────────────────────
# סוגר עסקה שלא הגיעה ל-TP1/BE אחרי 60 דקות — חוסך $4-7 לעומת SL מלא ($10)
SWING_MAX_DURATION_MIN = 120   # דקות מקסימום ללא TP1 — close at market

# ── Fast-Loss Protection — זיהוי setup כושל מוקדם ──────────────────────────────
# סוגר עסקה שמפסידה 5%+ מהמרג'ין בתוך 15 הדקות הראשונות
# 5% × $50 = $2.50 הפסד מהיר = setup נכשל → יוצאים לפני SL מלא ($10)
FAST_LOSS_MARGIN_PCT = 3.5    # % הפסד על מרג'ין שמפעיל סגירה מוקדמת
FAST_LOSS_MINUTES    = 15     # חלון זמן (דקות) לבדיקת fast-loss

# ── RSI Reversal Guard — זיהוי היפוך מומנטום על 15m ───────────────────────────
# פעיל אחרי 15 דק' (FastLoss כיסה את החלון הראשון).
# SHORT בהפסד + RSI_15m > 62 = קנייה חזקה → סוגר לפני SL ($10)
# LONG  בהפסד + RSI_15m < 38 = מכירה חזקה → סוגר לפני SL ($10)
RSI_REVERSAL_MIN_LOSS_PCT  = 0.30   # % הפסד מחיר מינימלי להפעלה (≈ $1.50)
RSI_REVERSAL_MIN_MIN       = 15     # לא פעיל בחלון FastLoss (0–15 דק')
RSI_REVERSAL_SHORT_THRESH  = 62     # RSI 15m מעל זה + SHORT בהפסד → סגור
RSI_REVERSAL_LONG_THRESH   = 38     # RSI 15m מתחת לזה + LONG בהפסד → סגור
RSI_REVERSAL_CACHE_TTL     = 120    # שניות: cache RSI לכל סימבול

# ── Breakout Strategy Enable ─────────────────────────────────────────────────────
# False = חסום את כל עסקאות ה-Breakout (להפעלה כשהאסטרטגיה מפסידה)
ENABLE_BREAKOUT_STRATEGY = True

# ── Scalp Scan Triggers ────────────────────────────────────────────────────────
SCALP_BUBBLE_MIN_PCT   = 30.0
SCALP_CRASH_MIN_PCT    = 20.0
SCALP_SHORT_RSI_THRESH = 82.0
SCALP_LONG_RSI_THRESH  = 18.0
SCALP_BOUNCE_PCT       = 1.0
SCALP_SCAN_INTERVAL    = 300
SCALP_SCAN_INTERVAL_WAIT = 600

# ── High-Velocity / Cliff ──────────────────────────────────────────────────────
# Set to False to disable all Velocity (Rocket/Cliff) trades — e.g. during Extreme Fear
ENABLE_VELOCITY_STRATEGY = False

CLIFF_DROP_PCT        = 2.5
CLIFF_VOL_MULT        = 3.0
CLIFF_RSI_OVERBOUGHT  = 70.0
CLIFF_SL_PCT          = 2.5
CLIFF_TP_PCT          = 3.0
CLIFF_BE_TRIGGER_PCT  = 1.5
CLIFF_TRAIL_PCT       = 1.5
CLIFF_LEVERAGE        = 10
CLIFF_MARGIN          = 50.0
CLIFF_POS_SIZE        = CLIFF_MARGIN * CLIFF_LEVERAGE
CLIFF_MAX_DURATION_MIN = 24
MAX_CLIFF_TRADES      = 2
CLIFF_SCAN_INTERVAL   = 120

# ── 3-Filter Momentum Gate ─────────────────────────────────────────────────────
MOMENTUM_GATE_ENABLED  = True
MOMENTUM_VOL_RATIO     = 0.10   # 24h Volume must be > 10% of Open Interest value
MOMENTUM_EMA_FAST      = 9
MOMENTUM_EMA_MID       = 21
MOMENTUM_EMA_SLOW      = 50
MOMENTUM_MIN_VOL_SURGE = 2.0    # 15m candle volume > 2.0× 20-bar average
VWAP_TIMEFRAME         = '1h'   # Timeframe used for VWAP calculation

def canonical_swap_symbol(symbol: str) -> str:
    """Return CCXT's canonical Bitget linear USDT-swap symbol."""
    raw = str(symbol or '').strip().upper()
    if not raw:
        raise ValueError('empty symbol')
    if raw.endswith(':USDT'):
        base_pair = raw[:-5]
    else:
        base_pair = raw
    if '/' not in base_pair:
        base_pair = f'{base_pair}/USDT'
    base, quote = base_pair.split('/', 1)
    if not base or quote != 'USDT':
        raise ValueError(f'not a USDT market: {symbol!r}')
    return f'{base}/USDT:USDT'


def display_symbol(symbol: str) -> str:
    """Human-facing pair without CCXT's settlement suffix."""
    return canonical_swap_symbol(symbol).split(':', 1)[0]


# ── Slow-Movers Blacklist ──────────────────────────────────────────────────────
SLOW_MOVERS = {'TRX/USDT:USDT', 'ADA/USDT:USDT', 'DOGE/USDT:USDT',   # DOGE: 4/4 MaxDuration הפסדים
               'BNB/USDT:USDT', 'SKYAI/USDT:USDT'}              # BNB: 4/5 SHORT losses; SKYAI: 2/3 FastLoss

# ── Logging ────────────────────────────────────────────────────────────────────
VERBOSE_LOG = False

# ── Symbol Lists ──────────────────────────────────────────────────────────────
ENERGY_GEO = ['POWR/USDT:USDT', 'WLD/USDT:USDT']

TOP10_SYMBOLS = [
    'BTC/USDT:USDT', 'ETH/USDT:USDT',
    'BNB/USDT:USDT', 'SOL/USDT:USDT', 'XRP/USDT:USDT', 'ADA/USDT:USDT',
    'DOGE/USDT:USDT', 'AVAX/USDT:USDT', 'DOT/USDT:USDT', 'LINK/USDT:USDT', 'TRX/USDT:USDT',
    'ATOM/USDT:USDT', 'NEAR/USDT:USDT', 'APT/USDT:USDT', 'SUI/USDT:USDT', 'ARB/USDT:USDT',
    'OP/USDT:USDT',   'INJ/USDT:USDT',
    'FET/USDT:USDT', 'RENDER/USDT:USDT', 'ONDO/USDT:USDT',
]

SECTOR_PRIORITY_SYMBOLS = {'FET/USDT:USDT', 'RENDER/USDT:USDT', 'ONDO/USDT:USDT'}

MAJOR_WATCH_COINS = [
    'BTC/USDT:USDT', 'ETH/USDT:USDT', 'BNB/USDT:USDT', 'XRP/USDT:USDT',
    'SOL/USDT:USDT', 'ADA/USDT:USDT', 'AVAX/USDT:USDT', 'DOGE/USDT:USDT',
]
MAJOR_WATCH_ICONS = {
    'BTC': '₿', 'ETH': '🔷', 'BNB': '🟡', 'XRP': '🔵',
    'SOL': '🌞', 'ADA': '🔶', 'AVAX': '🔺', 'DOGE': '🐕',
}

# ── Sector Map ────────────────────────────────────────────────────────────────
SECTOR_MAP: dict[str, str] = {
    'ETH': 'L1', 'SOL': 'L1', 'AVAX': 'L1', 'APT': 'L1', 'NEAR': 'L1',
    'SUI': 'L1', 'SEI': 'L1', 'ATOM': 'L1', 'DOT': 'L1', 'ADA': 'L1',
    'TRX': 'L1', 'TON': 'L1', 'FTM': 'L1', 'ONE': 'L1', 'ALGO': 'L1',
    'MATIC': 'L2', 'ARB': 'L2', 'OP': 'L2', 'IMX': 'L2', 'ZK': 'L2',
    'STRK': 'L2', 'MANTA': 'L2', 'BLAST': 'L2', 'METIS': 'L2',
    'UNI': 'DeFi', 'AAVE': 'DeFi', 'CRV': 'DeFi', 'MKR': 'DeFi',
    'COMP': 'DeFi', 'SNX': 'DeFi', 'BAL': 'DeFi', 'SUSHI': 'DeFi',
    'JUP': 'DeFi', 'DYDX': 'DeFi', 'GMX': 'DeFi', 'ENA': 'DeFi',
    'FET': 'AI', 'AGIX': 'AI', 'OCEAN': 'AI', 'RENDER': 'AI', 'TAO': 'AI',
    'WLD': 'AI', 'ALT': 'AI', 'GRT': 'AI',
    'AXS': 'Gaming', 'SAND': 'Gaming', 'MANA': 'Gaming', 'ENJ': 'Gaming',
    'GALA': 'Gaming', 'ILV': 'Gaming', 'YGG': 'Gaming',
    'DOGE': 'Meme', 'SHIB': 'Meme', 'PEPE': 'Meme', 'FLOKI': 'Meme',
    'BONK': 'Meme', 'WIF': 'Meme', 'BOME': 'Meme',
    'BNB': 'CEX', 'OKB': 'CEX', 'CRO': 'CEX', 'KCS': 'CEX', 'GT': 'CEX',
    'HT': 'CEX', 'BGB': 'CEX',
    'BTC': 'BTC', 'WBTC': 'BTC', 'STX': 'BTC', 'ORDI': 'BTC',
    'LINK': 'Oracle', 'BAND': 'Oracle', 'TRB': 'Oracle', 'API3': 'Oracle',
}

# ── Trade Capacity — fixed constant, runtime-adjustable via /slots ──────────────
MAX_TRADES: int = 3          # hard cap: never more than 3 concurrent open trades

# ── FNG Defaults ───────────────────────────────────────────────────────────────
FNG_DEFAULTS = {'extreme_fear': 25, 'fear': 30, 'greed': 70}

# ── FNG Score Filter — trend-aligned penalty/bonus (never touches sizing) ───────
FNG_FEAR_THRESHOLD  = 35    # FNG < 35 → Fear market: penalise LONGs, reward SHORTs
FNG_GREED_THRESHOLD = 65    # FNG > 65 → Greed market: penalise SHORTs, reward LONGs
FNG_PENALTY         = -10   # Points removed from counter-sentiment direction
FNG_BONUS           = +5    # Points added to with-sentiment direction
CONFIG_DEFAULTS = {'max_trades': MAX_TRADES}

# ── Heartbeat / Audit ──────────────────────────────────────────────────────────
HEARTBEAT_INTERVAL = 1800   # 30 minutes
AUDIT_HOURS = {12}          # Daily report at 12:00

# ── Make.com Webhook ───────────────────────────────────────────────────────────
# Set MAKE_WEBHOOK_SECRET as a Replit secret; never hardcode auth tokens.
MAKE_INCOMING_SECRET = os.environ.get('MAKE_WEBHOOK_SECRET', '')


# ── JSON Settings Loaders ───────────────────────────────────────────────────────
def load_fng_settings() -> dict:
    """
    Reads fng_settings.json and returns validated thresholds.
    Falls back to FNG_DEFAULTS on any error.
    """
    try:
        with open(FNG_SETTINGS_FILE, 'r') as _f:
            _d = json.load(_f)
        return {
            'extreme_fear': int(_d.get('extreme_fear', FNG_DEFAULTS['extreme_fear'])),
            'fear':         int(_d.get('fear',         FNG_DEFAULTS['fear'])),
            'greed':        int(_d.get('greed',        FNG_DEFAULTS['greed'])),
        }
    except Exception:
        return dict(FNG_DEFAULTS)


def load_config() -> dict:
    """
    Reads config.json and returns validated settings (max_trades clamped 1-5).
    Falls back to CONFIG_DEFAULTS on any error.
    """
    try:
        with open(CONFIG_FILE, 'r') as _f:
            _d = json.load(_f)
        return {
            'max_trades': max(1, min(5, int(_d.get('max_trades', CONFIG_DEFAULTS['max_trades'])))),
        }
    except Exception:
        return dict(CONFIG_DEFAULTS)
