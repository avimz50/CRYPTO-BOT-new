"""
market_logic.py — Clean Base Rules 2026
Pure strategy engine: scoring, pattern detection, position sizing.
No global bot state. All functions take data as arguments and return results.
Imports only from config.py + pandas/pandas_ta.
"""
import time as _time
import pandas as pd
import pandas_ta as ta

from config import (
    BB_SQUEEZE_RATIO, BB_SQUEEZE_LOOKBACK, BB_SQUEEZE_BREAKOUT,
    VERBOSE_LOG, MIN_SCORE,
    RSI_VETO_LONG, RSI_VETO_SHORT, EMA_PROXIMITY_PCT, VOL_EMA_BYPASS_MULT,
    FNG_DEFAULTS,
    FNG_FEAR_THRESHOLD, FNG_GREED_THRESHOLD, FNG_PENALTY, FNG_BONUS,
    MOMENTUM_VOL_RATIO, MOMENTUM_EMA_FAST, MOMENTUM_EMA_MID, MOMENTUM_EMA_SLOW,
    MOMENTUM_MIN_VOL_SURGE,
)

# ── Fear & Greed Index — module-level cache (1h TTL) ──────────────────────────
_fng_cache: dict = {'value': 50, 'label': 'Neutral', 'ts': 0.0}


def get_fear_greed() -> tuple[int, str]:
    """Returns (value: int, label: str) from Alternative.me. Cached for 1 hour."""
    now = _time.time()
    if now - _fng_cache['ts'] < 3600:
        return _fng_cache['value'], _fng_cache['label']
    try:
        import requests as _req
        r = _req.get('https://api.alternative.me/fng/?limit=1', timeout=5)
        d = r.json()['data'][0]
        prev_val = _fng_cache['value']
        _fng_cache.update({'value': int(d['value']), 'label': d['value_classification'], 'ts': now})
        if _fng_cache['value'] != prev_val:
            print(f"[FNG] {prev_val} → {_fng_cache['value']} – {_fng_cache['label']}")
    except Exception as e:
        print(f"[FNG] Refresh error: {e}")
    return _fng_cache['value'], _fng_cache['label']


# ═══════════════════════════════════════════════════════════════════════════════
# Order Block Detection (ICT Concept) — NEW
# ═══════════════════════════════════════════════════════════════════════════════

def detect_order_blocks(df, direction: str, lookback: int = 50) -> tuple[bool, float, float, str]:
    """
    Detects ICT-style Order Blocks on the given OHLCV DataFrame.

    Definition:
      Bullish OB  — the last bearish candle (close < open) immediately before
                    3+ consecutive bullish candles with total move ≥ 1.5%.
                    Marks smart-money accumulation zone.
      Bearish OB  — the last bullish candle (close > open) immediately before
                    3+ consecutive bearish candles with total move ≥ 1.5%.
                    Marks smart-money distribution zone.

    Scoring:
      LONG  — price inside or within 1% above a Bullish OB → strong support (+3)
      SHORT — price inside or within 1% below a Bearish OB → strong resistance (+3)

    Returns: (found: bool, ob_high: float, ob_low: float, description: str)
    """
    try:
        if len(df) < lookback + 5:
            return False, 0.0, 0.0, "not enough data"

        recent = df.iloc[-lookback:].reset_index(drop=True)
        opens  = recent['open'].values
        closes = recent['close'].values
        highs  = recent['high'].values
        lows   = recent['low'].values
        n      = len(recent)
        price  = closes[-1]

        best_ob_high = 0.0
        best_ob_low  = 0.0
        best_dist    = float('inf')
        found        = False

        # Scan from newest to oldest, looking for impulse moves
        for i in range(n - 4, 2, -1):
            if direction == 'LONG':
                # Need a bearish candle at position i
                if closes[i] >= opens[i]:
                    continue
                # Check 3 consecutive bullish candles after it
                if i + 3 >= n:
                    continue
                is_impulse = (
                    closes[i + 1] > opens[i + 1] and
                    closes[i + 2] > opens[i + 2] and
                    closes[i + 3] > opens[i + 3]
                )
                if not is_impulse:
                    continue
                # Total impulse move must be ≥ 1.5%
                impulse_pct = (closes[i + 3] - closes[i]) / closes[i] * 100
                if impulse_pct < 1.5:
                    continue
                # OB zone = high/low of the bearish candle
                ob_high = highs[i]
                ob_low  = lows[i]

            else:  # SHORT
                # Need a bullish candle at position i
                if closes[i] <= opens[i]:
                    continue
                if i + 3 >= n:
                    continue
                is_impulse = (
                    closes[i + 1] < opens[i + 1] and
                    closes[i + 2] < opens[i + 2] and
                    closes[i + 3] < opens[i + 3]
                )
                if not is_impulse:
                    continue
                impulse_pct = (closes[i] - closes[i + 3]) / closes[i] * 100
                if impulse_pct < 1.5:
                    continue
                ob_high = highs[i]
                ob_low  = lows[i]

            if ob_high <= ob_low or ob_low <= 0:
                continue

            zone_mid = (ob_high + ob_low) / 2
            dist = abs(price - zone_mid) / price
            if dist < best_dist:
                best_dist    = dist
                best_ob_high = ob_high
                best_ob_low  = ob_low
                found        = True

        if not found:
            return False, 0.0, 0.0, "no Order Block found"

        zone_mid = (best_ob_high + best_ob_low) / 2
        gap_pct  = round((best_ob_high - best_ob_low) / best_ob_low * 100, 2)

        # Price position relative to OB
        in_zone = best_ob_low <= price <= best_ob_high
        if direction == 'LONG':
            # Price slightly above OB (recently swept) is also valid
            near_zone = not in_zone and price <= best_ob_high * 1.01
        else:
            # Price slightly below OB (recently swept) is also valid
            near_zone = not in_zone and price >= best_ob_low * 0.99

        if not (in_zone or near_zone):
            return False, best_ob_high, best_ob_low, (
                f"OB({direction}) zone=[{best_ob_low:.4g}–{best_ob_high:.4g}] "
                f"AWAY({best_dist * 100:.1f}%)"
            )

        status = "IN_ZONE" if in_zone else "NEAR"
        desc   = (
            f"OB({direction}) [{best_ob_low:.4g}–{best_ob_high:.4g}] "
            f"gap={gap_pct}% mid={zone_mid:.4g} {status}"
        )
        return True, best_ob_high, best_ob_low, desc

    except Exception as e:
        return False, 0.0, 0.0, f"OB error: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# Candlestick Pattern Scoring
# ═══════════════════════════════════════════════════════════════════════════════

def score_candles(df_15m, direction) -> tuple[int, str]:
    """
    Detects Japanese candlestick patterns on 15m timeframe — max 10 points.
    Returns: (points: int, pattern_name: str)
    """
    try:
        if len(df_15m) < 6:
            return 0, "no data"

        o3 = df_15m['open'].iloc[-4];  c3 = df_15m['close'].iloc[-4]
        h3 = df_15m['high'].iloc[-4];  l3 = df_15m['low'].iloc[-4]
        o2 = df_15m['open'].iloc[-3];  c2 = df_15m['close'].iloc[-3]
        h2 = df_15m['high'].iloc[-3];  l2 = df_15m['low'].iloc[-3]
        o1 = df_15m['open'].iloc[-2];  c1 = df_15m['close'].iloc[-2]
        h1 = df_15m['high'].iloc[-2];  l1 = df_15m['low'].iloc[-2]

        body1  = abs(c1 - o1);  range1 = h1 - l1 if h1 != l1 else 1e-10
        body2  = abs(c2 - o2);  body3  = abs(c3 - o3)
        upper_wick1 = h1 - max(c1, o1)
        lower_wick1 = min(c1, o1) - l1

        green1 = c1 > o1;  red1 = c1 < o1
        green2 = c2 > o2;  red2 = c2 < o2
        green3 = c3 > o3;  red3 = c3 < o3

        if direction == 'LONG':
            if (body1 < range1 * 0.35 and
                    lower_wick1 >= 2.0 * max(body1, range1 * 0.01) and
                    upper_wick1 <= body1 * 1.1):
                return 10, "Hammer"
            if red2 and green1 and c1 > o2 and o1 < c2:
                return 10, "Bullish Engulfing"
            if (red3 and body2 < body3 * 0.35 and green1 and c1 > (o3 + c3) / 2):
                return 10, "Morning Star"
            if green3 and green2 and green1 and c1 > c2 > c3:
                return 10, "Three White Soldiers"
            if (red2 and green1 and o1 >= c2 and c1 <= o2 and body1 < body2 * 0.5):
                return 5, "Bullish Harami"
        else:
            if (body1 < range1 * 0.35 and
                    upper_wick1 >= 2.0 * max(body1, range1 * 0.01) and
                    lower_wick1 <= body1 * 1.1):
                return 10, "Shooting Star"
            if green2 and red1 and c1 < o2 and o1 > c2:
                return 10, "Bearish Engulfing"
            if (green3 and body2 < body3 * 0.35 and red1 and c1 < (o3 + c3) / 2):
                return 10, "Evening Star"
            if red3 and red2 and red1 and c1 < c2 < c3:
                return 10, "Three Black Crows"
            if (green2 and red1 and o1 <= c2 and c1 >= o2 and body1 < body2 * 0.5):
                return 5, "Bearish Harami"

        return 0, "no pattern"

    except Exception as e:
        print(f"  score_candles error: {e}")
        return 0, "error"


# ═══════════════════════════════════════════════════════════════════════════════
# Bull/Bear Flag Pattern
# ═══════════════════════════════════════════════════════════════════════════════

def detect_flag(df, direction) -> tuple[bool, str]:
    """
    Detects Bull Flag (LONG) / Bear Flag (SHORT) pattern.
    Returns: (is_flag: bool, description: str)
    """
    try:
        if len(df) < 25:
            return False, "not enough data"

        recent  = df.iloc[-25:].reset_index(drop=True)
        closes  = recent['close'].values
        highs   = recent['high'].values
        lows    = recent['low'].values
        vols    = recent['volume'].values
        vol_avg = vols[:-2].mean() if len(vols) > 2 else 1.0

        best_pole_end     = None
        best_pole_move    = 0.0
        best_pole_vol_avg = 0.0

        for pole_len in range(3, 6):
            for start in range(0, 15):
                end = start + pole_len
                if end >= len(recent) - 4:
                    continue
                start_price = closes[start]
                end_price   = closes[end - 1]
                if start_price == 0:
                    continue
                pct_move = (end_price - start_price) / start_price * 100
                pole_vol = vols[start:end].mean()

                if direction == 'LONG' and pct_move >= 3.0 and pole_vol > vol_avg * 1.2:
                    if pct_move > best_pole_move:
                        best_pole_move    = pct_move
                        best_pole_end     = end
                        best_pole_vol_avg = pole_vol
                elif direction == 'SHORT' and pct_move <= -3.0 and pole_vol > vol_avg * 1.2:
                    if abs(pct_move) > abs(best_pole_move):
                        best_pole_move    = pct_move
                        best_pole_end     = end
                        best_pole_vol_avg = pole_vol

        if best_pole_end is None:
            return False, "no pole found"

        flag_df  = recent.iloc[best_pole_end:-1]
        flag_len = len(flag_df)

        if flag_len < 4 or flag_len > 10:
            return False, f"flag length {flag_len} not in 4–10 range"

        flag_high  = flag_df['high'].max()
        flag_low   = flag_df['low'].min()
        flag_range = flag_high - flag_low

        pole_range = abs(closes[best_pole_end - 1] - closes[max(0, best_pole_end - 5)])
        if pole_range > 0 and flag_range > pole_range * 0.6:
            return False, f"flag too wide ({flag_range:.4g} > 60% of pole {pole_range:.4g})"

        flag_vol_avg = flag_df['volume'].mean()
        if flag_vol_avg >= best_pole_vol_avg * 0.85:
            return False, f"volume not declining in flag"

        flag_drift = (flag_df['close'].iloc[-1] - flag_df['close'].iloc[0]) / flag_df['close'].iloc[0] * 100
        if direction == 'LONG'  and flag_drift >  2.0:
            return False, f"flag drifting up {flag_drift:.1f}%"
        if direction == 'SHORT' and flag_drift < -2.0:
            return False, f"flag drifting down {flag_drift:.1f}%"

        bo_candle = recent.iloc[-2]
        bo_vol    = bo_candle['volume']

        if direction == 'LONG':
            breakout_ok = bo_candle['close'] > flag_high
        else:
            breakout_ok = bo_candle['close'] < flag_low

        if not breakout_ok:
            return False, f"no breakout (high={flag_high:.4g} low={flag_low:.4g})"

        if vol_avg > 0 and bo_vol < vol_avg * 1.5:
            return False, f"breakout vol weak ({bo_vol/vol_avg:.2f}× < 1.5×)"

        lbl = "Bull" if direction == 'LONG' else "Bear"
        return True, (f"{lbl} Flag ✓ Pole{best_pole_move:+.1f}% "
                      f"| Flag {flag_len}c | BO×{bo_vol/vol_avg:.1f}")

    except Exception as e:
        return False, f"flag error: {str(e)[:50]}"


# ═══════════════════════════════════════════════════════════════════════════════
# Fair Value Gap (FVG) — ICT Concept
# ═══════════════════════════════════════════════════════════════════════════════

def detect_fvg(df, direction: str, lookback: int = 20) -> tuple[bool, float, float, str]:
    """
    Detects Fair Value Gap (ICT).
    Bullish FVG: low[i] > high[i-2]
    Bearish FVG: high[i] < low[i-2]
    Returns: (found, fvg_top, fvg_bot, description)
    """
    try:
        if len(df) < lookback + 3:
            return False, 0.0, 0.0, "not enough data"

        recent = df.iloc[-(lookback + 3):].reset_index(drop=True)
        highs  = recent['high'].values
        lows   = recent['low'].values
        closes = recent['close'].values
        price  = closes[-1]

        best_fvg_top = 0.0
        best_fvg_bot = 0.0
        best_dist    = float('inf')
        found        = False

        for i in range(len(recent) - 2, 2, -1):
            if direction == 'LONG':
                fvg_bot = highs[i - 2]
                fvg_top = lows[i]
                if fvg_top > fvg_bot:
                    dist = abs(price - (fvg_bot + fvg_top) / 2) / price
                    if dist < best_dist:
                        best_dist = dist; best_fvg_top = fvg_top; best_fvg_bot = fvg_bot; found = True
            else:
                fvg_top = lows[i - 2]
                fvg_bot = highs[i]
                if fvg_top > fvg_bot:
                    dist = abs(price - (fvg_bot + fvg_top) / 2) / price
                    if dist < best_dist:
                        best_dist = dist; best_fvg_top = fvg_top; best_fvg_bot = fvg_bot; found = True

        if not found:
            return False, 0.0, 0.0, "no FVG found"

        gap_pct  = round((best_fvg_top - best_fvg_bot) / best_fvg_bot * 100, 2)
        zone_mid = round((best_fvg_top + best_fvg_bot) / 2, 8)

        in_zone   = best_fvg_bot <= price <= best_fvg_top
        near_zone = (not in_zone and (
            (direction == 'LONG'  and price <= best_fvg_top * 1.015) or
            (direction == 'SHORT' and price >= best_fvg_bot * 0.985)
        ))

        if in_zone:
            status = "IN_ZONE"
        elif near_zone:
            status = "NEAR"
        else:
            status = f"AWAY({best_dist*100:.1f}%)"

        desc = (f"FVG({direction}) gap={gap_pct}% "
                f"[{best_fvg_bot:.4g}–{best_fvg_top:.4g}] mid={zone_mid:.4g} {status}")
        return found and (in_zone or near_zone), best_fvg_top, best_fvg_bot, desc

    except Exception as e:
        return False, 0.0, 0.0, f"FVG error: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# Bollinger Band Squeeze
# ═══════════════════════════════════════════════════════════════════════════════

def detect_bb_squeeze(df, direction: str) -> tuple[bool, int, str]:
    """
    Detects Bollinger Band Squeeze (pre-breakout signal).
    Returns: (is_squeeze: bool, score_pts: int, description: str)
    """
    try:
        if len(df) < BB_SQUEEZE_LOOKBACK + 5:
            return False, 0, "not enough data"

        close = df['close']
        bb    = ta.bbands(close, length=20, std=2)
        if bb is None or bb.isna().all().all():
            return False, 0, "BB calc failed"

        bbu_col = next((c for c in bb.columns if 'BBU_' in c), None)
        bbl_col = next((c for c in bb.columns if 'BBL_' in c), None)
        bbm_col = next((c for c in bb.columns if 'BBM_' in c), None)
        if not all([bbu_col, bbl_col, bbm_col]):
            return False, 0, "BB columns missing"

        bbu = bb[bbu_col]; bbl = bb[bbl_col]; bbm = bb[bbm_col]
        width_series = (bbu - bbl) / bbm
        valid_w = width_series.dropna()
        if len(valid_w) < BB_SQUEEZE_LOOKBACK:
            return False, 0, "not enough BB data"

        curr_width = float(valid_w.iloc[-1])
        hist_avg   = float(valid_w.iloc[-(BB_SQUEEZE_LOOKBACK + 1):-1].mean())

        if hist_avg <= 0 or curr_width <= 0:
            return False, 0, "BB width invalid"

        squeeze_ratio = curr_width / hist_avg
        is_squeeze    = squeeze_ratio < BB_SQUEEZE_RATIO

        if not is_squeeze:
            return False, 0, f"no squeeze (width={squeeze_ratio:.2f}× avg)"

        price    = float(close.iloc[-1])
        curr_bbu = float(bbu.iloc[-1])
        curr_bbl = float(bbl.iloc[-1])
        curr_bbm = float(bbm.iloc[-1])

        if direction == 'LONG':
            breakout_starting = (price > curr_bbm) and (price >= curr_bbu * (1 - BB_SQUEEZE_BREAKOUT))
        else:
            breakout_starting = (price < curr_bbm) and (price <= curr_bbl * (1 + BB_SQUEEZE_BREAKOUT))

        squeeze_pct = round((1 - squeeze_ratio) * 100, 1)
        width_pct   = round(curr_width * 100, 2)

        if breakout_starting:
            desc = (f"BB_SQUEEZE ✅ width={width_pct}% ({squeeze_pct}% narrower) "
                    f"→ {'up' if direction == 'LONG' else 'down'} breakout starting")
            return True, 12, desc
        else:
            desc = (f"BB_SQUEEZE 🔄 width={width_pct}% ({squeeze_pct}% narrow) "
                    f"— contracting, breakout pending")
            return True, 6, desc

    except Exception as e:
        return False, 0, f"BB squeeze error: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# Volume Buildup
# ═══════════════════════════════════════════════════════════════════════════════

def detect_volume_buildup(df, candles: int = 5) -> tuple[int, str]:
    """
    Detects gradual volume accumulation (Smart Money entering before breakout).
    Returns: (score_pts: int, description: str)
    """
    try:
        if len(df) < candles + 2:
            return 0, "not enough data"

        vols   = df['volume'].iloc[-(candles + 1):].values
        streak = 0
        for i in range(1, len(vols)):
            if vols[i] >= vols[i - 1] * 1.05:
                streak += 1
            else:
                streak = 0

        if streak >= 4:
            avg_growth = round(((vols[-1] / vols[-streak - 1]) ** (1 / streak) - 1) * 100, 1)
            return 8, f"Vol buildup ✅ {streak} rising candles (~{avg_growth}%/candle)"
        elif streak == 3:
            return 4, f"Vol buildup 🔄 3 rising candles"
        else:
            return 0, f"no buildup (streak={streak})"

    except Exception as e:
        return 0, f"vol buildup error: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# RSI Divergence
# ═══════════════════════════════════════════════════════════════════════════════

def detect_rsi_divergence(df, direction: str, lookback: int = 30) -> tuple[int, str]:
    """
    Detects RSI divergence — early signal before reversal/breakout.
    Bullish: price lower low + RSI higher low.
    Bearish: price higher high + RSI lower high.
    Returns: (score_pts: int, description: str)
    """
    try:
        if len(df) < lookback + 5:
            return 0, "not enough data"

        df_s  = df.iloc[-lookback:].copy().reset_index(drop=True)
        close = df_s['close'].values
        rsi_s = ta.rsi(df_s['close'], length=14)
        if rsi_s is None or rsi_s.isna().all():
            return 0, "RSI calc failed"

        rsi = rsi_s.values
        n   = len(close)

        if direction == 'LONG':
            half  = n // 2
            a_idx = int(df_s['low'].iloc[:half].idxmin())
            b_idx = half + int(df_s['low'].iloc[half:].idxmin())
            price_a, price_b = close[a_idx], close[b_idx]
            rsi_a,   rsi_b   = rsi[a_idx],   rsi[b_idx]
            if price_b < price_a and rsi_b > rsi_a and not (pd.isna(rsi_a) or pd.isna(rsi_b)):
                price_diff = round((price_a - price_b) / price_a * 100, 2)
                rsi_diff   = round(rsi_b - rsi_a, 1)
                pts        = 10 if price_diff >= 1.5 else 5
                return pts, f"RSI Div ✅ Bullish: Low price -{price_diff}% but RSI +{rsi_diff}pts"
            return 0, "no bullish divergence"
        else:
            half  = n // 2
            a_idx = int(df_s['high'].iloc[:half].idxmax())
            b_idx = half + int(df_s['high'].iloc[half:].idxmax())
            price_a, price_b = close[a_idx], close[b_idx]
            rsi_a,   rsi_b   = rsi[a_idx],   rsi[b_idx]
            if price_b > price_a and rsi_b < rsi_a and not (pd.isna(rsi_a) or pd.isna(rsi_b)):
                price_diff = round((price_b - price_a) / price_a * 100, 2)
                rsi_diff   = round(rsi_a - rsi_b, 1)
                pts        = 10 if price_diff >= 1.5 else 5
                return pts, f"RSI Div ✅ Bearish: High price +{price_diff}% but RSI -{rsi_diff}pts"
            return 0, "no bearish divergence"

    except Exception as e:
        return 0, f"RSI div error: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3-Filter Momentum Gate
# ═══════════════════════════════════════════════════════════════════════════════

def momentum_gate(
    symbol: str,
    df_15m: pd.DataFrame | None,
    df_1h:  pd.DataFrame | None,
    direction: str,
    volume_usd_24h: float = 0.0,
    oi_data: dict | None = None,
) -> tuple[bool, str]:
    """
    Hard pre-trade gate — ALL 3 filters must pass for a symbol to reach scoring.

    Filter 1 — Liquidity  : 24h Volume > MOMENTUM_VOL_RATIO × OI Value (USD).
                             Skipped (soft-pass) when OI data is unavailable.
    Filter 2 — EMA Align  : EMA9 > EMA21 > EMA50 for LONG (reversed for SHORT)
                             on the 1H timeframe.
    Filter 3 — VWAP       : Current 1H close above VWAP (LONG) / below (SHORT).
                             Calculated manually as cumulative (HLC/3 × Vol) / cumVol.
                             Skipped (soft-pass) when the series is degenerate.

    Returns: (passed: bool, reason: str)
    Exceptions inside are caught — any crash yields a soft-pass so the main
    scan loop is never halted by a gate error.
    """
    try:
        # ── Filter 1: Liquidity — 24h Volume vs Open Interest ────────────────
        if oi_data is not None:
            oi_value = (
                oi_data.get('openInterestValue')
                or float((oi_data.get('info') or {}).get('size', 0) or 0)
            )
            try:
                oi_value = float(oi_value) if oi_value else 0.0
            except (TypeError, ValueError):
                oi_value = 0.0

            if oi_value > 0:
                ratio = volume_usd_24h / oi_value
                if ratio < MOMENTUM_VOL_RATIO:
                    return False, (
                        f"Filter1 Liquidity ✗ — "
                        f"Vol/OI={ratio:.1%} < {MOMENTUM_VOL_RATIO:.0%} "
                        f"(Vol=${volume_usd_24h/1e6:.1f}M  OI=${oi_value/1e6:.1f}M)"
                    )
        # OI unavailable → soft-pass Filter 1

        # ── Filter 2: EMA Alignment on 1H ────────────────────────────────────
        if df_1h is None or len(df_1h) < MOMENTUM_EMA_SLOW + 5:
            return False, (
                f"Filter2 EMA ✗ — insufficient 1H bars "
                f"(need ≥{MOMENTUM_EMA_SLOW + 5}, got {0 if df_1h is None else len(df_1h)})"
            )

        close_1h = df_1h['close']
        ema_fast_s = ta.ema(close_1h, length=MOMENTUM_EMA_FAST)
        ema_mid_s  = ta.ema(close_1h, length=MOMENTUM_EMA_MID)
        ema_slow_s = ta.ema(close_1h, length=MOMENTUM_EMA_SLOW)

        if ema_fast_s is None or ema_mid_s is None or ema_slow_s is None:
            return False, "Filter2 EMA ✗ — EMA series returned None"

        ema_f = float(ema_fast_s.iloc[-1])
        ema_m = float(ema_mid_s.iloc[-1])
        ema_s = float(ema_slow_s.iloc[-1])

        if any(pd.isna(v) for v in [ema_f, ema_m, ema_s]):
            return False, "Filter2 EMA ✗ — NaN in EMA values (insufficient history)"

        if direction == 'LONG':
            aligned = ema_f > ema_m > ema_s
        else:
            aligned = ema_f < ema_m < ema_s

        if not aligned:
            order = '>' if direction == 'LONG' else '<'
            return False, (
                f"Filter2 EMA Align ✗ — "
                f"EMA{MOMENTUM_EMA_FAST}={ema_f:.5g} {order} "
                f"EMA{MOMENTUM_EMA_MID}={ema_m:.5g} {order} "
                f"EMA{MOMENTUM_EMA_SLOW}={ema_s:.5g} not met"
            )

        # ── Filter 3: VWAP (manual calculation from 1H OHLCV) ────────────────
        try:
            typical_price = (df_1h['high'] + df_1h['low'] + df_1h['close']) / 3
            cum_vol       = df_1h['volume'].cumsum()
            if cum_vol.iloc[-1] <= 0:
                raise ValueError("zero cumulative volume")
            vwap_series = (typical_price * df_1h['volume']).cumsum() / cum_vol
            vwap_v      = float(vwap_series.iloc[-1])
            price_1h    = float(df_1h['close'].iloc[-1])

            if pd.isna(vwap_v) or vwap_v <= 0:
                raise ValueError(f"degenerate VWAP={vwap_v}")

            vwap_ok = (price_1h > vwap_v) if direction == 'LONG' else (price_1h < vwap_v)
            if not vwap_ok:
                side = 'above' if direction == 'LONG' else 'below'
                return False, (
                    f"Filter3 VWAP ✗ — "
                    f"price={price_1h:.5g} not {side} VWAP={vwap_v:.5g}"
                )
        except Exception as _ve:
            # Soft-pass: degenerate data should not kill the scan
            if VERBOSE_LOG:
                print(f"  [MomentumGate] VWAP soft-pass {symbol}: {_ve}")

        # ── All filters passed ────────────────────────────────────────────────
        return True, (
            f"Gate ✅  "
            f"EMA{MOMENTUM_EMA_FAST}={ema_f:.5g} "
            f"EMA{MOMENTUM_EMA_MID}={ema_m:.5g} "
            f"EMA{MOMENTUM_EMA_SLOW}={ema_s:.5g}"
        )

    except Exception as _e:
        # Fail-safe: unexpected crash → soft-pass so scan loop is never halted
        print(f"  [MomentumGate] ⚠️ unexpected error for {symbol} — soft-pass: {_e}")
        return True, f"Gate soft-pass (exception): {_e}"


# ═══════════════════════════════════════════════════════════════════════════════
# Master Scoring Function
# ═══════════════════════════════════════════════════════════════════════════════

def score_symbol(df_3h, df_1h, symbol: str, direction: str = 'LONG',
                 fng_v: int = None,
                 rsi_veto_long: int = None,
                 rsi_veto_short: int = None) -> tuple[int, str, float]:
    """
    Professional scoring system 0–100+ points.

    Scoring breakdown:
      Trend     (25): EMA200 4H (+15) + EMA200 1H (+10)
      MACD      (15): Signal Cross (+10) + Histogram (+5)
      RSI       (10): Sweet-spot (+10), Acceptable (+5)
      BB        (20): Above MidBB (+12) + touching correct Band (+8)
      Volume    (30): ×1.5 (+20) | ×2.0 (+30) | <×1.5 → VETO!
      Flag      (15): Bull/Bear Flag on 1H
      FVG       (10): Fair Value Gap in-zone/near on 1H
      OB        ( 3): Order Block (ICT) in-zone/near on 1H  ← NEW
      BB Squeeze(12): Pre-breakout squeeze on 1H
      Vol Build  (8): Smart Money volume accumulation
      RSI Div   (10): RSI divergence on 1H
      FNG  (-10/+5): trend-aligned filter (Fear→LONG-10/SHORT+5, Greed→LONG+5/SHORT-10)

    Returns: (score: int, breakdown: str, atr: float)
    """
    score = 0
    parts = []

    try:
        close_3h = df_3h['close']

        ema200_1h = ta.ema(close_3h, length=200)
        macd_df   = ta.macd(close_3h, fast=12, slow=26, signal=9)
        rsi_s     = ta.rsi(close_3h, length=14)
        bb_df     = ta.bbands(close_3h, length=20, std=2)
        atr_s     = ta.atr(df_3h['high'], df_3h['low'], close_3h, length=14)
        ema200_15 = ta.ema(df_1h['close'], length=200)

        if any(v is None for v in [ema200_1h, macd_df, rsi_s, bb_df, atr_s, ema200_15]):
            if VERBOSE_LOG:
                print(f"  [{symbol}] indicator calc failed")
            return 0, "indicator error", 0

        price      = close_3h.iloc[-1]
        ema200_v   = ema200_1h.iloc[-1]
        ema200_15v = ema200_15.iloc[-1]

        macd_col = next(c for c in macd_df.columns if c.startswith('MACD_'))
        sig_col  = next(c for c in macd_df.columns if c.startswith('MACDs_'))
        hist_col = next(c for c in macd_df.columns if c.startswith('MACDh_'))
        macd_v   = macd_df[macd_col].iloc[-1]
        sig_v    = macd_df[sig_col].iloc[-1]
        hist_v   = macd_df[hist_col].iloc[-1]
        hist_p   = macd_df[hist_col].iloc[-2]

        rsi_v    = rsi_s.iloc[-1]
        bb_mid_col = next(c for c in bb_df.columns if 'BBM_' in c)
        bb_mid   = bb_df[bb_mid_col].iloc[-1]
        atr_v    = atr_s.iloc[-1]

        vol_curr = df_3h['volume'].iloc[-2]
        vol_avg  = df_3h['volume'].iloc[-12:-2].mean()
        vol_rat  = vol_curr / vol_avg if vol_avg > 0 else 0

        if any(pd.isna(v) for v in [ema200_v, ema200_15v, macd_v, sig_v,
                                      hist_v, hist_p, rsi_v, bb_mid, atr_v]):
            if VERBOSE_LOG:
                print(f"  [{symbol}] NaN in indicators")
            return 0, "NaN values", 0

        # ── Anti-FOMO Vetoes ──────────────────────────────────────────────────
        ema_gap_pct      = (price - ema200_v) / ema200_v * 100
        vol_ema_bypassed = (vol_rat >= VOL_EMA_BYPASS_MULT)
        if vol_ema_bypassed and abs(ema_gap_pct) > EMA_PROXIMITY_PCT:
            parts.append(f"EMA_bypass(vol×{vol_rat:.1f}≥{VOL_EMA_BYPASS_MULT})")
        else:
            if direction == 'LONG' and ema_gap_pct > EMA_PROXIMITY_PCT:
                return 0, f"EMA200 chase veto ({ema_gap_pct:.1f}% above EMA200)", atr_v
            if direction == 'SHORT' and ema_gap_pct < -EMA_PROXIMITY_PCT:
                return 0, f"EMA200 chase veto ({abs(ema_gap_pct):.1f}% below EMA200)", atr_v

        last_c  = df_3h.iloc[-2]
        c_body  = abs(last_c['close'] - last_c['open'])
        c_upper = last_c['high'] - max(last_c['close'], last_c['open'])
        c_lower = min(last_c['close'], last_c['open']) - last_c['low']
        if direction == 'LONG' and c_upper > c_body and c_body > 0:
            return 0, f"Wick rejection LONG (upper wick {c_upper:.4g} > body {c_body:.4g})", atr_v
        if direction == 'SHORT' and c_lower > c_body and c_body > 0:
            return 0, f"Wick rejection SHORT (lower wick {c_lower:.4g} > body {c_body:.4g})", atr_v

        # ── 1. Trend / EMA200 — 25 pts ────────────────────────────────────────
        t1h  = price > ema200_v   if direction == 'LONG' else price < ema200_v
        t15m = price > ema200_15v if direction == 'LONG' else price < ema200_15v
        t_pts = (15 if t1h else 0) + (10 if t15m else 0)
        score += t_pts
        parts.append(f"Trend={t_pts}/25")
        if VERBOSE_LOG:
            print(f"  [{symbol}] {direction} | Trend={t_pts} (4H={'✓' if t1h else '✗'} 1H={'✓' if t15m else '✗'})")

        # ── 2. MACD — 15 pts ──────────────────────────────────────────────────
        macd_ok = (macd_v > sig_v)  if direction == 'LONG' else (macd_v < sig_v)
        hist_ok = (hist_v > hist_p) if direction == 'LONG' else (hist_v < hist_p)
        m_pts = (10 if macd_ok else 0) + (5 if hist_ok else 0)
        score += m_pts
        parts.append(f"MACD={m_pts}/15")

        # ── 3. RSI Safety Gate + Scoring — 10 pts ────────────────────────────
        # STRICT: LONG forbidden if RSI > 55 | SHORT forbidden if RSI < 45
        _rvl = rsi_veto_long  if rsi_veto_long  is not None else RSI_VETO_LONG
        _rvs = rsi_veto_short if rsi_veto_short is not None else RSI_VETO_SHORT
        if direction == 'LONG' and rsi_v > _rvl:
            return 0, f"RSI Safety Gate: LONG אסור (RSI={rsi_v:.1f} > {_rvl} — overbought)", atr_v
        if direction == 'SHORT' and rsi_v < _rvs:
            return 0, f"RSI Safety Gate: SHORT אסור (RSI={rsi_v:.1f} < {_rvs} — oversold)", atr_v

        # Scoring — within the allowed RSI window
        if direction == 'LONG':
            rsi_ideal = 25 <= rsi_v <= 48   # oversold zone = best LONG setup
            rsi_ok    = 20 <= rsi_v <= 62   # full allowed window
        else:
            rsi_ideal = 55 <= rsi_v <= 75   # overbought zone = best SHORT setup
            rsi_ok    = 40 <= rsi_v <= 80   # full allowed window

        r_pts = 10 if rsi_ideal else (5 if rsi_ok else 0)
        score += r_pts
        parts.append(f"RSI={r_pts}/10(={rsi_v:.0f})")

        # ── 4. Bollinger Bands — 20 pts ───────────────────────────────────────
        try:
            bb_lower_col = next(c for c in bb_df.columns if 'BBL_' in c)
            bb_upper_col = next(c for c in bb_df.columns if 'BBU_' in c)
            bb_lower     = bb_df[bb_lower_col].iloc[-1]
            bb_upper     = bb_df[bb_upper_col].iloc[-1]
        except Exception:
            bb_lower = bb_upper = None

        bb_mid_ok    = (price > bb_mid) if direction == 'LONG' else (price < bb_mid)
        bb_band_touch = False
        if bb_lower is not None and bb_upper is not None and bb_upper != bb_lower:
            if direction == 'LONG':
                bb_band_touch = price <= bb_lower * 1.02
            else:
                bb_band_touch = price >= bb_upper * 0.98

        b_pts = (12 if bb_mid_ok else 0) + (8 if bb_band_touch else 0)
        score += b_pts
        parts.append(f"BB={b_pts}/20")

        # ── 5. Volume — 30 pts | HARD VETO <×1.5 ─────────────────────────────
        if vol_rat < 1.5:
            return 0, f"Volume VETO: {vol_rat:.2f}× < 1.5× avg (min threshold)", atr_v

        v_pts = 30 if vol_rat >= 2.0 else 20
        score += v_pts
        parts.append(f"Vol={v_pts}/30(×{vol_rat:.1f})")

        # ── 6. Candles — filtered (noise) ─────────────────────────────────────
        parts.append("Candles=0/0(filtered)")

        # ── 7a. Flag Pattern — +15 pts ────────────────────────────────────────
        is_flag, flag_desc = detect_flag(df_1h, direction)
        if is_flag:
            score += 15
            parts.append(f"Flag=+15({flag_desc})")
        else:
            parts.append(f"Flag=0(no:{flag_desc[:30]})")

        # ── 7b. FVG — Fair Value Gap — +10 pts ───────────────────────────────
        fvg_hit, _fvg_top, _fvg_bot, fvg_desc = detect_fvg(df_1h, direction, lookback=20)
        if fvg_hit:
            score += 10
            parts.append(f"FVG=+10({fvg_desc})")
        else:
            parts.append(f"FVG=0({fvg_desc[:35]})")

        # ── 7c. Order Block (ICT) — +3 pts ← NEW ─────────────────────────────
        ob_hit, _ob_high, _ob_low, ob_desc = detect_order_blocks(df_1h, direction, lookback=50)
        if ob_hit:
            score += 3
            parts.append(f"OB=+3({ob_desc})")
            if VERBOSE_LOG:
                print(f"  [{symbol}] {direction} | ORDER BLOCK HIT: {ob_desc}")
        else:
            parts.append(f"OB=0({ob_desc[:35]})")

        # ── 7d. BB Squeeze — +12/+6 pts ──────────────────────────────────────
        sq_hit, sq_pts, sq_desc = detect_bb_squeeze(df_1h, direction)
        if sq_pts > 0:
            score += sq_pts
            parts.append(f"Squeeze=+{sq_pts}({sq_desc[:40]})")
        else:
            parts.append(f"Squeeze=0({sq_desc[:30]})")

        # ── 7e. Volume Buildup — +8/+4 pts ───────────────────────────────────
        vb_pts, vb_desc = detect_volume_buildup(df_1h, candles=5)
        if vb_pts > 0:
            score += vb_pts
            parts.append(f"VolBuild=+{vb_pts}({vb_desc[:40]})")
        else:
            parts.append(f"VolBuild=0({vb_desc[:25]})")

        # ── 7f. RSI Divergence — +10/+5 pts ──────────────────────────────────
        rd_pts, rd_desc = detect_rsi_divergence(df_1h, direction, lookback=30)
        if rd_pts > 0:
            score += rd_pts
            parts.append(f"RSIDiv=+{rd_pts}({rd_desc[:45]})")
        else:
            parts.append(f"RSIDiv=0({rd_desc[:25]})")

        # ── 7g. Pump Surge Bonus — +8 pts ────────────────────────────────────
        # מזהה "Pump Fade" סטאפ: נפח קיצוני ×3.0+ ו-RSI בזון האידיאלי לכיוון ההפוך.
        # SKYAI: נפח ×3.3, RSI=69 (SHORT ideal) → בדיוק הסטאפ הזה.
        pump_surge = False
        if vol_rat >= 3.0:
            if direction == 'SHORT' and rsi_ideal:   # RSI overbought (55–75)
                pump_surge = True
            elif direction == 'LONG' and rsi_ideal:  # RSI oversold  (25–48)
                pump_surge = True
        if pump_surge:
            score += 8
            parts.append(f"PumpSurge=+8(vol×{vol_rat:.1f}+RSI={rsi_v:.0f})")
        else:
            parts.append(f"PumpSurge=0(vol×{vol_rat:.1f})")

        # ── 8. Fear & Greed — trend-aligned filter ───────────────────────────
        # Fear  (FNG < FNG_FEAR_THRESHOLD=35):  LONG -10, SHORT +5
        # Greed (FNG > FNG_GREED_THRESHOLD=65): LONG +5,  SHORT -10
        # Neutral (35–65): no adjustment
        # Position sizing is NEVER touched here — score only.
        if fng_v is None:
            fng_v, _ = get_fear_greed()

        if direction == 'LONG':
            if   fng_v < FNG_FEAR_THRESHOLD:  fng_adj = FNG_PENALTY  # Fear  → discourage LONG
            elif fng_v > FNG_GREED_THRESHOLD: fng_adj = FNG_BONUS    # Greed → reward LONG
            else:                             fng_adj = 0
        else:  # SHORT
            if   fng_v < FNG_FEAR_THRESHOLD:  fng_adj = FNG_BONUS    # Fear  → reward SHORT
            elif fng_v > FNG_GREED_THRESHOLD: fng_adj = FNG_PENALTY  # Greed → discourage SHORT
            else:                             fng_adj = 0

        score = max(0, min(100, score + fng_adj))
        sign  = f"+{fng_adj}" if fng_adj >= 0 else str(fng_adj)
        parts.append(f"FNG={fng_v}({sign})")

        breakdown = " | ".join(parts) + f"  →  TOTAL={score}/100"
        if score >= MIN_SCORE:
            print(f"[Score] 🟢 {symbol} {direction} SCORE={score}/100 | {breakdown}")
        elif VERBOSE_LOG:
            print(f"[Score] 🔴 {symbol} {direction} {score}/100 — skip")
        return score, breakdown, atr_v

    except Exception as e:
        print(f"[Score] ⚠️ {symbol} error: {e}")
        return 0, str(e), 0


# ── Sentiment Check ────────────────────────────────────────────────────────────
# Runtime FNG thresholds are injected by bot.py via set_sentiment_thresholds().
# Defaults match FNG_DEFAULTS so the module is safe to use before bot.py syncs.
_sentiment_thresholds: dict = dict(FNG_DEFAULTS)
_last_sentiment_action: str = ""


def set_sentiment_thresholds(extreme_fear: int, fear: int, greed: int) -> None:
    """
    Called by bot.py to push the runtime FNG thresholds into this module.
    Invoke once at startup and after every Telegram /fng or API threshold change.
    """
    global _sentiment_thresholds
    _sentiment_thresholds['extreme_fear'] = extreme_fear
    _sentiment_thresholds['fear']         = fear
    _sentiment_thresholds['greed']        = greed


def sentiment_check(context: str = "scan") -> tuple:
    """
    Returns (fng_v: int, label: str, action: str).
    Prints only when the regime label changes (Low-Resource mode).
    Thresholds are kept in sync via set_sentiment_thresholds().
    """
    global _last_sentiment_action
    fng_v, lbl = get_fear_greed()
    extreme = _sentiment_thresholds['extreme_fear']
    fear    = _sentiment_thresholds['fear']
    greed   = _sentiment_thresholds['greed']

    if fng_v < extreme:
        action = f"FEAR (FNG={fng_v})"
    elif fng_v <= fear:
        action = f"FEAR (FNG={fng_v})"
    elif fng_v >= greed:
        action = f"GREED FILTER (FNG={fng_v})"
    else:
        action = f"NEUTRAL (FNG={fng_v})"

    if action.split('(')[0] != _last_sentiment_action.split('(')[0]:
        print(f"[Sentiment] {_last_sentiment_action or 'START'} → {action} [{context}]")
        _last_sentiment_action = action
    return fng_v, lbl, action


# ── Adaptive Threshold Engine ──────────────────────────────────────────────────
# FNG band: (max_fng_inclusive, label, min_score_delta, rsi_veto_long, rsi_veto_short)
_FNG_BANDS: list[tuple] = [
    (19,  'Extreme Fear',  +10, 55, 65),
    (39,  'Fear',          +5,  58, 62),
    (59,  'Neutral',        0,  62, 60),
    (74,  'Greed',         -3,  65, 58),
    (100, 'Extreme Greed', +3,  63, 60),
]

# Regime + direction → additional delta on MIN_SCORE
_REGIME_DIR_DELTA: dict[tuple, int] = {
    ('BULL', 'LONG'):    -3,   # with-trend long — slightly easier
    ('BULL', 'SHORT'):  +15,   # counter-trend short — very hard
    ('BEAR', 'SHORT'):   -3,   # with-trend short — slightly easier
    ('BEAR', 'LONG'):   +10,   # counter-trend long — hard
    ('NEUTRAL', 'LONG'):  0,
    ('NEUTRAL', 'SHORT'): 0,
}


def adaptive_threshold(fng_value, btc_regime: str, direction: str
                       ) -> tuple[int, int, int, str]:
    """
    Compute effective (min_score, rsi_veto_long, rsi_veto_short, label) from
    current FNG value, BTC regime, and trade direction.

    Base values come from config (MIN_SCORE / RSI_VETO_LONG / RSI_VETO_SHORT).
    This function adds regime-aware deltas on top.

    Robust fallback: if fng_value is None or out of 0-100 range, falls back to
    Neutral (FNG=50) so the bot keeps trading even when the FNG API is down.

    Returns:
        (eff_min_score, eff_rsi_veto_long, eff_rsi_veto_short, label_str)
    """
    # ── Safe fallback — FNG API may be unavailable ─────────────────────────
    try:
        fng_int = int(fng_value)
        if not (0 <= fng_int <= 100):
            raise ValueError
    except (TypeError, ValueError):
        fng_int = 50  # Neutral — bot keeps trading

    btc_regime = (btc_regime or 'NEUTRAL').upper()
    direction  = (direction  or 'LONG').upper()

    # ── FNG band lookup ───────────────────────────────────────────────────
    fng_delta  = 0
    rsi_long   = RSI_VETO_LONG
    rsi_short  = RSI_VETO_SHORT
    band_label = 'Neutral'
    for max_fng, label, delta, rv_long, rv_short in _FNG_BANDS:
        if fng_int <= max_fng:
            fng_delta  = delta
            rsi_long   = rv_long
            rsi_short  = rv_short
            band_label = label
            break

    # ── Regime + direction delta ──────────────────────────────────────────
    regime_delta = _REGIME_DIR_DELTA.get((btc_regime, direction), 0)

    # ── Compute & clamp ───────────────────────────────────────────────────
    eff_min = MIN_SCORE + fng_delta + regime_delta
    eff_min = max(60, min(95, eff_min))

    # ── Human-readable label for logs / Telegram ─────────────────────────
    fng_part    = (f"FNG={fng_int}({band_label})→{fng_delta:+d}"
                   if fng_delta != 0 else f"FNG={fng_int}({band_label})")
    regime_part = (f"{btc_regime}+{direction}→{regime_delta:+d}"
                   if regime_delta != 0 else "")
    parts       = [p for p in [fng_part, regime_part] if p]
    label_str   = " | ".join(parts) or "Neutral"

    return eff_min, rsi_long, rsi_short, label_str


# ── Adaptive Exit Parameters ──────────────────────────────────────────────────
# FNG band → (base_tp1_pct, base_max_dur_min)
_EXIT_FNG_BANDS: list[tuple] = [
    (19,  'Extreme Fear',  0.8,  90),
    (39,  'Fear',          1.0, 120),
    (59,  'Neutral',       1.0, 150),
    (74,  'Greed',         1.5, 180),
    (100, 'Extreme Greed', 2.0, 210),
]

# Regime → (tp1_pct delta, duration delta minutes)
_EXIT_REGIME_DELTA: dict[str, tuple] = {
    'BULL':    (+0.2, +30),
    'BEAR':    (-0.2, -30),
    'NEUTRAL': ( 0.0,   0),
}


def adaptive_exit_params(fng_value, regime: str) -> tuple[float, int, str]:
    """
    Compute adaptive exit parameters from current FNG + BTC regime.

    Mirrors adaptive_threshold() logic but for exit management:
      - Higher FNG (greed) + BULL regime → larger TP1, more time (trending market)
      - Lower FNG (fear) + BEAR regime  → smaller TP1, less time (volatile market)

    Robust fallback: FNG=None or out-of-range → treated as Neutral (50).

    Returns:
        (tp1_pct, max_duration_min, label_str)
        tp1_pct          — TP1 trigger level (%), clamped 0.5–2.5
        max_duration_min — max trade time without TP1 hit (min), clamped 60–240
        label_str        — human-readable description for logs / Telegram
    """
    try:
        fng_int = int(fng_value)
        if not (0 <= fng_int <= 100):
            raise ValueError
    except (TypeError, ValueError):
        fng_int = 50  # Neutral fallback — bot keeps trading even if FNG API is down

    regime = (regime or 'NEUTRAL').upper()

    base_tp1   = 1.0
    base_dur   = 150
    band_label = 'Neutral'
    for max_fng, label, tp1, dur in _EXIT_FNG_BANDS:
        if fng_int <= max_fng:
            base_tp1   = tp1
            base_dur   = dur
            band_label = label
            break

    tp1_delta, dur_delta = _EXIT_REGIME_DELTA.get(regime, (0.0, 0))

    eff_tp1 = round(max(0.5, min(2.5, base_tp1 + tp1_delta)), 1)
    eff_dur = max(60, min(240, base_dur + dur_delta))

    label_str = (
        f"FNG={fng_int}({band_label}) | {regime} "
        f"→ TP1={eff_tp1}% MaxDur={eff_dur}min"
    )
    return eff_tp1, eff_dur, label_str


def _test_adaptive_threshold() -> None:
    """
    Dry-run sanity check.  Run directly:  python market_logic.py
    Prints the computed thresholds for key scenarios so you can verify the math.
    """
    cases = [
        (15,  'BULL',    'LONG',  'Extreme Fear + BULL LONG  → expect ~72  (85-13?)'),
        (15,  'BULL',    'SHORT', 'Extreme Fear + BULL SHORT → expect 90   (85+15-10 clamp)'),
        (15,  'NEUTRAL', 'LONG',  'Extreme Fear + NEUTRAL    → expect 85   (75+10)'),
        (75,  'BULL',    'LONG',  'Extreme Greed + BULL LONG → expect 75   (75+3-3)'),
        (75,  'BEAR',    'SHORT', 'Extreme Greed + BEAR SHORT→ expect 75   (75+3-3)'),
        (75,  'BULL',    'SHORT', 'Extreme Greed + BULL SHORT→ expect 93   (75+3+15)'),
        (50,  'NEUTRAL', 'LONG',  'Pure Neutral              → expect 75   (base)'),
        (None,'BULL',    'LONG',  'FNG=None fallback         → expect 72   (Neutral+BULL LONG)'),
    ]
    print("\n╔══ adaptive_threshold() — Dry Run ══════════════════════════════════")
    print(f"  {'FNG':>6}  {'Regime':>8}  {'Dir':>6} │ {'eff_min':>8}  {'RSI_L':>6}  {'RSI_S':>6}  Label")
    print("  " + "─" * 72)
    for fng, regime, dirn, note in cases:
        eff_min, rsi_l, rsi_s, lbl = adaptive_threshold(fng, regime, dirn)
        fng_d = str(fng) if fng is not None else "None"
        print(f"  {fng_d:>6}  {regime:>8}  {dirn:>6} │ {eff_min:>8}  {rsi_l:>6}  {rsi_s:>6}  {lbl}")
    print("╚" + "═" * 74 + "\n")
