#!/usr/bin/env python3
"""
backtest_exits.py — Exit-rule backtester (read-only, no wallet/exchange writes)

WHAT IT DOES
    Takes the REAL entries the bot already took (from trade_audit.json),
    downloads 1-minute candles for each, and re-runs every trade under
    alternative exit rules.

WHY THIS DESIGN
    Same entries, different exits => clean attribution. Any difference in
    the result is caused by the exit rule and nothing else.

SAFETY
    Reads trade_audit.json and public OHLCV. Never writes state, never
    touches the wallet, never places an order.

USAGE
    python3 backtest_exits.py                 # all scenarios
    python3 backtest_exits.py --calibrate     # calibration only
"""
from __future__ import annotations
import json, math, os, sys, time, argparse
from collections import defaultdict

try:
    import ccxt
except ImportError:
    sys.exit("ccxt required:  pip install ccxt")

# ── Live config (same numbers the bot runs on) ───────────────────────────────
POSITION_SIZE   = 500.0   # notional USD
MARGIN          = 50.0
SL_PCT          = 2.0
TP1_PCT         = 1.0     # closes 75% + moves SL to break-even
TP2_PCT         = 4.0
TP1_CLOSE_FRAC  = 0.75
TRAIL_ACT_PCT   = 2.0
TRAIL_PCT       = 1.5
MAX_DURATION    = 120     # minutes
FAST_LOSS_MARGIN_PCT = 3.5
FAST_LOSS_MINUTES    = 15

AUDIT_CANDIDATES = [
    'artifacts/bot-dashboard/public/trade_audit.json',
    'trade_audit.json',
    'data/trade_audit.json',
]
CACHE_DIR = '/tmp/bt_cache'

# FastLoss threshold expressed as a price move:
#   3.5% of margin ($50) = $1.75 on $500 notional = 0.35% price move
FAST_LOSS_PRICE_PCT = FAST_LOSS_MARGIN_PCT * MARGIN / POSITION_SIZE


# ── Scenarios ────────────────────────────────────────────────────────────────
# atr_mult_* : when set, targets become multiples of that trade's ATR%
#              instead of the fixed percentages.
SCENARIOS = {
    'baseline': dict(),
    'atr_targets': dict(atr_mult_tp1=1.0, atr_mult_tp2=2.5, atr_mult_sl=1.5),
    'atr_conservative': dict(atr_mult_tp1=0.8, atr_mult_tp2=2.0, atr_mult_sl=1.5),
    'fastloss_5.5': dict(fast_loss_margin_pct=5.5),
    'no_fastloss': dict(fast_loss_margin_pct=None),
    'maxdur_240': dict(max_duration=240),
    'atr+fastloss+dur': dict(atr_mult_tp1=1.0, atr_mult_tp2=2.5, atr_mult_sl=1.5,
                             fast_loss_margin_pct=5.5, max_duration=240),
}


def load_audit() -> list:
    for p in AUDIT_CANDIDATES:
        if os.path.exists(p):
            d = json.load(open(p))
            t = d['trades'] if isinstance(d, dict) else d
            print(f"[data] {len(t)} trades from {p}")
            return t
    sys.exit("trade_audit.json not found in: " + ", ".join(AUDIT_CANDIDATES))


def make_exchange():
    """Futures market — that is what the bot trades."""
    ex = ccxt.bitget({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    ex.load_markets()
    return ex


def fetch_bars(ex, symbol: str, since_ms: int, minutes: int) -> list | None:
    """1m candles from since_ms, cached on disk. Returns None if unavailable."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = f"{symbol.replace('/','_').replace(':','-')}_{since_ms}_{minutes}.json"
    path = os.path.join(CACHE_DIR, key)
    if os.path.exists(path):
        return json.load(open(path))

    out, cursor, need = [], since_ms, minutes + 5
    try:
        while len(out) < need:
            batch = ex.fetch_ohlcv(symbol, '1m', since=cursor, limit=min(1000, need - len(out)))
            if not batch:
                break
            out += batch
            cursor = batch[-1][0] + 60_000
            if len(batch) < 2:
                break
            time.sleep(ex.rateLimit / 1000)
    except Exception as e:
        print(f"  [skip] {symbol}: {type(e).__name__} {str(e)[:70]}")
        return None

    if not out:
        return None
    json.dump(out, open(path, 'w'))
    return out


def simulate(bars, entry, direction, atr_pct, cfg) -> tuple:
    """
    Replay one trade bar by bar.
    Returns (pnl_pct_of_notional, exit_reason, minutes_held).

    Conservative tie-breaking: if both the stop and the target fall inside the
    same 1m bar, the stop is assumed to hit first. This never flatters a result.
    """
    sl_p  = cfg.get('sl_pct',  SL_PCT)
    tp1_p = cfg.get('tp1_pct', TP1_PCT)
    tp2_p = cfg.get('tp2_pct', TP2_PCT)

    if cfg.get('atr_mult_sl')  and atr_pct: sl_p  = atr_pct * cfg['atr_mult_sl']
    if cfg.get('atr_mult_tp1') and atr_pct: tp1_p = atr_pct * cfg['atr_mult_tp1']
    if cfg.get('atr_mult_tp2') and atr_pct: tp2_p = atr_pct * cfg['atr_mult_tp2']

    # Guard rails so a degenerate ATR cannot create absurd targets
    sl_p  = min(max(sl_p,  0.20), 10.0)
    tp1_p = min(max(tp1_p, 0.15),  15.0)
    tp2_p = min(max(tp2_p, tp1_p), 30.0)

    max_dur = cfg.get('max_duration', MAX_DURATION)
    fl_marg = cfg.get('fast_loss_margin_pct', FAST_LOSS_MARGIN_PCT)
    fl_pct  = (fl_marg * MARGIN / POSITION_SIZE) if fl_marg else None

    long = direction.upper() == 'LONG'
    sign = 1.0 if long else -1.0

    realized = 0.0      # locked in from the TP1 partial
    remaining = 1.0
    tp1_done = False
    stop_pct = -sl_p    # stop expressed as move-in-our-favour %
    peak = 0.0

    for i, b in enumerate(bars):
        if i > max_dur:
            break
        _, o, h, l, c, _v = b
        hi_move = sign * (h - entry) / entry * 100
        lo_move = sign * (l - entry) / entry * 100
        if not long:
            hi_move, lo_move = sign * (l - entry) / entry * 100, sign * (h - entry) / entry * 100

        # 1. Stop / trailing stop — checked first, on purpose
        if lo_move <= stop_pct:
            return realized + remaining * stop_pct, ('BE/Trail' if tp1_done else 'SL'), i

        # 2. FastLoss — only inside the opening window
        if fl_pct is not None and i <= FAST_LOSS_MINUTES and not tp1_done:
            if lo_move <= -fl_pct:
                return realized + remaining * (-fl_pct), 'FastLoss', i

        # 3. Final target
        if hi_move >= tp2_p:
            return realized + remaining * tp2_p, 'TP', i

        # 4. First target — partial close, stop to break-even
        if not tp1_done and hi_move >= tp1_p:
            realized += TP1_CLOSE_FRAC * tp1_p
            remaining = 1.0 - TP1_CLOSE_FRAC
            tp1_done = True
            stop_pct = 0.0

        # 5. Trailing
        peak = max(peak, hi_move)
        if peak >= TRAIL_ACT_PCT:
            stop_pct = max(stop_pct, peak - TRAIL_PCT)

    # 6. Timeout — close at the last available price
    last = bars[min(len(bars) - 1, max_dur)]
    final = sign * (last[4] - entry) / entry * 100
    return realized + remaining * final, 'MaxDuration', min(len(bars) - 1, max_dur)


def stats(pnls: list) -> dict:
    n = len(pnls)
    if n < 2:
        return dict(n=n, total=sum(pnls), mean=0, ci_lo=0, ci_hi=0, win=0)
    m = sum(pnls) / n
    sd = (sum((x - m) ** 2 for x in pnls) / (n - 1)) ** 0.5
    se = sd / math.sqrt(n)
    return dict(n=n, total=sum(pnls), mean=m, sd=sd, se=se,
                ci_lo=m - 1.96 * se, ci_hi=m + 1.96 * se,
                win=100 * len([x for x in pnls if x > 0]) / n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--calibrate', action='store_true')
    ap.add_argument('--slip', type=float, default=0.10,
                    help='round-trip friction %% subtracted from every exit')
    ap.add_argument('--fee', type=float, default=0.06,
                    help='taker fee %% per side (default 0.06)')
    args = ap.parse_args()

    trades = load_audit()
    ex = make_exchange()

    # ── Download once, reuse for every scenario ──────────────────────────────
    print("[data] downloading 1m candles ...")
    prepared = []
    for t in trades:
        sym = t.get('symbol')
        op  = t.get('opened_at')
        ent = t.get('entry_price')
        if not (sym and op and ent):
            continue
        try:
            since = ex.parse8601(op if 'T' in op else op.replace(' ', 'T'))
        except Exception:
            continue
        if since is None:
            continue
        bars = fetch_bars(ex, sym, since, 300)
        if not bars or len(bars) < 20:
            continue
        atr_abs = t.get('atr')
        atr_pct = (float(atr_abs) if atr_abs else 0.0)
        # audit stores ATR already as a percentage in this build
        prepared.append(dict(sym=sym, entry=float(ent), dirn=t.get('direction', 'LONG'),
                             atr_pct=atr_pct, bars=bars,
                             actual=float(t.get('pnl_usd') or 0),
                             reason=t.get('close_reason')))
    print(f"[data] usable: {len(prepared)} / {len(trades)}\n")
    if not prepared:
        sys.exit("no usable trades — cannot continue")

    # ── Calibration ──────────────────────────────────────────────────────────
    sim_base, act = [], []
    reason_match = 0
    for p in prepared:
        pct, reason, _mins = simulate(p['bars'], p['entry'], p['dirn'], p['atr_pct'], {})
        sim_base.append((pct - args.slip) * POSITION_SIZE / 100)
        act.append(p['actual'])
        if reason == p['reason']:
            reason_match += 1

    print("=" * 62)
    print("CALIBRATION — simulated baseline vs what actually happened")
    print("=" * 62)
    print(f"  actual    total {sum(act):+9.2f}   mean {sum(act)/len(act):+7.3f}")
    print(f"  simulated total {sum(sim_base):+9.2f}   mean {sum(sim_base)/len(sim_base):+7.3f}")
    diffs = [a - b for a, b in zip(act, sim_base)]
    print(f"  mean abs error  {sum(abs(d) for d in diffs)/len(diffs):.3f} USD/trade")
    print(f"  exit reason matched {reason_match}/{len(prepared)} "
          f"({100*reason_match/len(prepared):.0f}%)")
    print("\n  If these are far apart the simulator does not reflect the live")
    print("  bot, and every number below is meaningless. Check this first.\n")
    if args.calibrate:
        return

    # ── Scenarios ────────────────────────────────────────────────────────────
    fee_cost = POSITION_SIZE * (args.fee / 100) * 2
    print("=" * 62)
    print(f"SCENARIOS   (fee {args.fee}%/side  =  ${fee_cost:.2f} per trade)")
    print("=" * 62)
    print(f"  {'scenario':<20}{'gross':>9}{'net':>9}{'mean':>8}{'win%':>7}  {'95% CI (net/trade)':>22}")

    results = {}
    for name, cfg in SCENARIOS.items():
        pnls, reasons = [], defaultdict(int)
        for p in prepared:
            pct, reason, _m = simulate(p['bars'], p['entry'], p['dirn'], p['atr_pct'], cfg)
            pnls.append((pct - args.slip) * POSITION_SIZE / 100)
            reasons[reason] += 1
        net = [x - fee_cost for x in pnls]
        s = stats(net)
        results[name] = (stats(pnls), s, dict(reasons))
        print(f"  {name:<20}{sum(pnls):+9.2f}{sum(net):+9.2f}{s['mean']:+8.3f}"
              f"{s['win']:6.0f}%  [{s['ci_lo']:+7.3f},{s['ci_hi']:+7.3f}]")

    # ── SHORT only, on the best scenario ─────────────────────────────────────
    print("\n" + "=" * 62)
    print("DIRECTION SPLIT")
    print("=" * 62)
    for name in ('baseline', 'atr_targets', 'atr+fastloss+dur'):
        for d in ('LONG', 'SHORT'):
            sel = [p for p in prepared if p['dirn'].upper() == d]
            if not sel:
                continue
            pn = [simulate(p['bars'], p['entry'], p['dirn'], p['atr_pct'],
                           SCENARIOS[name])[0] * POSITION_SIZE / 100 - fee_cost
                  for p in sel]
            s = stats(pn)
            print(f"  {name:<20} {d:<6} n={s['n']:<4} net {sum(pn):+8.2f}  "
                  f"mean {s['mean']:+7.3f}  CI [{s['ci_lo']:+7.3f},{s['ci_hi']:+7.3f}]")

    # ── Exit mix of the winner ───────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("EXIT MIX")
    print("=" * 62)
    for name in ('baseline', 'atr_targets', 'atr+fastloss+dur'):
        print(f"  {name:<20} {dict(results[name][2])}")

    print("\nReminder: a scenario only counts as an improvement if its 95% CI")
    print("sits entirely above zero. Anything else is noise.")


if __name__ == '__main__':
    main()
