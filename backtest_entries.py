#!/usr/bin/env python3
"""
backtest_entries.py — Entry-scoring backtester (read-only)

WHAT THIS ANSWERS
    "Does a scoring function actually separate winning setups from losing ones?"

    backtest_exits.py replayed the entries the bot really took and varied the
    exits. This does the opposite: it generates its own entries by walking
    history bar by bar, scoring every candidate, and trading the ones that pass.

THE POINT IS THE CONTROL, NOT THE SCORE
    Every strategy is run alongside a `random` scorer on the identical
    candidates. A score that does not beat random is not a score, however
    sensible its formula looks. Four months of tuning never ran this check;
    it is the whole reason this file exists.

NO LOOKAHEAD
    At decision bar i, features are computed from bars[:i+1] only. The trade
    is entered at the OPEN of bar i+1 and exits are simulated from i+1 onward.
    Any indicator that peeks at bar i+1 or later silently turns noise into a
    fake edge, so this boundary is enforced in one place: iter_decisions().

SAFETY
    Reads public OHLCV. Never touches the wallet, never places an order,
    never writes bot state.

USAGE
    python3 backtest_entries.py --selftest          # synthetic data, no network
    python3 backtest_entries.py --days 30
    python3 backtest_entries.py --days 30 --symbols BTC/USDT,ETH/USDT
"""
from __future__ import annotations
import argparse, json, math, os, random, sys, time
from collections import defaultdict

# ── Trade mechanics — same numbers the live bot runs on ──────────────────────
POSITION_SIZE = 500.0
MARGIN        = 50.0
SL_PCT        = 2.0
TP1_PCT       = 1.0
TP2_PCT       = 4.0
TP1_CLOSE_FRAC = 0.75
TRAIL_ACT_PCT  = 2.0
TRAIL_PCT      = 1.5
MAX_DURATION_MIN     = 120
FAST_LOSS_MARGIN_PCT = 3.5
FAST_LOSS_MINUTES    = 15

# Exit templates. A pattern trade and a swing trade want different exits;
# testing a sweep under swing exits and calling the null result "no edge" would
# confound the entry hypothesis with an exit mismatch.
EXIT_PROFILES = {
    'swing': dict(sl=2.0, tp1=1.0, tp2=4.0, trail_act=2.0, trail=1.5,
                  max_min=120, fl_margin=3.5),
    'scalp': dict(sl=0.6, tp1=0.3, tp2=0.8, trail_act=0.5, trail=0.3,
                  max_min=30, fl_margin=None),
}

BAR_MINUTES   = 5           # decision + simulation granularity
DECIDE_EVERY  = 3           # score a symbol every 3 bars = 15 minutes
COOLDOWN_BARS = 24          # no re-entry on the same symbol for 2h
CACHE_DIR     = '/tmp/bt_entries_cache'

DEFAULT_SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'BNB/USDT',
    'ADA/USDT', 'AVAX/USDT', 'DOGE/USDT', 'LINK/USDT', 'DOT/USDT',
    'ATOM/USDT', 'NEAR/USDT', 'APT/USDT', 'SUI/USDT', 'ARB/USDT',
    'OP/USDT', 'INJ/USDT', 'FET/USDT', 'RENDER/USDT', 'ONDO/USDT',
]


# ── Indicators (plain python, no pandas — keeps this file standalone) ────────
def ema_series(vals: list, n: int) -> list:
    if not vals:
        return []
    k = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi_at(closes: list, n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(len(closes) - n, len(closes)):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1.0 + rs)


def atr_pct_at(bars: list, n: int = 14) -> float:
    if len(bars) < n + 1:
        return 0.0
    trs = []
    for i in range(len(bars) - n, len(bars)):
        h, l, pc = bars[i][2], bars[i][3], bars[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    close = bars[-1][4]
    return (sum(trs) / n) / close * 100 if close else 0.0


def features_at(bars: list, i: int) -> dict | None:
    """
    Features from bars[:i+1] ONLY. Returns None when there is not enough
    history. This function is the lookahead boundary — nothing here may read
    bars[i+1] or beyond.
    """
    if i < 210:
        return None
    window = bars[:i + 1]
    closes = [b[4] for b in window]
    vols   = [b[5] for b in window]

    e9  = ema_series(closes[-120:], 9)[-1]
    e21 = ema_series(closes[-120:], 21)[-1]
    e50 = ema_series(closes[-200:], 50)[-1]
    e200 = ema_series(closes[-210:], 200)[-1]
    macd = ema_series(closes[-120:], 12)[-1] - ema_series(closes[-120:], 26)[-1]
    price = closes[-1]
    vol_mean = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0.0

    return dict(
        price=price, ema9=e9, ema21=e21, ema50=e50, ema200=e200,
        macd=macd, rsi=rsi_at(closes), atr_pct=atr_pct_at(window),
        vol_ratio=(vols[-1] / vol_mean) if vol_mean else 0.0,
        chg_24h=(price / closes[-288] - 1) * 100 if len(closes) > 288 else 0.0,
        chg_1h=(price / closes[-12] - 1) * 100 if len(closes) > 12 else 0.0,
    )


# ── Scorers ──────────────────────────────────────────────────────────────────
# Each returns 0-100. Higher = more attractive. The runner trades the ones
# at or above --threshold.

def score_random(f: dict, direction: str) -> float:
    """CONTROL. Any real scorer must beat this or it carries no information."""
    return random.uniform(0, 100)


def score_bot_like(f: dict, direction: str) -> float:
    """Approximates the live engine: EMA 35 / MACD 25 / RSI 20 / vol 15."""
    s = 0.0
    if direction == 'LONG':
        if f['ema9'] > f['ema21'] > f['ema50']: s += 35
        elif f['ema9'] > f['ema21']:            s += 18
        if f['macd'] > 0:                       s += 25
        if 45 <= f['rsi'] <= 70:                s += 20
        elif f['rsi'] < 45:                     s += 8
    else:
        if f['ema9'] < f['ema21'] < f['ema50']: s += 35
        elif f['ema9'] < f['ema21']:            s += 18
        if f['macd'] < 0:                       s += 25
        if 30 <= f['rsi'] <= 55:                s += 20
        elif f['rsi'] > 55:                     s += 8
    if f['vol_ratio'] > 1.5:  s += 15
    elif f['vol_ratio'] > 1.0: s += 8
    return min(s, 100.0)


def score_trend(f: dict, direction: str) -> float:
    """Pure trend following: side with the 200 EMA, demand real momentum."""
    up = f['price'] > f['ema200']
    if (direction == 'LONG') != up:
        return 0.0
    s = 40.0
    s += min(abs(f['chg_1h']) * 8, 30)
    if f['vol_ratio'] > 1.2:
        s += 15
    if f['atr_pct'] > 0.5:
        s += 15
    return min(s, 100.0)


def score_meanrev(f: dict, direction: str) -> float:
    """Fade stretched RSI — the opposite bet to the live bot."""
    if direction == 'LONG':
        if f['rsi'] > 35: return 0.0
        return min(40 + (35 - f['rsi']) * 3, 100)
    else:
        if f['rsi'] < 65: return 0.0
        return min(40 + (f['rsi'] - 65) * 3, 100)


SCORERS = {
    'random':   score_random,
    'bot_like': score_bot_like,
    'trend':    score_trend,
    'meanrev':  score_meanrev,
}

try:
    from pattern_scorer import score_sweep, PatternScorer
    SCORERS['sweep'] = score_sweep
except ImportError:
    PatternScorer = None


LIMIT_OFFSET_PCT = 0.05
LIMIT_WAIT_BARS  = 6


def try_limit_fill(bars: list, start: int, direction: str) -> tuple:
    """
    Rest a limit OFFSET better than the reference price; see if the next
    LIMIT_WAIT_BARS bars reach it. Returns (index, price) or (None, None).

    Unfilled orders are the ones price ran away from - usually the winners.
    That adverse selection is exactly why this must be simulated, not assumed.
    """
    ref = bars[start][1]
    if ref <= 0:
        return None, None
    limit = ref * (1 - LIMIT_OFFSET_PCT / 100) if direction == 'LONG' \
        else ref * (1 + LIMIT_OFFSET_PCT / 100)
    for k in range(LIMIT_WAIT_BARS):
        j = start + k
        if j >= len(bars):
            return None, None
        _, _o, h, l, _c, _v = bars[j]
        if direction == 'LONG' and l <= limit:
            return j, limit
        if direction == 'SHORT' and h >= limit:
            return j, limit
    return None, None


# ── Exit simulation — same rules as backtest_exits.py, on BAR_MINUTES bars ───
def simulate_exit(bars: list, start: int, entry: float, direction: str,
                  slip_pct: float, prof: dict) -> tuple:
    max_bars = prof['max_min'] // BAR_MINUTES
    fl_bars  = FAST_LOSS_MINUTES // BAR_MINUTES
    fl_pct   = (prof['fl_margin'] * MARGIN / POSITION_SIZE
                if prof['fl_margin'] else None)
    long = direction == 'LONG'
    sign = 1.0 if long else -1.0

    realized, remaining = 0.0, 1.0
    tp1_done, stop_pct, peak = False, -prof['sl'], 0.0

    for k in range(1, max_bars + 1):
        j = start + k
        if j >= len(bars):
            break
        _, o, h, l, c, _v = bars[j]
        up = sign * (h - entry) / entry * 100
        dn = sign * (l - entry) / entry * 100
        if not long:
            up, dn = sign * (l - entry) / entry * 100, sign * (h - entry) / entry * 100

        # Stop first — never flatter the result
        if dn <= stop_pct:
            return realized + remaining * stop_pct - slip_pct, ('BE/Trail' if tp1_done else 'SL')
        if fl_pct is not None and k <= fl_bars and not tp1_done and dn <= -fl_pct:
            return realized + remaining * (-fl_pct) - slip_pct, 'FastLoss'
        if up >= prof['tp2']:
            return realized + remaining * prof['tp2'] - slip_pct, 'TP'
        if not tp1_done and up >= prof['tp1']:
            realized += TP1_CLOSE_FRAC * prof['tp1']
            remaining = 1.0 - TP1_CLOSE_FRAC
            tp1_done, stop_pct = True, 0.0
        peak = max(peak, up)
        if peak >= prof['trail_act']:
            stop_pct = max(stop_pct, peak - prof['trail'])

    j = min(start + max_bars, len(bars) - 1)
    final = sign * (bars[j][4] - entry) / entry * 100
    return realized + remaining * final - slip_pct, 'MaxDuration'


# ── Walk-forward ─────────────────────────────────────────────────────────────
def iter_decisions(bars: list):
    """Yields (i, features) for each decision bar. The only lookahead gate."""
    for i in range(210, len(bars) - 2, DECIDE_EVERY):
        f = features_at(bars, i)
        if f:
            # Structure-based scorers need the bars themselves; a flat feature
            # vector cannot express "this bar pierced the prior low".
            f['_bars'], f['_i'] = bars, i
            yield i, f


def run_scorer(data: dict, name: str, threshold: float, slip_pct: float,
               fee_pct: float, seed: int, prof: dict,
               fill: str = 'taker') -> tuple:
    """Returns (trades, signals_seen) - unfilled setups must stay visible."""
    random.seed(seed)
    fn = SCORERS[name]
    fee_cost_pct = fee_pct * 2
    trades = []
    signals = 0
    for sym, bars in data.items():
        last_exit = -10**9
        for i, f in iter_decisions(bars):
            if i < last_exit + COOLDOWN_BARS:
                continue
            for direction in ('LONG', 'SHORT'):
                s = fn(f, direction)
                if s < threshold:
                    continue
                signals += 1
                if fill == 'taker':
                    start = i + 1
                    entry = bars[start][1]
                    entry_slip = slip_pct
                else:
                    start, entry = try_limit_fill(bars, i + 1, direction)
                    if start is None:
                        continue
                    entry_slip = slip_pct / 2
                if not entry or entry <= 0:
                    continue
                pct, reason = simulate_exit(bars, start, entry, direction,
                                            entry_slip, prof)
                trades.append(dict(
                    symbol=sym, direction=direction, score=s, reason=reason,
                    gross=pct * POSITION_SIZE / 100,
                    net=(pct - fee_cost_pct) * POSITION_SIZE / 100,
                    ts=bars[i + 1][0],
                ))
                last_exit = i + prof['max_min'] // BAR_MINUTES
                break
    return trades, signals


# ── Stats ────────────────────────────────────────────────────────────────────
def summarize(vals: list) -> dict:
    n = len(vals)
    if n < 2:
        return dict(n=n, total=sum(vals), mean=0.0, ci_lo=0.0, ci_hi=0.0, win=0.0)
    m = sum(vals) / n
    sd = (sum((x - m) ** 2 for x in vals) / (n - 1)) ** 0.5
    se = sd / math.sqrt(n)
    return dict(n=n, total=sum(vals), mean=m, ci_lo=m - 1.96 * se,
                ci_hi=m + 1.96 * se, win=100 * len([x for x in vals if x > 0]) / n)


def pearson(a: list, b: list) -> tuple:
    n = len(a)
    if n < 3:
        return 0.0, 0.0
    ma, mb = sum(a) / n, sum(b) / n
    ca = [x - ma for x in a]
    cb = [x - mb for x in b]
    sa = math.sqrt(sum(x * x for x in ca))
    sb = math.sqrt(sum(x * x for x in cb))
    if sa == 0 or sb == 0:
        return 0.0, 0.0
    r = max(min(sum(x * y for x, y in zip(ca, cb)) / (sa * sb), 0.9999), -0.9999)
    return r, r * math.sqrt((n - 2) / (1 - r * r))


def report(name: str, trades: list, signals: int = 0):
    if not trades:
        print(f"  {name:<10} no trades ({signals} signals, 0 filled)")
        return
    net = [t['net'] for t in trades]
    s = summarize(net)
    r, t_ = pearson([t['score'] for t in trades], net)
    flag = 'EDGE' if s['ci_lo'] > 0 else ('NEGATIVE' if s['ci_hi'] < 0 else 'noise')
    fr = (100.0 * len(trades) / signals) if signals else 100.0
    print(f"  {name:<10}{s['n']:>6}{fr:>6.0f}%{s['total']:>10.2f}"
          f"{s['mean']:>9.3f}{s['win']:>6.0f}%  "
          f"[{s['ci_lo']:+7.3f},{s['ci_hi']:+7.3f}]  r={r:+.3f}  {flag}")


# ── Data ─────────────────────────────────────────────────────────────────────
def fetch_symbol(ex, symbol: str, since_ms: int, need: int) -> list | None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR,
                        f"{symbol.replace('/','_')}_{since_ms}_{need}.json")
    if os.path.exists(path):
        return json.load(open(path))
    out, cursor = [], since_ms
    try:
        while len(out) < need:
            tf = f"{BAR_MINUTES}m"
            batch = ex.fetch_ohlcv(symbol, tf, since=cursor, limit=1000)
            if not batch:
                break
            out += batch
            cursor = batch[-1][0] + BAR_MINUTES * 60_000
            if len(batch) < 2:
                break
            time.sleep(ex.rateLimit / 1000)
    except Exception as e:
        print(f"  [skip] {symbol}: {type(e).__name__} {str(e)[:60]}")
        return None
    if len(out) < 400:
        return None
    json.dump(out, open(path, 'w'))
    return out


def synthetic(symbol: str, n: int, seed: int) -> list:
    """Random walk with a mild session drift — for --selftest only."""
    rnd = random.Random(seed)
    price, bars, t = 100.0, [], 1_700_000_000_000
    for i in range(n):
        drift = 0.0004 * math.sin(i / 300.0)
        price *= (1 + rnd.gauss(drift, 0.0025))
        o = price
        h = o * (1 + abs(rnd.gauss(0, 0.0018)))
        l = o * (1 - abs(rnd.gauss(0, 0.0018)))
        c = rnd.uniform(l, h)
        bars.append([t + i * BAR_MINUTES * 60_000, o, h, l, c,
                     abs(rnd.gauss(1000, 300))])
        price = c
    return bars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--threshold', type=float, default=70.0)
    ap.add_argument('--slip', type=float, default=0.10,
                    help='round-trip friction %% (0.10 calibrated in backtest_exits)')
    ap.add_argument('--fee', type=float, default=0.06, help='taker %% per side')
    ap.add_argument('--symbols', type=str, default='')
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--exit-profile', dest='exit_profile', default='swing',
                    choices=list(EXIT_PROFILES.keys()),
                    help='swing = the live bot rules; scalp = small fast target')
    ap.add_argument('--fill', default='taker', choices=['taker', 'maker'])
    ap.add_argument('--param-sweep', action='store_true',
                    help='vary the sweep lookback to check the result is a '
                         'plateau and not one lucky setting')
    args = ap.parse_args()
    prof = EXIT_PROFILES[args.exit_profile]

    if args.selftest:
        print("SELFTEST — synthetic random-walk data, no network.")
        print("A random walk has no edge, so every scorer should read 'noise'.")
        print("Anything showing EDGE here means lookahead has crept in.\n")
        data = {f"SYN{i}/USDT": synthetic(f"SYN{i}", 3000, 100 + i)
                for i in range(6)}
    else:
        try:
            import ccxt
        except ImportError:
            sys.exit("ccxt required:  pip install ccxt")
        ex = ccxt.bitget({'enableRateLimit': True,
                          'options': {'defaultType': 'swap'}})
        ex.load_markets()
        syms = ([s.strip() for s in args.symbols.split(',') if s.strip()]
                or DEFAULT_SYMBOLS)
        need = args.days * 24 * 60 // BAR_MINUTES
        since = ex.milliseconds() - args.days * 86_400_000
        print(f"[data] {len(syms)} symbols x {need} bars of {BAR_MINUTES}m ...")
        data = {}
        for s in syms:
            b = fetch_symbol(ex, s, since, need)
            if b:
                data[s] = b
        print(f"[data] usable: {len(data)}/{len(syms)} symbols\n")
        if not data:
            sys.exit("no data")

    print("=" * 96)
    print(f"ENTRY SCORERS   threshold={args.threshold}  "
          f"fee={args.fee}%/side  slip={args.slip}%  "
          f"exits={args.exit_profile} "
          f"(SL {prof['sl']}% TP {prof['tp2']}% {prof['max_min']}min)  "
          f"fill={args.fill}")
    print("=" * 96)
    print(f"  {'scorer':<10}{'n':>6}{'fill':>6}{'net':>10}{'mean':>9}"
          f"{'win':>7}  {'95% CI (net/trade)':>20}  corr")

    results = {}
    for name in ('random', 'bot_like', 'trend', 'meanrev', 'sweep'):
        if name not in SCORERS:
            continue
        tr, sig = run_scorer(data, name, args.threshold, args.slip, args.fee,
                             args.seed, prof, args.fill)
        results[name] = tr
        report(name, tr, sig)

    if args.param_sweep and PatternScorer is not None:
        import pattern_scorer as ps
        print("\n" + "=" * 96)
        print("PARAM SWEEP — a real effect is a plateau, a lucky fit is a spike")
        print("=" * 96)
        original = ps.DEFAULT
        for lb in (5, 8, 10, 14, 20):
            ps.DEFAULT = PatternScorer(lookback=lb)
            tr, sig = run_scorer(data, 'sweep', args.threshold, args.slip,
                                 args.fee, args.seed, prof, args.fill)
            report(f"lookback={lb}", tr, sig)
        ps.DEFAULT = original

    print("\n" + "=" * 96)
    print("READ THIS BEFORE BELIEVING ANY LINE ABOVE")
    print("=" * 96)
    if args.fill == 'maker':
        print("  0. FILL RATE IS THE NUMBER TO WATCH. Unfilled orders are the")
        print("     ones price ran away from - usually the winners.")
    print("  1. A scorer only matters if its CI sits entirely above zero AND")
    print("     it clearly beats the random control on the same candidates.")
    print("  2. r is the correlation between score and outcome. Near zero means")
    print("     the number is decoration even when the total happens to be positive.")
    print("  3. One period is one sample. Re-run over a different --days window")
    print("     before trusting anything that looks good here.")


if __name__ == '__main__':
    main()
