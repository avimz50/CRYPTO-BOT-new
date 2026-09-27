#!/usr/bin/env python3
"""
feature_screen.py — Clean-start feature discovery for a new entry scorer.

WHY THIS EXISTS
    step1_validation.py proved the CURRENT scoring logic (including the
    live bot's own formula, approximated as bot_like) has no real edge.
    Before hand-crafting a replacement formula and hoping, this measures
    each CANDIDATE FEATURE's raw predictive power on its own — the
    Information Coefficient (correlation with the forward return) — so a
    new scorer is built only from features that actually show something,
    not from another guess dressed up as a formula.

METHOD
    At each decision bar i (bars[:i+1] only — same no-lookahead boundary
    as backtest_entries.py), compute every candidate feature, then look
    ahead (only for the LABEL, never for the feature) to the forward
    return over two fixed horizons (60min / 120min). Report each
    feature's correlation with that forward return, split TRAIN vs TEST
    and LONG vs SHORT (SHORT = -forward return), with a t-stat.

    Decision bars are sampled hourly (not every 15min) to reduce the
    overlap between consecutive forward-return windows — the windows
    still overlap somewhat (a 2h horizon sampled hourly shares half its
    span with its neighbour), so treat the t-stats as a screening
    signal, not a confirmatory test. A feature only earns attention if
    it is consistent in sign AND non-trivial in both TRAIN and TEST.

SAFETY
    Read-only public OHLCV via ccxt. Never touches the wallet, never
    places an order.

USAGE
    python3 feature_screen.py --days 60 --split 20
"""
from __future__ import annotations
import argparse
import bisect
import math
import sys
import time

import backtest_entries as be

STEP_BARS = 12          # sample a decision bar once per hour (12 x 5min)
HORIZONS = {'h1': 12, 'h2': 24}   # 60min, 120min forward, in 5min bars
MIN_HISTORY = 210        # same warmup as backtest_entries.features_at


def ema_series(vals: list, n: int) -> list:
    return be.ema_series(vals, n)


def rolling_percentile(vals: list, window: int) -> list:
    """Causal percentile rank of vals[i] within vals[i-window+1:i+1]."""
    out = []
    for i in range(len(vals)):
        lo = max(0, i - window + 1)
        window_vals = vals[lo:i + 1]
        if len(window_vals) < 5:
            out.append(50.0)
            continue
        v = vals[i]
        rank = sum(1 for x in window_vals if x <= v)
        out.append(100.0 * rank / len(window_vals))
    return out


def build_btc_regime(btc_bars: list):
    """Precompute a causal BTC trend regime (+1/-1) per BTC bar, and a
    lookup by timestamp (bisect on sorted timestamps)."""
    closes = [b[4] for b in btc_bars]
    e50 = ema_series(closes, 50)
    e200 = ema_series(closes, 200)
    ts = [b[0] for b in btc_bars]
    regime = [1.0 if e50[i] > e200[i] else -1.0 for i in range(len(closes))]
    return ts, regime


def lookup_regime(ts_list: list, regime: list, query_ts: int) -> float:
    idx = bisect.bisect_right(ts_list, query_ts) - 1
    if idx < 0:
        return 0.0
    return regime[idx]


def features_at_v2(bars: list, i: int, ts_list, regime) -> dict | None:
    if i < MIN_HISTORY:
        return None
    window = bars[:i + 1]
    closes = [b[4] for b in window]
    highs = [b[2] for b in window]
    lows = [b[3] for b in window]
    vols = [b[5] for b in window]
    price = closes[-1]

    e9 = ema_series(closes[-120:], 9)[-1]
    e21 = ema_series(closes[-120:], 21)[-1]
    e50 = ema_series(closes[-200:], 50)[-1]
    e200 = ema_series(closes[-210:], 200)[-1]
    macd = ema_series(closes[-120:], 12)[-1] - ema_series(closes[-120:], 26)[-1]
    rsi = be.rsi_at(closes)
    atr_pct = be.atr_pct_at(window)

    atr_hist = [be.atr_pct_at(window[:j + 1]) for j in
                range(max(0, len(window) - 100), len(window))]
    atr_pctile = (100.0 * sum(1 for x in atr_hist if x <= atr_pct)
                  / len(atr_hist)) if atr_hist else 50.0

    vol_mean20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0.0
    vol_ratio = (vols[-1] / vol_mean20) if vol_mean20 else 0.0
    vol_mean5 = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0.0
    vol_trend = (vol_mean5 / vol_mean20) if vol_mean20 else 1.0

    dc_high = max(highs[-50:])
    dc_low = min(lows[-50:])
    donchian_pos = ((price - dc_low) / (dc_high - dc_low)
                    if dc_high > dc_low else 0.5)

    ts = bars[i][0]
    hour = time.gmtime(ts / 1000).tm_hour
    hour_sin = math.sin(2 * math.pi * hour / 24)
    hour_cos = math.cos(2 * math.pi * hour / 24)

    btc_regime = lookup_regime(ts_list, regime, ts)

    return dict(
        ema9_21_gap=(e9 - e21) / price * 100,
        ema21_50_gap=(e21 - e50) / price * 100,
        dist_ema200=(price - e200) / e200 * 100,
        macd_norm=macd / price * 100,
        rsi_c=rsi - 50.0,
        atr_pct=atr_pct,
        atr_pctile=atr_pctile - 50.0,
        vol_ratio=vol_ratio,
        vol_trend=vol_trend,
        donchian_pos=donchian_pos - 0.5,
        chg_1h=(price / closes[-12] - 1) * 100 if len(closes) > 12 else 0.0,
        chg_24h=(price / closes[-288] - 1) * 100 if len(closes) > 288 else 0.0,
        hour_sin=hour_sin,
        hour_cos=hour_cos,
        btc_regime=btc_regime,
    )


def forward_return_pct(bars: list, i: int, h: int) -> float | None:
    j = i + 1 + h
    if j >= len(bars):
        return None
    entry = bars[i + 1][1]   # next bar's open
    exitp = bars[j][4]       # close h bars later
    if not entry:
        return None
    return (exitp - entry) / entry * 100


def collect(data: dict, ts_list, regime, label: str) -> list:
    rows = []
    for sym, bars in data.items():
        for i in range(MIN_HISTORY, len(bars) - 2 - max(HORIZONS.values()),
                        STEP_BARS):
            f = features_at_v2(bars, i, ts_list, regime)
            if f is None:
                continue
            fwd = {k: forward_return_pct(bars, i, h) for k, h in HORIZONS.items()}
            if any(v is None for v in fwd.values()):
                continue
            f['_sym'], f['_fwd'] = sym, fwd
            rows.append(f)
    print(f"  [{label}] {len(rows)} sampled decision bars")
    return rows


FEATURES = ['ema9_21_gap', 'ema21_50_gap', 'dist_ema200', 'macd_norm',
            'rsi_c', 'atr_pct', 'atr_pctile', 'vol_ratio', 'vol_trend',
            'donchian_pos', 'chg_1h', 'chg_24h', 'hour_sin', 'hour_cos',
            'btc_regime']


def ic_report(rows: list, horizon_key: str, label: str) -> None:
    print(f"\n  --- horizon={horizon_key} ({label}) ---")
    print(f"  {'feature':<14}{'n':>6}  {'LONG r':>9}{'t':>7}  "
          f"{'SHORT r':>9}{'t':>7}")
    for feat in FEATURES:
        xs = [r[feat] for r in rows]
        fwd = [r['_fwd'][horizon_key] for r in rows]
        r_long, t_long = be.pearson(xs, fwd)
        neg_fwd = [-v for v in fwd]
        r_short, t_short = be.pearson(xs, neg_fwd)
        flag = ''
        if abs(t_long) > 2.5 or abs(t_short) > 2.5:
            flag = '  <-- look'
        print(f"  {feat:<14}{len(rows):>6}  {r_long:>+9.3f}{t_long:>+7.2f}  "
              f"{r_short:>+9.3f}{t_short:>+7.2f}{flag}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=60)
    ap.add_argument('--split', type=int, default=20)
    ap.add_argument('--symbols', type=str, default='')
    args = ap.parse_args()
    if args.split >= args.days:
        sys.exit("--split must be less than --days")

    import ccxt
    ex = ccxt.bitget({'enableRateLimit': True,
                      'options': {'defaultType': 'swap'}})
    ex.load_markets()
    syms = ([s.strip() for s in args.symbols.split(',') if s.strip()]
            or be.DEFAULT_SYMBOLS)
    if 'BTC/USDT' not in syms:
        syms = ['BTC/USDT'] + syms

    now_ms = ex.milliseconds()
    train_days = args.days - args.split
    train_since = now_ms - args.days * 86_400_000
    test_since = now_ms - args.split * 86_400_000
    need_train = train_days * 24 * 60 // be.BAR_MINUTES + 300
    need_test = args.split * 24 * 60 // be.BAR_MINUTES + 300

    print(f"[data] TRAIN {train_days}d / TEST {args.split}d, "
          f"{len(syms)} symbols (incl. BTC/USDT for regime)")

    def fetch_win(since_ms, need):
        out = {}
        for s in syms:
            b = be.fetch_symbol(ex, s, since_ms, need)
            if b:
                out[s] = b
        return out

    train_data = fetch_win(train_since, need_train)
    test_data = fetch_win(test_since, need_test)
    print(f"[data] usable: train={len(train_data)} test={len(test_data)}")

    if 'BTC/USDT' not in train_data or 'BTC/USDT' not in test_data:
        sys.exit("BTC/USDT data required for regime feature but missing")

    train_ts, train_regime = build_btc_regime(train_data['BTC/USDT'])
    test_ts, test_regime = build_btc_regime(test_data['BTC/USDT'])

    train_rows = collect(train_data, train_ts, train_regime, 'TRAIN')
    test_rows = collect(test_data, test_ts, test_regime, 'TEST')

    for hk in HORIZONS:
        print("\n" + "=" * 100)
        print(f"HORIZON {hk} = {HORIZONS[hk] * 5} minutes forward")
        print("=" * 100)
        ic_report(train_rows, hk, 'TRAIN')
        ic_report(test_rows, hk, 'TEST')

    print("\n" + "=" * 100)
    print("HOW TO READ THIS")
    print("=" * 100)
    print("  r is the correlation between the feature (known at decision time)")
    print("  and the forward return (LONG) or its negative (SHORT). A feature")
    print("  only deserves a place in a new scorer if the SAME direction shows")
    print("  |t| > ~2.5 in BOTH train and test, with the SAME sign. A feature")
    print("  that only works in one window is fitting noise.")
    print("  Windows overlap (hourly sampling, up to 2h horizon), so treat t")
    print("  as a screening heuristic, not a final significance test.")


if __name__ == '__main__':
    main()
