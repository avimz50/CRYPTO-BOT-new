"""
strategy_engine.py — Pure Math Engine

RULES (enforced by design):
  - ZERO access to wallet, balance, or database_manager.
  - Input: price dataframes + config constants.
  - Output: SL/TP price levels and ATR values.
  - Importing this file can NEVER corrupt financial state.

Used by bot.py to compute trade targets before calling database_manager.
"""

from __future__ import annotations
import pandas as pd
try:
    import pandas_ta as ta
except ImportError:
    ta = None

# ── Config (imported read-only) ────────────────────────────────────────────────
from config import (
    ATR_PERIOD,
    ATR_SL_MULT,
    ATR_TP1_MULT,
    ATR_TP2_MULT,
    ATR_SL_TF,
    ATR_TP1_TF,
    TARGET_RR_RATIO,
    HUNTER_TP1_RR,
    HUNTER_MIN_RR,
)


# ── ATR Calculation ────────────────────────────────────────────────────────────
def calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    """
    Compute the latest ATR value from a price DataFrame.
    Returns 0.0 if calculation fails (caller must handle fallback).
    DataFrame must have columns: high, low, close.
    """
    if ta is None or df is None or len(df) < period + 1:
        return 0.0
    try:
        series = ta.atr(df['high'], df['low'], df['close'], length=period)
        if series is None:
            return 0.0
        clean = series.dropna()
        if len(clean) == 0:
            return 0.0
        val = float(clean.iloc[-1])
        return val if val > 0 else 0.0
    except Exception:
        return 0.0


# ── SL / TP Level Calculator ──────────────────────────────────────────────────
def calc_targets(
    entry: float,
    direction: str,         # 'LONG' or 'SHORT'
    atr_sl: float,          # ATR from slow timeframe (1H) for SL
    atr_tp1: float = 0.0,   # ATR from fast timeframe (15m) for TP1; falls back to atr_sl
    rr_ratio: float = TARGET_RR_RATIO,
    hunter: bool = False,
) -> dict:
    """
    Compute SL, TP1, TP2, and BE prices from ATR values.

    Returns:
        sl_price, tp1_price, tp_price (TP2), be_price,
        sl_pct, tp1_pct, tp_pct,
        sl_dist, tp1_dist, tp_dist,
        atr_sl, atr_tp1  (inputs, echoed for logging)

    Formula:
        SL   dist = ATR_SL_MULT  × atr_sl       (1H ATR — stable)
        TP1  dist = ATR_TP1_MULT × atr_tp1      (15m ATR — fast partial trigger)
        TP2  dist = sl_dist × TARGET_RR_RATIO   (RR-based final target)
        BE   dist = TP1 dist × 0.50             (move SL to entry at 50% of TP1)
    """
    if entry <= 0:
        return {}

    # Fallback: if 15m ATR unavailable, use 1H ATR for TP1 too
    _atr_tp1 = atr_tp1 if atr_tp1 > 0 else atr_sl

    if hunter:
        # Hunter mode overrides: TP1 at 1:1, TP2 at HUNTER_MIN_RR
        sl_dist  = ATR_SL_MULT * atr_sl if atr_sl > 0 else entry * 0.02
        sl_pct   = round(sl_dist / entry * 100, 4)
        tp1_pct  = round(sl_pct * HUNTER_TP1_RR, 2)
        tp_pct   = round(sl_pct * HUNTER_MIN_RR, 2)
        tp1_dist = entry * tp1_pct / 100
        tp_dist  = entry * tp_pct  / 100
    else:
        sl_dist  = ATR_SL_MULT  * atr_sl   if atr_sl  > 0 else entry * 0.02
        tp1_dist = ATR_TP1_MULT * _atr_tp1 if _atr_tp1 > 0 else sl_dist * 1.0
        tp_dist  = sl_dist * rr_ratio
        sl_pct   = round(sl_dist  / entry * 100, 4)
        tp1_pct  = round(tp1_dist / entry * 100, 4)
        tp_pct   = round(tp_dist  / entry * 100, 4)

    be_dist = tp1_dist * 0.50

    if direction == 'LONG':
        sl_price  = round(entry - sl_dist,  8)
        tp1_price = round(entry + tp1_dist, 8)
        tp_price  = round(entry + tp_dist,  8)
        be_price  = round(entry + be_dist,  8)
    else:  # SHORT
        sl_price  = round(entry + sl_dist,  8)
        tp1_price = round(entry - tp1_dist, 8)
        tp_price  = round(entry - tp_dist,  8)
        be_price  = round(entry - be_dist,  8)

    tp1_rr = round(tp1_dist / sl_dist, 3) if sl_dist > 0 else 0.0

    return {
        'sl_price':  sl_price,
        'tp1_price': tp1_price,
        'tp_price':  tp_price,
        'be_price':  be_price,
        'sl_pct':    sl_pct,
        'tp1_pct':   tp1_pct,
        'tp_pct':    tp_pct,
        'sl_dist':   round(sl_dist,  8),
        'tp1_dist':  round(tp1_dist, 8),
        'tp_dist':   round(tp_dist,  8),
        'be_dist':   round(be_dist,  8),
        'tp1_rr':    tp1_rr,
        'atr_sl':    atr_sl,
        'atr_tp1':   _atr_tp1,
        'rr_ratio':  rr_ratio,
    }


# ── RSI Helper ────────────────────────────────────────────────────────────────
def calc_rsi(df: pd.DataFrame, period: int = 14) -> float:
    """Latest RSI value from a price DataFrame. Returns 50.0 on failure."""
    if ta is None or df is None or len(df) < period + 1:
        return 50.0
    try:
        series = ta.rsi(df['close'], length=period)
        if series is None:
            return 50.0
        clean = series.dropna()
        return float(clean.iloc[-1]) if len(clean) > 0 else 50.0
    except Exception:
        return 50.0


# ── EMA Helper ────────────────────────────────────────────────────────────────
def calc_ema(df: pd.DataFrame, period: int = 200) -> float:
    """Latest EMA value. Returns 0.0 on failure."""
    if ta is None or df is None or len(df) < period:
        return 0.0
    try:
        series = ta.ema(df['close'], length=period)
        if series is None:
            return 0.0
        clean = series.dropna()
        return float(clean.iloc[-1]) if len(clean) > 0 else 0.0
    except Exception:
        return 0.0


# ── Viability Check ───────────────────────────────────────────────────────────
def is_trade_viable(
    entry: float,
    targets: dict,
    round_trip_fee_pct: float = 0.12,
    pos_size: float = 0.0,
    min_net_profit_usd: float = 1.50,
    min_rr: float = 1.8,
) -> tuple[bool, str]:
    """
    Check if a trade setup is viable before opening.
    Returns (ok: bool, reason: str).
    Purely mathematical — no wallet access.
    """
    sl_dist  = targets.get('sl_dist', 0)
    tp_dist  = targets.get('tp_dist', 0)
    entry_p  = entry

    if sl_dist <= 0 or tp_dist <= 0 or entry_p <= 0:
        return False, "invalid distances"

    gross_profit = pos_size * tp_dist / entry_p if entry_p > 0 else 0
    gross_loss   = pos_size * sl_dist / entry_p if entry_p > 0 else 0
    fee_cost     = pos_size * round_trip_fee_pct / 100
    net_profit   = gross_profit - fee_cost

    rr = gross_profit / gross_loss if gross_loss > 0 else 0

    if net_profit < min_net_profit_usd:
        return False, f"net profit ${net_profit:.2f} < min ${min_net_profit_usd}"
    if rr < min_rr:
        return False, f"RR {rr:.2f} < min {min_rr}"

    return True, "ok"
