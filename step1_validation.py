#!/usr/bin/env python3
"""
step1_validation.py — Walk-forward, direction-split validation of the entry
scorers in backtest_entries.py, for the Step-1 gate in the v2 spec doc.

WHY THIS EXISTS (on top of backtest_entries.py)
    backtest_entries.py already proves the harness has no lookahead and
    reports a random control alongside every real scorer. Two things it does
    NOT do that the spec's validation gate requires:
      1. Walk-forward: the same scorer must be checked on a TRAIN window and
         then on a separate, later TEST window - an edge that only shows up
         in one window is not trusted.
      2. Direction split: trade_history.csv showed LONG and SHORT behaving
         very differently under the live bot. Averaging them together can
         hide a real edge in one direction or a real problem in the other.
    This script reuses every function from backtest_entries.py unchanged
    (same no-lookahead boundary, same exit simulation, same fee/slip model)
    and only adds the TRAIN/TEST split and the LONG/SHORT breakdown on top.

SAFETY
    Read-only public OHLCV via ccxt. Never touches the wallet, never places
    an order.

USAGE
    python3 step1_validation.py --days 60 --split 30
    (the most recent --split days = TEST; the days before that = TRAIN)
"""
from __future__ import annotations
import argparse
import sys
import time

import backtest_entries as be


def split_report(trades: list, label: str) -> None:
    if not trades:
        print(f"    {label:<6} ALL   no trades")
        return
    net_all = [t['net'] for t in trades]
    s = be.summarize(net_all)
    r, _ = be.pearson([t['score'] for t in trades], net_all)
    flag = 'EDGE' if s['ci_lo'] > 0 else ('NEGATIVE' if s['ci_hi'] < 0 else 'noise')
    print(f"    {label:<6} ALL   n={s['n']:>4}  net={s['total']:+9.2f}  "
          f"mean={s['mean']:+7.3f}  win={s['win']:>5.1f}%  "
          f"CI=[{s['ci_lo']:+7.3f},{s['ci_hi']:+7.3f}]  r={r:+.3f}  {flag}")
    for d in ('LONG', 'SHORT'):
        sub = [t for t in trades if t['direction'] == d]
        if len(sub) < 2:
            print(f"    {label:<6} {d:<5} n={len(sub)} - too few to score")
            continue
        netd = [t['net'] for t in sub]
        sd = be.summarize(netd)
        rd, _ = be.pearson([t['score'] for t in sub], netd)
        flagd = ('EDGE' if sd['ci_lo'] > 0
                 else ('NEGATIVE' if sd['ci_hi'] < 0 else 'noise'))
        print(f"    {label:<6} {d:<5} n={sd['n']:>4}  net={sd['total']:+9.2f}  "
              f"mean={sd['mean']:+7.3f}  win={sd['win']:>5.1f}%  "
              f"CI=[{sd['ci_lo']:+7.3f},{sd['ci_hi']:+7.3f}]  r={rd:+.3f}  {flagd}")


def fetch_window(ex, syms: list, since_ms: int, need_bars: int) -> dict:
    data = {}
    for s in syms:
        b = be.fetch_symbol(ex, s, since_ms, need_bars)
        if b:
            data[s] = b
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=60, help='total lookback')
    ap.add_argument('--split', type=int, default=30,
                     help='most recent N days = TEST; the rest = TRAIN')
    ap.add_argument('--threshold', type=float, default=70.0)
    ap.add_argument('--slip', type=float, default=0.10)
    ap.add_argument('--fee', type=float, default=0.06)
    ap.add_argument('--symbols', type=str, default='')
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--fill', default='taker', choices=['taker', 'maker'])
    args = ap.parse_args()

    if args.split >= args.days:
        sys.exit("--split must be less than --days")

    try:
        import ccxt
    except ImportError:
        sys.exit("ccxt required: pip install ccxt")

    ex = ccxt.bitget({'enableRateLimit': True,
                      'options': {'defaultType': 'swap'}})
    ex.load_markets()
    syms = ([s.strip() for s in args.symbols.split(',') if s.strip()]
            or be.DEFAULT_SYMBOLS)

    now_ms = ex.milliseconds()
    train_days = args.days - args.split
    train_since = now_ms - args.days * 86_400_000
    test_since = now_ms - args.split * 86_400_000
    need_train = train_days * 24 * 60 // be.BAR_MINUTES + 300
    need_test = args.split * 24 * 60 // be.BAR_MINUTES + 300

    print(f"[data] TRAIN: {train_days}d starting "
          f"{time.strftime('%Y-%m-%d', time.gmtime(train_since / 1000))}")
    print(f"[data] TEST:  {args.split}d starting "
          f"{time.strftime('%Y-%m-%d', time.gmtime(test_since / 1000))}")
    print(f"[data] fetching {len(syms)} symbols x 2 windows ...")

    train_data = fetch_window(ex, syms, train_since, need_train)
    test_data = fetch_window(ex, syms, test_since, need_test)
    print(f"[data] usable symbols: train={len(train_data)} "
          f"test={len(test_data)}\n")

    if not train_data or not test_data:
        sys.exit("no usable data in one of the windows")

    for profile_name, prof in be.EXIT_PROFILES.items():
        print("=" * 100)
        print(f"EXIT PROFILE = {profile_name}   threshold={args.threshold}  "
              f"fee={args.fee}%/side  slip={args.slip}%  fill={args.fill}")
        print("=" * 100)
        for name in ('random', 'bot_like', 'trend', 'meanrev', 'sweep'):
            if name not in be.SCORERS:
                continue
            print(f"  scorer={name}")
            tr_train, _ = be.run_scorer(train_data, name, args.threshold,
                                        args.slip, args.fee, args.seed,
                                        prof, args.fill)
            tr_test, _ = be.run_scorer(test_data, name, args.threshold,
                                       args.slip, args.fee, args.seed,
                                       prof, args.fill)
            split_report(tr_train, 'TRAIN')
            split_report(tr_test, 'TEST')
        print()

    print("=" * 100)
    print("HOW TO READ THIS")
    print("=" * 100)
    print("  A scorer only passes the Step-1 gate if: (a) TEST beats random on")
    print("  the same candidates, (b) TEST does not flip sign from TRAIN, and")
    print("  (c) the result holds in at least one direction without the other")
    print("  direction being NEGATIVE. n counts must reach the spec's 200+")
    print("  per track before any of this is trusted over noise.")


if __name__ == '__main__':
    main()
