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

from config import ATR_PERIOD, SL_PCT, TP1_PCT, TP2_PCT


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


# ── SL / TP Level Calculator — Fixed % ────────────────────────────────────────
def calc_targets(
    entry: float,
    direction: str,          # 'LONG' or 'SHORT'
    tp1_pct: float | None = None,  # override TP1_PCT from config (adaptive exit)
) -> dict:
    """
    Fixed-percentage SL/TP targets.
      SL  = SL_PCT  from entry (2%)
      TP1 = tp1_pct arg (or TP1_PCT from config) — triggers Break-Even + 75% close
      TP2 = TP2_PCT from entry (4%) — final target (1:2 RR)
      BE  = entry price            — SL moves to entry when TP1 is hit
    """
    if entry <= 0:
        return {}

    eff_tp1_pct = tp1_pct if (tp1_pct is not None and tp1_pct > 0) else TP1_PCT

    sl_dist  = entry * SL_PCT     / 100
    tp1_dist = entry * eff_tp1_pct / 100
    tp_dist  = entry * TP2_PCT    / 100

    if direction.upper() == 'LONG':
        sl_price  = round(entry - sl_dist,  8)
        tp1_price = round(entry + tp1_dist, 8)
        tp_price  = round(entry + tp_dist,  8)
        be_price  = round(entry,             8)
    elif direction.upper() == 'SHORT':
        sl_price  = round(entry + sl_dist,  8)
        tp1_price = round(entry - tp1_dist, 8)
        tp_price  = round(entry - tp_dist,  8)
        be_price  = round(entry,             8)
    else:
        raise ValueError(f"Invalid direction: {direction!r} — must be LONG or SHORT")

    return {
        'sl_price':  sl_price,
        'tp1_price': tp1_price,
        'tp_price':  tp_price,
        'be_price':  be_price,
        'sl_pct':    SL_PCT,
        'tp1_pct':   eff_tp1_pct,
        'tp_pct':    TP2_PCT,
        'sl_dist':   round(sl_dist,  8),
        'tp1_dist':  round(tp1_dist, 8),
        'tp_dist':   round(tp_dist,  8),
    }


# ── RSI Helper ─────────────────────────────────────────────────────────────────
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


# ── EMA Helper ─────────────────────────────────────────────────────────────────
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
