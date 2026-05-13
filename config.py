"""
config.py — Adaptive Sniper 2026
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

# ── Position Sizing ────────────────────────────────────────────────────────────
LEVERAGE      = 10
MARGIN        = 50           # $50 margin per trade
POSITION_SIZE = MARGIN * LEVERAGE  # $500 controlled

# ── Circuit Breaker ────────────────────────────────────────────────────────────
DAILY_LOSS_LIMIT = -30.0     # -$30 = 15% of $200 starting balance

# ── Scoring Thresholds ─────────────────────────────────────────────────────────
MIN_SCORE           = 78     # Entry threshold
RSI_VETO_LONG       = 65     # Hard veto — extreme overbought
RSI_VETO_SHORT      = 15     # Hard veto — extreme oversold (lowered: allow shorts in high-momentum sells)
EMA_PROXIMITY_PCT   = 10.0   # Anti-chase: max % above EMA200 (increased: don't miss breakouts far from EMA)
VOL_EMA_BYPASS_MULT = 1.5    # Volume ≥ ×1.5 bypasses EMA proximity veto

# ── Trade Lifecycle ────────────────────────────────────────────────────────────
TRAIL_ACTIVATION_PCT = 2.0
BE_BUFFER_PCT        = 2.0
BE_LOCK_BUFFER_PCT   = 0.1
TRAIL_PCT            = 1.5
ATR_TRAIL_MULT       = 1.5
PARTIAL_25_TRIGGER   = 5.0
PARTIAL_25_DROP      = 1.0
SL_PCT_FIXED         = 3.5
TP1_PCT_FIXED        = 5.0
TP_PCT_FIXED         = 15.0

# ── Dynamic SL Parameters ─────────────────────────────────────────────────────
MAJOR_COINS     = {'BTC', 'ETH', 'SOL'}
SL_BASE_MAJOR   = 3.0   # % SL for major coins
SL_BASE_ALTCOIN = 5.0   # % SL for altcoins
SL_FEAR_BUFFER  = 1.0   # Extra % when FNG < 25
SL_ATR_MULT     = 1.5   # SL must be at least 1.5 × ATR%

# ── Regime Filters ─────────────────────────────────────────────────────────────
EXTREME_FEAR_LONG_MIN_SCORE    = 95   # LONG in extreme fear: score > 95 only
STRONG_UPTREND_SHORT_MIN_SCORE = 90   # BTC strong uptrend: SHORT score > 90

# ── Sniper Exception ───────────────────────────────────────────────────────────
SNIPER_MIN_SCORE   = 92
SNIPER_EMA_PCT     = 5.0
SNIPER_VOL_MIN     = 2.5
SNIPER_MARGIN_MULT = 0.5   # Half-size entry during Kill-Switch

# ── Hunter / Risk ──────────────────────────────────────────────────────────────
MAX_EQUITY_RISK_PCT  = 1.5    # Max 1.5% equity risk per trade
MIN_RR_RATIO         = 2.0    # Minimum 1:2 R:R
HUNTER_FNG_THRESHOLD = 70
HUNTER_PUMP_PCT_24H  = 15.0
HUNTER_MIN_RR        = 3.0
HUNTER_TP1_RR        = 1.0
WEEKLY_PROFIT_TARGET = 50.0

# ── Risk & Position Sizing Engine ─────────────────────────────────────────────
TRADE_RISK_PCT       = 1.0    # % of total balance to risk per trade (configurable)
ROUND_TRIP_FEE_PCT   = 0.12   # Bitget round-trip taker fee: 0.06% entry + 0.06% exit
MIN_NET_PROFIT_USD   = 15.0   # Minimum net profit at TP1 to justify opening a trade
MIN_PROFIT_RR        = 1.5    # Minimum RR at TP1 (gross profit / gross loss)
MAX_AUTO_LEVERAGE    = 20     # Cap on auto-calculated leverage
MIN_AUTO_LEVERAGE    = 2      # Floor on auto-calculated leverage

# ── ATR-Based Dynamic Targets ──────────────────────────────────────────────────
USE_ATR_TARGETS = True   # Use 1H ATR to set SL/TP1/TP2 instead of fixed %
ATR_PERIOD      = 14     # ATR calculation period (1H candles)
ATR_SL_MULT     = 2.0    # SL = Entry ∓ (ATR_SL_MULT  × ATR)
ATR_TP1_MULT    = 1.5    # TP1 = Entry ± (ATR_TP1_MULT × ATR)  → always triggers BE
ATR_TP2_MULT    = 3.0    # TP2 = Entry ± (ATR_TP2_MULT × ATR)

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
SWING_TRACK_VOL_MIN  = 10_000_000
SWING_TRACK_SL_PCT   = 6.0
SWING_TRACK_TP1_PCT  = 6.0
SWING_TRACK_TP_PCT   = 12.0
SWING_TRACK_LEVERAGE = 3
SWING_TRACK_BE_PCT   = 4.0

# ── Scalp Mode (Mean-Reversion) ────────────────────────────────────────────────
SCALP_LEVERAGE         = 5
SCALP_MARGIN           = 15.0
SCALP_POS_SIZE         = SCALP_MARGIN * SCALP_LEVERAGE
SCALP_TP_PCT           = 3.0
SCALP_SL_PCT           = 1.5
SCALP_MAX_DURATION_MIN = 60
MAX_SCALP_TRADES       = 2

# ── Stagnation Exit ────────────────────────────────────────────────────────────
STAGNATION_MIN_HOURS        = 4.0
STAGNATION_PROFIT_MIN_HOURS = 24.0
STAGNATION_RANGE_PCT        = 0.5

# ── Scalp Scan Triggers ────────────────────────────────────────────────────────
SCALP_BUBBLE_MIN_PCT   = 30.0
SCALP_CRASH_MIN_PCT    = 20.0
SCALP_SHORT_RSI_THRESH = 82.0
SCALP_LONG_RSI_THRESH  = 18.0
SCALP_BOUNCE_PCT       = 1.0
SCALP_SCAN_INTERVAL    = 300
SCALP_SCAN_INTERVAL_WAIT = 600

# ── High-Velocity / Cliff ──────────────────────────────────────────────────────
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
CLIFF_MAX_DURATION_MIN = 30
MAX_CLIFF_TRADES      = 2
CLIFF_SCAN_INTERVAL   = 120

# ── 3-Filter Momentum Gate ─────────────────────────────────────────────────────
MOMENTUM_GATE_ENABLED  = True
MOMENTUM_VOL_RATIO     = 0.10   # 24h Volume must be > 10% of Open Interest value
MOMENTUM_EMA_FAST      = 9
MOMENTUM_EMA_MID       = 21
MOMENTUM_EMA_SLOW      = 50
MOMENTUM_MIN_VOL_SURGE = 1.5    # 15m candle volume > 1.5× 20-bar average
VWAP_TIMEFRAME         = '1h'   # Timeframe used for VWAP calculation

# ── Slow-Movers Blacklist ──────────────────────────────────────────────────────
SLOW_MOVERS = {'TRX/USDT', 'ADA/USDT'}

# ── Logging ────────────────────────────────────────────────────────────────────
VERBOSE_LOG = False

# ── Symbol Lists ──────────────────────────────────────────────────────────────
ENERGY_GEO = ['POWR/USDT', 'HNT/USDT', 'WLD/USDT']

TOP10_SYMBOLS = [
    'BTC/USDT', 'ETH/USDT',
    'BNB/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT',
    'DOGE/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT', 'TRX/USDT',
    'ATOM/USDT', 'NEAR/USDT', 'APT/USDT', 'SUI/USDT', 'ARB/USDT',
    'OP/USDT',   'INJ/USDT',
    'FET/USDT', 'RENDER/USDT', 'ONDO/USDT',
]

SECTOR_PRIORITY_SYMBOLS = {'FET/USDT', 'RENDER/USDT', 'ONDO/USDT'}

MAJOR_WATCH_COINS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'XRP/USDT',
    'SOL/USDT', 'ADA/USDT', 'AVAX/USDT', 'DOGE/USDT',
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

# ── FNG Defaults ───────────────────────────────────────────────────────────────
FNG_DEFAULTS = {'extreme_fear': 25, 'fear': 30, 'greed': 70}
CONFIG_DEFAULTS = {'max_trades': 3}

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
