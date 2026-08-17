#!/usr/bin/env python3
"""
pattern_scorer.py — Liquidity-sweep detector for backtest_entries.py

THE HYPOTHESIS
    Stops cluster just beyond a recent swing high/low. Price is drawn there,
    trips them, and reverts. If that is a real flow effect rather than a chart
    shape, a bar that pierces the prior N-bar extreme and closes back inside
    should be followed by movement away from the swept side.

WHY THIS IS NOT "ANOTHER INDICATOR"
    EMA/RSI/MACD are smoothing transforms of close prices — by construction
    they carry less information than the raw series. A sweep is a claim about
    market microstructure, so it deserves its own test rather than being
    lumped in with the indicators that already failed.

LOOKAHEAD DISCIPLINE
    detect() may read bars[:i+1] and nothing else. The reference extreme comes
    from the window BEFORE bar i, so the sweep bar cannot define the level it
    is breaking. The reclaim must complete inside bar i — we never wait to see
    whether price came back, because at decision time we would not know.

OVERFITTING DISCIPLINE
    Parameters are pre-registered, not tuned. Quality modifiers are two, fixed,
    and additive. Run --param-sweep to check the result is a plateau and not a
    single lucky setting.
"""
from __future__ import annotations


class PatternScorer:
    """
    Binary trigger with two pre-registered quality modifiers.

    A pattern is present or absent — forcing it onto a 0-100 scale would invent
    gradations the data does not support. Base fires at 60; the modifiers can
    lift a textbook example to 100, and nothing else moves the number.
    """

    def __init__(self, lookback: int = 10, min_pierce_atr: float = 0.10,
                 min_reclaim_frac: float = 0.50, base: float = 60.0):
        self.lookback = lookback              # bars defining the prior extreme
        self.min_pierce_atr = min_pierce_atr  # sweep must pierce by >= this x ATR
        self.min_reclaim_frac = min_reclaim_frac  # close must recover this much of the bar
        self.base = base

    # ── detection ────────────────────────────────────────────────────────────
    def detect(self, bars: list, i: int, direction: str, atr_pct: float) -> float:
        """
        Returns 0..100. Reads bars[:i+1] only.

        LONG  <- sweep of the prior low  (sellers trapped)
        SHORT <- sweep of the prior high (buyers trapped)
        """
        if i < self.lookback + 2:
            return 0.0

        window = bars[i - self.lookback - 1:i]   # strictly BEFORE the sweep bar
        if len(window) < self.lookback:
            return 0.0

        _, o, h, l, c, _v = bars[i]
        rng = h - l
        if rng <= 0 or c <= 0:
            return 0.0

        # ATR expressed in price so the pierce test is volatility-relative and
        # not a fixed percentage that means different things on different coins.
        atr_price = (atr_pct / 100.0) * c
        if atr_price <= 0:
            return 0.0

        if direction == 'LONG':
            prior = min(b[3] for b in window)
            if l >= prior:                       # nothing swept
                return 0.0
            pierce = prior - l
            reclaim = (c - l) / rng              # how much of the bar was won back
            reclaimed_level = c > prior          # closed back above the swept low
        else:
            prior = max(b[2] for b in window)
            if h <= prior:
                return 0.0
            pierce = h - prior
            reclaim = (h - c) / rng
            reclaimed_level = c < prior

        if not reclaimed_level:
            return 0.0
        if pierce < self.min_pierce_atr * atr_price:
            return 0.0                            # a graze, not a sweep
        if reclaim < self.min_reclaim_frac:
            return 0.0                            # closed near the extreme

        # ── two fixed modifiers, nothing else ────────────────────────────────
        score = self.base
        # 1. depth of the sweep, capped so one outlier cannot dominate
        score += min((pierce / atr_price) * 10.0, 20.0)
        # 2. strength of the reclaim
        score += (reclaim - self.min_reclaim_frac) / (1.0 - self.min_reclaim_frac) * 20.0
        return min(score, 100.0)


# Pre-registered default. Changing this after seeing results is how backtests lie.
DEFAULT = PatternScorer()


def score_sweep(f: dict, direction: str) -> float:
    """
    Adapter for the SCORERS table in backtest_entries.py.

    The runner passes a feature dict; the raw bars and index are injected by
    the runner as f['_bars'] and f['_i'] so the detector can see structure
    that a flat feature vector cannot express.
    """
    bars = f.get('_bars')
    i = f.get('_i')
    if bars is None or i is None:
        return 0.0
    return DEFAULT.detect(bars, i, direction, f.get('atr_pct', 0.0))
