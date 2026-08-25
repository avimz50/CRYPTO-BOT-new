#!/usr/bin/env python3
"""
backtest_ranking.py — Is there ANY way to pick coins that beats picking at random?

WHY THIS IS A DIFFERENT QUESTION
    Everything tested so far asked: "does THIS coin pass a threshold right
    now?" Each coin was judged alone, and the answer was always no better
    than a coin flip.

    This asks something else: "of the coins available today, WHICH ONE is
    best?" That is a ranking, not a filter, and it is how essentially every
    quantitative equity fund actually operates. Cross-sectional momentum —
    buy what has gone up most, relative to its peers — is one of the few
    effects in finance with decades of independent replication behind it.
    It has never been tested here.

HOW IT WORKS
    Every REBAL days: rank every coin by a rule computed from PAST data,
    buy the top N in equal weight, hold, repeat. Fees on every rebalance.

WHAT IT IS MEASURED AGAINST
    random   the same number of coins, drawn at random           <- the control
    all      hold every coin, equal weight, never rebalance      <- the market

    A rule is only worth anything if it beats BOTH. Beating "all" while
    losing to "random" means the rule is noise wearing a suit; beating
    "random" while losing to "all" means it is just an expensive way of
    owning the market.

NO LOOKAHEAD
    Ranking at time t uses bars up to t. The position earns the return from
    t to t+REBAL. Enforced in rank_at().

SAFETY
    Reads public OHLCV. Never touches the wallet, never places an order.

USAGE
    python3 backtest_ranking.py --selftest
    python3 backtest_ranking.py --days 730 --exchange gateio
"""
from __future__ import annotations
import argparse, math, random, sys

import backtest_swing as bs      # reuse its fetcher, cache and bar size

TOP_N     = 5        # how many coins to hold at a time
REBAL_D   = 7        # rebalance every 7 days
COST_PCT  = bs.FEE_PCT * 2 + bs.SLIP_PCT     # round trip, per position


# ── Ranking rules. Higher score = more attractive. Past data only. ──────────
def _ret(bars: list, i: int, days: int) -> float:
    k = int(days * bs.BARS_DAY)
    if i - k < 0 or bars[i - k][4] <= 0:
        return 0.0
    return bars[i][4] / bars[i - k][4] - 1.0


def _vol(bars: list, i: int, days: int) -> float:
    k = int(days * bs.BARS_DAY)
    if i - k < 1:
        return 0.0
    c = [b[4] for b in bars[i - k:i + 1]]
    r = [c[j] / c[j - 1] - 1 for j in range(1, len(c)) if c[j - 1] > 0]
    if len(r) < 2:
        return 0.0
    m = sum(r) / len(r)
    return (sum((x - m) ** 2 for x in r) / (len(r) - 1)) ** 0.5


RULES = {
    'מומנטום 30 יום':   lambda b, i: _ret(b, i, 30),
    'מומנטום 7 ימים':   lambda b, i: _ret(b, i, 7),
    'היפוך 30 יום':     lambda b, i: -_ret(b, i, 30),
    'תנודתיות נמוכה':   lambda b, i: -_vol(b, i, 30),
    'הציון של הבוט':    None,        # handled separately, needs features
}


def bot_score_at(bars: list, i: int) -> float:
    f = bs.features_at(bars, i)
    if not f:
        return -1e9
    return bs.score_bot_like(f, 'LONG')


# ── Engine ──────────────────────────────────────────────────────────────────
def run(data: dict, rule: str, seed: int, top_n: int, rebal_d: int) -> list:
    """
    Returns the list of per-period portfolio returns in percent, net of costs.
    'random' picks at random; 'all' holds everything and never rebalances,
    so it pays entry costs once and nothing after.
    """
    rnd = random.Random(seed)
    syms = list(data)
    n_bars = min(len(b) for b in data.values())
    step = int(rebal_d * bs.BARS_DAY)
    out, prev = [], set()

    for t in range(240, n_bars - step, step):
        avail = [s for s in syms if data[s][t][4] > 0]
        if len(avail) < top_n:
            continue

        if rule == 'random':
            pick = rnd.sample(avail, top_n)
        elif rule == 'all':
            pick = avail
        elif rule == 'הציון של הבוט':
            pick = sorted(avail, key=lambda s: -bot_score_at(data[s], t))[:top_n]
        else:
            fn = RULES[rule]
            pick = sorted(avail, key=lambda s: -fn(data[s], t))[:top_n]

        gross = 0.0
        for s in pick:
            p0, p1 = data[s][t][4], data[s][t + step][4]
            if p0 > 0:
                gross += (p1 / p0 - 1.0) * 100.0
        gross /= len(pick)

        # Cost only on the part of the book that actually changed.
        cur = set(pick)
        turnover = len(cur - prev) / len(cur) if cur else 0.0
        if rule == 'all':
            turnover = 1.0 if not prev else 0.0
        prev = cur
        out.append(gross - turnover * COST_PCT)
    return out


def driftless(n: int, seed: int) -> list:
    """
    A martingale: E[next/current] is exactly 1.0, so no rule can profit.

    The first version of this selftest used a generator with positive drift,
    and every rule — including picking at random — came back profitable.
    That is the correct behaviour for a rising market and completely useless
    as a test. A selftest has to be a world where the honest answer is
    "nothing works", or it cannot detect a bug.
    """
    r = random.Random(seed)
    sig = 0.02
    px, out, t = 100.0, [], 1_600_000_000_000
    for i in range(n):
        px *= math.exp(r.gauss(-0.5 * sig * sig, sig))   # drift-free
        o = px
        h = o * (1 + abs(r.gauss(0, 0.012)))
        l = o * (1 - abs(r.gauss(0, 0.012)))
        c = r.uniform(l, h)
        out.append([t + i * bs.BAR_MIN * 60_000, o, h, l, c, 1000.0])
        px = c
    return out


def stats(v: list) -> dict:
    n = len(v)
    if n < 2:
        return dict(n=n, mean=0.0, lo=0.0, hi=0.0, win=0.0)
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / (n - 1)) ** 0.5
    se = sd / math.sqrt(n)
    return dict(n=n, mean=m, lo=m - 1.96 * se, hi=m + 1.96 * se,
                win=100 * len([x for x in v if x > 0]) / n)


def verdict(s: dict) -> str:
    if s['n'] < 20:
        return 'מעט מדי תקופות'
    if s['lo'] > 0:
        return '✅ מרוויח'
    if s['hi'] < 0:
        return '❌ מפסיד'
    return '➖ אפס'


def _mean(v: list) -> float:
    return sum(v) / len(v) if v else 0.0


def line(label: str, v: list, rnds: list | None = None):
    """
    Two hurdles, not one.

    1. Beat the random draws overall.
    2. Beat them in BOTH halves of the history, separately.

    The second hurdle exists because the first one is far weaker than it
    looks. Running this selftest on three different noise worlds, "0 out of
    25" — a perfect score against the control — came up twice. With six
    rules on the table, one of them looking brilliant by luck is the
    expected outcome, not a surprise. An effect that is real shows up in the
    first half and in the second half; luck almost never does both.
    """
    s = stats(v)
    if rnds is None:
        print(f"  {label:<22}{s['mean']:>+8.2f}%{s['win']:>7.0f}%"
              f"{'—':>10}{'—':>12}")
        return

    beat = sum(1 for r in rnds if _mean(r) > s['mean'])
    h = len(v) // 2
    h1 = all(_mean(r[:len(r) // 2]) <= _mean(v[:h]) for r in rnds)
    h2 = all(_mean(r[len(r) // 2:]) <= _mean(v[h:]) for r in rnds)

    if beat == 0 and h1 and h2:
        tag = '✅ יציב'
    elif beat == 0:
        tag = '⚠️ רק בחצי'
    elif beat <= len(rnds) * 0.1:
        tag = 'גבולי'
    else:
        tag = 'בתוך הרעש'
    print(f"  {label:<22}{s['mean']:>+8.2f}%{s['win']:>7.0f}%"
          f"{beat:>4}/{len(rnds):<5}{tag:<12}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=730)
    ap.add_argument('--exchange', default='gateio')
    ap.add_argument('--top', type=int, default=TOP_N)
    ap.add_argument('--rebal', type=int, default=REBAL_D)
    ap.add_argument('--draws', type=int, default=25)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()

    if args.selftest:
        print("בדיקה עצמית — עולם בלי מגמה כלל. שום כלל לא אמור לנצח אקראי.")
        print("כל שורה שכתוב לידה 'מנצח אקראי' כאן היא באג.\n")
        # --seed changes the WORLD, not just the control draws, so the
        # selftest can be repeated on fresh noise. A harness that only ever
        # sees one random world can be fooled by that one world.
        data = {f"SYN{i}": driftless(3000, args.seed * 1000 + i)
                for i in range(20)}
    else:
        try:
            import ccxt
        except ImportError:
            sys.exit("צריך ccxt:  pip install ccxt")
        ex = getattr(ccxt, args.exchange)({'enableRateLimit': True})
        try:
            ex.load_markets()
        except Exception as e:
            sys.exit(f"אין גישה ל-{args.exchange}: {type(e).__name__}")
        need = args.days * bs.BARS_DAY
        since = ex.milliseconds() - args.days * 86_400_000
        print(f"[נתונים] {len(bs.SYMBOLS)} מטבעות · נר 4 שעות · "
              f"{args.days} ימים · {args.exchange}")
        data = {}
        for s in bs.SYMBOLS:
            b = bs.fetch(ex, s, since, need)
            if b:
                data[s] = b
        print(f"[נתונים] נמשכו {len(data)}/{len(bs.SYMBOLS)}\n")
        if len(data) < args.top + 2:
            sys.exit("אין מספיק מטבעות")

    # The control first, so every rule below is measured against it.
    rnds = [run(data, 'random', args.seed + d, args.top, args.rebal)
            for d in range(args.draws)]
    ms = sorted(_mean(r) for r in rnds)
    flat = [x for r in rnds for x in r]

    print("=" * 78)
    print(f"  לבחור {args.top} מטבעות כל {args.rebal} ימים — איזה כלל בחירה עדיף?")
    print("=" * 78)
    print(f"  {'':<22}{'לתקופה':>8}{'זכייה':>7}{'אקראי שניצחו':>10}  פסיקה")

    line('אקראי (הבקרה)', flat)
    print(f"    טווח {args.draws} ההגרלות: {ms[0]:+.2f}% עד {ms[-1]:+.2f}%")
    line('להחזיק את הכל', run(data, 'all', args.seed, args.top, args.rebal),
         rnds)
    print()
    for rule in RULES:
        line(rule, run(data, rule, args.seed, args.top, args.rebal), rnds)

    print("\n" + "=" * 78)
    print("  איך לקרוא")
    print("=" * 78)
    print("  ✅ יציב        ניצח את כל ההגרלות האקראיות — וגם בחצי הראשון")
    print("                של התקופה וגם בחצי השני, בנפרד. זה מה שמחפשים.")
    print("  ⚠️ רק בחצי    ניצח בסך הכל, אבל לא בשתי המחציות.")
    print("                נבדקו 6 כללים — שאחד ייראה מבריק במקרה זה הצפוי.")
    print("  בתוך הרעש     לא טוב יותר מהטלת מטבע, גם אם המספר חיובי.")
    print()
    print("  ושורת 'להחזיק את הכל' היא הרף השני: אם היא הכי טובה,")
    print("  כל הבחירה מיותרת ועדיף פשוט להחזיק את הסל.")


if __name__ == '__main__':
    main()
