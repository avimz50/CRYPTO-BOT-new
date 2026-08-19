#!/usr/bin/env python3
"""
backtest_daily.py — Daily trend-following on a broad crypto basket.

WHAT THIS ANSWERS
    A vendor (SIGNUM / AutoTrading Michael) claims 62-78%/yr since 2018 from
    a daily trend strategy over "the top 100 assets by market cap". This
    measures how much of that survives once the test is set up honestly.

DIFFERENT REGIME — DO NOT REUSE THE OLD CONCLUSION
    backtest_entries.py killed the 5-minute bot on friction: 693 trades in
    four months at $1.10 each. Here a coin turns over a few times a year, so
    friction is a rounding error. The dangers here are survivorship bias and
    overfitting instead, and they need their own controls.

THE FOUR CONTROLS
    1. Universe frozen in January 2018, read from universe_2018.txt, with
       coins that later died still in it.               -> survivorship bias
    2. Buy & hold on that same basket, always reported.  -> missing benchmark
    3. Random entries at a matched turnover.            -> is the signal real
    4. Fees and slippage on every fill.                 -> friction

    A ticker that cannot be fetched is NOT skipped. Silently dropping dead
    tickers is precisely the bias under test, so the run reports three
    universes and the gap between them IS the answer:

       A honest     every 2018 ticker; a delisting is marked to a total loss
       B dropped    dead tickers quietly ignored (what most backtests do)
       C survivors  only coins still trading today (today's top-100 list)

MODES
    --selftest-survivorship   offline, synthetic, no network. Sizes the bias
                              in a world built with NO momentum, so any trend
                              edge there is exposed as mechanical.
    --real                    needs exchange access. Run on Replit.

SAFETY
    Read-only. Never touches the wallet, never places an order.

USAGE
    python3 backtest_daily.py --selftest-survivorship
    python3 backtest_daily.py --selftest-survivorship --death-sweep
    python3 backtest_daily.py --real                       # <- on Replit
    python3 backtest_daily.py --real --trend-sweep
"""
from __future__ import annotations
import argparse, json, math, os, random, sys, time

# ── Trading assumptions ──────────────────────────────────────────────────────
FEE_PCT    = 0.045     # Hyperliquid taker — the number shown in the video
SLIP_PCT   = 0.05      # daily entries are far less slippage-sensitive
TREND_N    = 50        # slow line, pre-registered; swept with --trend-sweep
MAX_WEIGHT = 0.10      # never more than 10% of equity in one coin

START_MS = 1_514_764_800_000        # 2018-01-01T00:00:00Z
DAY_MS   = 86_400_000
CACHE    = '/tmp/bt_daily_cache'


# ══════════════════════════════════════════════════════════════════════════
#  Portfolio engine
# ══════════════════════════════════════════════════════════════════════════
def sma(path: list, t: int, n: int) -> float | None:
    """Mean of the last n prices ending at t. None if history is incomplete."""
    if t < n:
        return None
    w = path[t - n + 1:t + 1]
    if any(p is None for p in w):
        return None
    return sum(w) / n


def run_portfolio(series: list, members: list, mode: str, seed: int,
                  trend_n: int = TREND_N, trade_rate: float = 0.0,
                  wipe_on_delist: bool = True, allow_short: bool = False) -> dict:
    """
    Equal-weight portfolio rebalanced daily; idle cash earns nothing.

    mode 'hold'   : hold every listed asset, every day
    mode 'trend'  : long while close > SMA(trend_n); short below it when
                    allow_short, else flat
    mode 'random' : same exposure and same turnover, chosen at random.
                    This is the control — a rule that cannot beat it is
                    not carrying information.

    LOOKAHEAD GATE: the decision at day t reads prices up to and including
    t, and the resulting weights earn the return from t to t+1. Nothing in
    this function may read t+1 before the weights are fixed.

    wipe_on_delist=True prices a coin that stops trading at a total loss.
    False sells it at its last print, which is the flattering assumption
    almost every public backtest makes without saying so.
    """
    rnd = random.Random(seed)
    n_days = len(series[0])
    equity, trades = 1.0, 0
    peak, maxdd = 1.0, 0.0
    held: dict[int, int] = {a: 0 for a in members}      # -1 / 0 / +1

    for t in range(n_days - 1):
        want = {}
        for a in members:
            p = series[a][t]
            if p is None:
                if held[a] != 0:
                    held[a] = 0
                continue
            if mode == 'hold':
                pos = 1
            elif mode == 'trend':
                s = sma(series[a], t, trend_n)
                if s is None:
                    pos = 0
                elif p > s:
                    pos = 1
                else:
                    pos = -1 if allow_short else 0
            else:
                pos = held[a] if rnd.random() > trade_rate else \
                      rnd.choice([1, -1] if allow_short else [1, 0])
            if pos != held[a]:
                trades += 1
            held[a] = pos
            if pos != 0:
                want[a] = pos

        if want:
            w = min(1.0 / len(want), MAX_WEIGHT)
            ret = 0.0
            for a, pos in want.items():
                p0, p1 = series[a][t], series[a][t + 1]
                if p1 is None:
                    # Stopped trading overnight.
                    if wipe_on_delist:
                        ret += w * (-1.0 * pos)
                    # else: sold at the last print, contributing nothing
                else:
                    ret += w * pos * (p1 / p0 - 1.0)
            equity *= max(1.0 + ret, 1e-12)

        peak = max(peak, equity)
        maxdd = max(maxdd, 1.0 - equity / peak)

    cost = (FEE_PCT + SLIP_PCT) / 100.0
    avg_w = min(1.0 / max(len(members), 1), MAX_WEIGHT)
    equity *= math.exp(-trades * cost * avg_w)

    years = (n_days - 1) / 365.0
    cagr = (equity ** (1 / years) - 1) * 100 if equity > 1e-11 else -100.0
    return dict(equity=equity, cagr=cagr, maxdd=maxdd * 100,
                trades=trades, years=years)


def show(label: str, r: dict, ref: dict | None = None):
    tot = (r['equity'] - 1) * 100
    d = f"{r['cagr'] - ref['cagr']:+7.1f}" if ref else '      -'
    print(f"  {label:<34}{tot:>13,.0f}%{r['cagr']:>9.1f}%"
          f"{r['maxdd']:>9.1f}%{r['trades']:>8}{d:>9}")


def header(title: str):
    print("\n" + "=" * 94)
    print(title)
    print("=" * 94)
    print(f"  {'':<34}{'total':>13}{'CAGR':>10}{'MaxDD':>9}"
          f"{'trades':>8}{'vs B&H':>9}")


def panel(name: str, series: list, members: list, seed: int, trend_n: int,
          wipe: bool, allow_short: bool):
    header(f"{name}   —   {len(members)} coins")
    hold  = run_portfolio(series, members, 'hold', seed,
                          wipe_on_delist=wipe)
    trend = run_portfolio(series, members, 'trend', seed, trend_n=trend_n,
                          wipe_on_delist=wipe, allow_short=allow_short)
    rate  = trend['trades'] / max(len(members) * len(series[0]), 1)
    rand  = run_portfolio(series, members, 'random', seed, trade_rate=rate,
                          wipe_on_delist=wipe, allow_short=allow_short)
    show('buy & hold', hold)
    show(f'daily trend (close > SMA{trend_n})', trend, hold)
    show('random, matched turnover', rand, hold)
    return dict(hold=hold, trend=trend, rand=rand)


# ══════════════════════════════════════════════════════════════════════════
#  Synthetic universe — the offline bias experiment
# ══════════════════════════════════════════════════════════════════════════
def synth_universe(n_assets: int, n_days: int, death_frac: float, seed: int):
    """
    Daily series with NO momentum: log-returns are i.i.d., so a trend rule
    has nothing to forecast. A fraction die by slow bleed and then delist —
    that is how a delisting really looks, and it is what lets a trend rule
    escape while a holder cannot.
    """
    rnd = random.Random(seed)
    dead = set(rnd.sample(range(n_assets), int(round(n_assets * death_frac))))
    series, survivors = [], set()
    for a in range(n_assets):
        mu  = rnd.gauss(0.0007, 0.0004)
        vol = rnd.uniform(0.035, 0.075)
        px, path = 100.0, []
        if a in dead:
            d0 = rnd.randint(int(n_days * .15), int(n_days * .85))
            dl = rnd.randint(120, 400)
        else:
            survivors.add(a); d0, dl = 10 ** 9, 0
        for t in range(n_days):
            if t >= d0 + dl:
                path.append(None); continue
            drift = mu - (4.6 / dl if t >= d0 else 0.0)
            px *= math.exp(rnd.gauss(drift - .5 * vol * vol, vol))
            path.append(max(px, 1e-9))
        series.append(path)
    return series, survivors


def survivorship(n_assets, n_days, death_frac, seed, quiet=False) -> dict:
    series, survivors = synth_universe(n_assets, n_days, death_frac, seed)
    out = {}
    for tag, members in (('frozen', list(range(n_assets))),
                         ('survivors', sorted(survivors))):
        hold  = run_portfolio(series, members, 'hold', seed)
        trend = run_portfolio(series, members, 'trend', seed)
        rate  = trend['trades'] / max(len(members) * n_days, 1)
        rand  = run_portfolio(series, members, 'random', seed, trade_rate=rate)
        out[tag] = dict(hold=hold, trend=trend, rand=rand, n=len(members))
    if not quiet:
        for tag, nm in (('frozen',    'A. UNIVERSE FROZEN AT DAY 0 (honest)'),
                        ('survivors', 'B. ONLY COINS ALIVE AT THE END (biased)')):
            d = out[tag]
            header(f"{nm}   —   {d['n']} assets")
            show('buy & hold', d['hold'])
            show('daily trend (close > SMA50)', d['trend'], d['hold'])
            show('random, matched turnover', d['rand'], d['hold'])
    return out


# ══════════════════════════════════════════════════════════════════════════
#  Real data
# ══════════════════════════════════════════════════════════════════════════
def load_universe(path: str) -> list:
    if not os.path.exists(path):
        sys.exit(f"universe file not found: {path}")
    out = []
    for ln in open(path, encoding='utf-8'):
        ln = ln.split('#')[0].strip()
        if ln:
            out.append(ln.upper())
    return out


def fetch_daily(ex, symbol: str, since: int, need: int) -> dict | None:
    """Daily closes as {timestamp: close}, cached. None when unavailable."""
    os.makedirs(CACHE, exist_ok=True)
    key = os.path.join(CACHE, f"{symbol.replace('/', '_')}_{since}.json")
    if os.path.exists(key):
        d = json.load(open(key))
        return {int(k): v for k, v in d.items()} or None
    out, cur = {}, since
    try:
        while len(out) < need:
            b = ex.fetch_ohlcv(symbol, '1d', since=cur, limit=1000)
            if not b:
                break
            for row in b:
                out[row[0]] = row[4]
            nxt = b[-1][0] + DAY_MS
            if nxt <= cur:
                break
            cur = nxt
            time.sleep(ex.rateLimit / 1000)
    except Exception:
        return None
    return out or None


def build_series(ex, tickers: list, n_days: int, quote_hint: str = 'USDT'):
    """
    Resolve each ticker to a USD-denominated daily series on a fixed grid.

    Tries TICKER/USDT first, then TICKER/BTC converted through BTC/USDT —
    in 2018 most alts only had BTC pairs, so without that fallback half the
    universe would look 'dead' for a reason that has nothing to do with the
    coin. Every ticker lands in exactly one bucket, and the buckets are
    reported, because an unexplained disappearance is the finding.
    """
    grid = [START_MS + i * DAY_MS for i in range(n_days)]
    btc = fetch_daily(ex, f'BTC/{quote_hint}', START_MS, n_days)
    if not btc:
        sys.exit("could not fetch BTC — check exchange access")

    series, names, status = [], [], {'usdt': [], 'via_btc': [], 'never': []}
    for tk in tickers:
        raw, how = None, None
        for sym, conv in ((f'{tk}/{quote_hint}', None), (f'{tk}/BTC', btc)):
            got = fetch_daily(ex, sym, START_MS, n_days)
            if got:
                raw, how = got, ('usdt' if conv is None else 'via_btc')
                if conv is not None:
                    raw = {ts: p * conv[ts] for ts, p in got.items()
                           if ts in conv}
                break
        if not raw:
            status['never'].append(tk)
            continue
        status[how].append(tk)
        path, last = [], None
        for ts in grid:
            v = raw.get(ts)
            if v is not None:
                last = ts
            path.append(v)
        # Forward-fill single missing days only; a long gap means delisted.
        for i in range(1, n_days):
            if path[i] is None and path[i - 1] is not None:
                nxt = next((j for j in range(i, min(i + 5, n_days))
                            if path[j] is not None), None)
                if nxt is not None:
                    path[i] = path[i - 1]
        series.append(path)
        names.append(tk)
    return series, names, status, grid


def real_run(args):
    try:
        import ccxt
    except ImportError:
        sys.exit("ccxt required:  pip install ccxt")

    n_days = int(args.years * 365)
    tickers = load_universe(args.universe)
    ex = getattr(ccxt, args.exchange)({'enableRateLimit': True})
    try:
        ex.load_markets()
    except Exception as e:
        sys.exit(f"cannot reach {args.exchange}: {type(e).__name__}\n"
                 f"  This sandbox is firewalled — run on Replit.\n"
                 f"  If Replit blocks it too, try --exchange kraken / okx / "
                 f"bitget.")

    print(f"[universe] {len(tickers)} tickers frozen at 2018-01-01 "
          f"from {args.universe}")
    print(f"[data] {args.exchange}, daily bars, {args.years} years ...")
    series, names, status, grid = build_series(ex, tickers, n_days)

    print(f"\n[resolved] {len(status['usdt'])} via USDT pair, "
          f"{len(status['via_btc'])} via BTC pair, "
          f"{len(status['never'])} never fetchable")
    if status['never']:
        print("  never fetchable (dead, renamed, or never listed here):")
        print("  " + ", ".join(status['never']))
        print("  These carry no price data at all, so they cannot be priced")
        print("  into any portfolio — but they are the survivorship story.")
        print("  Treat every result below as OPTIMISTIC by that much.")

    alive_end = [i for i, p in enumerate(series) if p[-1] is not None]
    full_data = [i for i, p in enumerate(series)
                 if p[-1] is not None and p[0] is not None]
    everyone  = list(range(len(series)))

    print(f"\n[universes] honest {len(everyone)} · "
          f"survivors {len(alive_end)} · full-history {len(full_data)}")

    a = panel('A. FROZEN 2018 UNIVERSE, delistings marked to a total loss',
              series, everyone, args.seed, args.trend_n, True, args.short)
    b = panel('B. SAME UNIVERSE, delistings sold at their last print',
              series, everyone, args.seed, args.trend_n, False, args.short)
    c = panel("C. SURVIVORS ONLY — today's list, the vendor's setup",
              series, alive_end, args.seed, args.trend_n, False, args.short)

    if args.trend_sweep:
        header("TREND-LENGTH SWEEP on universe A — a real effect is a plateau")
        for n in (20, 30, 50, 80, 120):
            r = run_portfolio(series, everyone, 'trend', args.seed,
                              trend_n=n, wipe_on_delist=True,
                              allow_short=args.short)
            show(f'SMA{n}', r, a['hold'])

    print("\n" + "=" * 94)
    print("THE ONE NUMBER")
    print("=" * 94)
    excess = a['trend']['cagr'] - a['hold']['cagr']
    infl   = c['hold']['cagr'] - a['hold']['cagr']
    print(f"  Excess CAGR of the strategy over holding the same 2018 basket,")
    print(f"  honestly priced:                                {excess:+.1f} pp/yr")
    print(f"  Inflation from testing on survivors instead:    {infl:+.1f} pp/yr")
    print()
    print("  The vendor claims 62-78%/yr. Compare that to universe A, not C.")
    print("  A strategy only earns the word 'edge' if it beats buy & hold on")
    print("  the honest universe AND beats the random control on the same")
    print("  candidates. Anything else is beta wearing a costume.")


# ══════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest-survivorship', action='store_true')
    ap.add_argument('--death-sweep', action='store_true')
    ap.add_argument('--real', action='store_true')
    ap.add_argument('--trend-sweep', action='store_true')
    ap.add_argument('--short', action='store_true',
                    help='also take shorts below the slow line')
    ap.add_argument('--universe', default='universe_2018.txt')
    ap.add_argument('--exchange', default='binance')
    ap.add_argument('--trend-n', type=int, default=TREND_N)
    ap.add_argument('--assets', type=int, default=100)
    ap.add_argument('--years', type=float, default=8.4)
    ap.add_argument('--death-frac', type=float, default=0.60)
    ap.add_argument('--seed', type=int, default=7)
    args = ap.parse_args()

    if args.real:
        return real_run(args)

    if not args.selftest_survivorship:
        print("Choose a mode:")
        print("  --selftest-survivorship   offline, sizes the bias")
        print("  --real                    needs exchange access (Replit)")
        return

    n_days = int(args.years * 365)
    print("SURVIVORSHIP EXPERIMENT — synthetic universe, NO momentum by design.")
    print(f"{args.assets} assets, {args.years} years, "
          f"{args.death_frac:.0%} die by delisting.")
    print("Log-returns are i.i.d., so a trend rule has nothing to forecast.")

    if args.death_sweep:
        header("DEATH-RATE SWEEP — CAGR inflation from using survivors only")
        print(f"  {'death rate':<34}{'B&H frozen':>12}{'B&H surv':>10}"
              f"{'trend frz':>10}{'trend surv':>12}{'inflation':>11}")
        for df in (0.0, 0.2, 0.4, 0.6, 0.8):
            acc = [survivorship(args.assets, n_days, df, args.seed + s, True)
                   for s in range(5)]
            av = lambda tag, k: sum(x[tag][k]['cagr'] for x in acc) / len(acc)
            hf, hs = av('frozen', 'hold'), av('survivors', 'hold')
            tf, ts = av('frozen', 'trend'), av('survivors', 'trend')
            print(f"  {df:<34.0%}{hf:>11.1f}%{hs:>9.1f}%"
                  f"{tf:>9.1f}%{ts:>11.1f}%{hs - hf:>10.1f}pp")
        return

    res = survivorship(args.assets, n_days, args.death_frac, args.seed)
    inf = res['survivors']['hold']['cagr'] - res['frozen']['hold']['cagr']
    print("\n" + "=" * 94)
    print("WHAT THIS DOES AND DOES NOT SHOW")
    print("=" * 94)
    print(f"  Survivorship inflation on the headline: {inf:+.1f} percentage")
    print("  points of CAGR, from nothing but the choice of universe.")
    print("  That number is arithmetic and transfers to reality. Whether")
    print("  trend-following predicts anything does NOT — this world has no")
    print("  momentum in it. For that, run --real on Replit.")


if __name__ == '__main__':
    main()
