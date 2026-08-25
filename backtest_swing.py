#!/usr/bin/env python3
"""
backtest_swing.py — Would the bot have made money holding for DAYS?

THE QUESTION
    The live bot capped every trade at 120 minutes, and two out of three
    trades closed on that timer rather than at a stop or a target. It paid a
    full round trip in fees and was then forced out before any multi-day move
    could develop.

    Nothing in this project ever tested the alternative, because the
    backtester's "swing" profile was also 120 minutes. So this file asks the
    one question that was never asked: same entry logic, but held for days —
    does it clear its costs?

WHAT IS COMPARED
    The identical entry signal is run under several holding limits, on the
    same candidates, over the same period:

        2 hours (what the bot did)  ·  1 day  ·  3 days  ·  7 days

    Alongside each, a RANDOM entry drawn from the same candidate pool at the
    same rate. Costs are charged on every trade. If the signal carries no
    information, longer holds will look exactly like random with longer
    holds — bigger swings around the same zero.

REGIME FILTER
    Avi's second idea: only trade when the market is strongly bullish or
    bearish. Regimes here are computed from PAST bars only at the moment of
    the decision, never in hindsight, and several definitions are swept —
    a rule that only works in one setting is luck, not a finding.

NO LOOKAHEAD
    Features at bar i use bars[:i+1]. Entry is the OPEN of bar i+1. Exits
    are simulated from i+1 onward. Enforced in one place: iter_decisions().

SAFETY
    Reads public OHLCV. Never touches the wallet, never places an order.

USAGE
    python3 backtest_swing.py --selftest          # synthetic, no network
    python3 backtest_swing.py --days 730 --exchange gateio
"""
from __future__ import annotations
import argparse, json, math, os, random, sys, time

BAR_MIN   = 240          # 4-hour bars: the natural granularity for swing
BARS_DAY  = 24 * 60 // BAR_MIN
CACHE     = '/tmp/bt_swing_cache'
POSITION  = 500.0        # notional per trade, same as the live bot
FEE_PCT   = 0.06         # taker, per side
SLIP_PCT  = 0.10         # round-trip friction, calibrated in backtest_exits

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT',
           'AVAX/USDT', 'DOGE/USDT', 'LINK/USDT', 'DOT/USDT', 'ATOM/USDT',
           'NEAR/USDT', 'APT/USDT', 'SUI/USDT', 'ARB/USDT', 'OP/USDT',
           'INJ/USDT', 'FET/USDT', 'LTC/USDT', 'BCH/USDT', 'ETC/USDT']

# Holding limits to compare. Targets widen with the horizon on purpose: a
# 7-day trade aiming at 0.8% would be absurd, and a 2-hour trade aiming at
# 20% would never fill. Each row is a coherent trade, not a knob to tune.
HOLDS = [
    ('שעתיים (מה שהבוט עשה)',  2 / 24,  2.0,  4.0),
    ('יום אחד',                 1.0,     4.0,  8.0),
    ('3 ימים',                  3.0,     6.0, 12.0),
    ('7 ימים',                  7.0,     8.0, 18.0),
]   # (label, days, stop %, target %)


# ── Indicators ───────────────────────────────────────────────────────────────
def ema_series(v: list, n: int) -> list:
    if not v:
        return []
    k = 2.0 / (n + 1)
    out = [v[0]]
    for x in v[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def rsi_at(c: list, n: int = 14) -> float:
    if len(c) < n + 1:
        return 50.0
    g = l = 0.0
    for i in range(len(c) - n, len(c)):
        d = c[i] - c[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    if l == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + (g / n) / (l / n))


def features_at(bars: list, i: int) -> dict | None:
    """Reads bars[:i+1] only. This is the lookahead boundary."""
    if i < 210:
        return None
    c = [b[4] for b in bars[:i + 1]]
    e9, e21 = ema_series(c[-120:], 9)[-1], ema_series(c[-120:], 21)[-1]
    e50 = ema_series(c[-200:], 50)[-1]
    macd = ema_series(c[-120:], 12)[-1] - ema_series(c[-120:], 26)[-1]
    return dict(price=c[-1], ema9=e9, ema21=e21, ema50=e50,
                macd=macd, rsi=rsi_at(c))


def score_bot_like(f: dict, direction: str) -> float:
    """The live engine's shape: EMA stack 35 / MACD 25 / RSI 20."""
    s = 0.0
    if direction == 'LONG':
        if f['ema9'] > f['ema21'] > f['ema50']: s += 35
        elif f['ema9'] > f['ema21']:            s += 18
        if f['macd'] > 0:                       s += 25
        if 45 <= f['rsi'] <= 70:                s += 20
    else:
        if f['ema9'] < f['ema21'] < f['ema50']: s += 35
        elif f['ema9'] < f['ema21']:            s += 18
        if f['macd'] < 0:                       s += 25
        if 30 <= f['rsi'] <= 55:                s += 20
    return s


# ── Market regime, computed from the past only ──────────────────────────────
DEAD = dict(bull=False, bear=False, strong_bull=False, strong_bear=False)


def build_regimes(btc: list) -> dict:
    """
    BTC's state at every timestamp, each computed from PAST bars only.

    Keyed by TIMESTAMP, not by bar index: two coins can have different
    history lengths, so bar 500 of ETH and bar 500 of BTC are not the same
    moment. Indexing by position would silently gate ETH's entries on BTC's
    state from a different week.
    """
    out = {}
    c_all = [b[4] for b in btc]
    look = BARS_DAY * 30
    for i in range(len(btc)):
        if i < 210:
            out[btc[i][0]] = DEAD
            continue
        c = c_all[:i + 1]
        e200 = ema_series(c[-210:], 200)[-1]
        chg30 = (c[-1] / c[-look] - 1) * 100 if len(c) > look else 0.0
        above = c[-1] > e200
        out[btc[i][0]] = dict(bull=above, bear=not above,
                              strong_bull=above and chg30 > 15.0,
                              strong_bear=(not above) and chg30 < -15.0)
    return out


REGIMES = {
    'הכל':          lambda r, d: True,
    'שורי':         lambda r, d: r['bull'] if d == 'LONG' else r['bear'],
    'שורי חזק':     lambda r, d: (r['strong_bull'] if d == 'LONG'
                                  else r['strong_bear']),
    'קיצון בלבד':   lambda r, d: r['strong_bull'] or r['strong_bear'],
}


# ── Exit simulation ─────────────────────────────────────────────────────────
def simulate(bars: list, start: int, entry: float, direction: str,
             hold_days: float, sl: float, tp: float) -> float:
    """
    Returns net P&L in dollars, costs included.
    Conservative: if stop and target fall in the same bar, the stop wins.
    """
    max_bars = max(1, int(hold_days * BARS_DAY))
    sign = 1.0 if direction == 'LONG' else -1.0
    pct = None
    for k in range(1, max_bars + 1):
        j = start + k
        if j >= len(bars):
            break
        _, _o, h, l, _c, _v = bars[j]
        up = sign * ((h if direction == 'LONG' else l) - entry) / entry * 100
        dn = sign * ((l if direction == 'LONG' else h) - entry) / entry * 100
        if dn <= -sl:          # stop checked first — never flatter a result
            pct = -sl
            break
        if up >= tp:
            pct = tp
            break
    if pct is None:            # time ran out: close at the last close
        j = min(start + max_bars, len(bars) - 1)
        pct = sign * (bars[j][4] - entry) / entry * 100
    cost = FEE_PCT * 2 + SLIP_PCT
    return (pct - cost) * POSITION / 100.0


def iter_decisions(bars: list, every: int = 3):
    """Yields (i, features). The only place lookahead could enter."""
    for i in range(210, len(bars) - 2, every):
        f = features_at(bars, i)
        if f:
            yield i, f


# ── Runner ──────────────────────────────────────────────────────────────────
def run(data: dict, reg_map: dict, threshold: float, hold_days: float,
        sl: float, tp: float, regime: str, mode: str, seed: int) -> list:
    """
    mode 'signal' trades when the score passes; mode 'random' trades the SAME
    candidates at the same rate but picks them at random. That control is
    what separates "the signal works" from "we simply traded more".
    """
    rnd = random.Random(seed)
    gate = REGIMES[regime]
    out = []

    # First pass, control only: how often does the real signal fire? The
    # random arm is then tuned to that rate, so both arms take a comparable
    # number of trades and only the SELECTION differs.
    rate = 0.0
    if mode == 'random':
        seen = taken = 0
        for bars in data.values():
            last = -10 ** 9
            for i, f in iter_decisions(bars):
                if i < last:
                    continue
                r = reg_map.get(bars[i][0], DEAD)
                for d in ('LONG', 'SHORT'):
                    if not gate(r, d):
                        continue
                    seen += 1
                    if score_bot_like(f, d) >= threshold:
                        taken += 1
                        last = i + int(hold_days * BARS_DAY)
                        break
        rate = taken / seen if seen else 0.0

    for bars in data.values():
        last = -10 ** 9
        for i, f in iter_decisions(bars):
            if i < last:
                continue
            r = reg_map.get(bars[i][0], DEAD)
            for d in ('LONG', 'SHORT'):
                if not gate(r, d):
                    continue
                fire = (score_bot_like(f, d) >= threshold if mode == 'signal'
                        else rnd.random() < rate)
                if not fire:
                    continue
                s = i + 1
                entry = bars[s][1]
                if not entry or entry <= 0:
                    continue
                out.append(simulate(bars, s, entry, d, hold_days, sl, tp))
                last = i + int(hold_days * BARS_DAY)
                break
    return out


def stats(v: list) -> dict:
    n = len(v)
    if n < 2:
        return dict(n=n, mean=0.0, lo=0.0, hi=0.0, win=0.0, total=0.0)
    m = sum(v) / n
    sd = (sum((x - m) ** 2 for x in v) / (n - 1)) ** 0.5
    se = sd / math.sqrt(n)
    return dict(n=n, mean=m, lo=m - 1.96 * se, hi=m + 1.96 * se,
                total=sum(v), win=100 * len([x for x in v if x > 0]) / n)


def verdict(s: dict) -> str:
    if s['n'] < 30:
        return 'מעט מדי עסקאות'
    if s['lo'] > 0:
        return '✅ מרוויח'
    if s['hi'] < 0:
        return '❌ מפסיד'
    return '➖ אפס'


def line(label: str, v: list):
    s = stats(v)
    if s['n'] < 2:
        print(f"  {label:<26}{s['n']:>5}   —")
        return
    print(f"  {label:<26}{s['n']:>5}{s['mean']:>+10.2f}$"
          f"{s['win']:>7.0f}%   {verdict(s)}")


# ── Data ────────────────────────────────────────────────────────────────────
def fetch(ex, sym: str, since: int, need: int) -> list | None:
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f"{ex.id}_{sym.replace('/', '_')}_{since}.json")
    if os.path.exists(p):
        return json.load(open(p))
    out, cur = [], since
    try:
        while len(out) < need:
            b = ex.fetch_ohlcv(sym, f'{BAR_MIN // 60}h', since=cur, limit=1000)
            if not b:
                break
            out += b
            nxt = b[-1][0] + BAR_MIN * 60_000
            if nxt <= cur:
                break
            cur = nxt
            time.sleep(ex.rateLimit / 1000)
    except Exception:
        return None
    if len(out) < 300:
        return None
    json.dump(out, open(p, 'w'))
    return out


def synthetic(n: int, seed: int) -> list:
    """Random walk — no edge by construction. Nothing here may look good."""
    r = random.Random(seed)
    px, out, t = 100.0, [], 1_600_000_000_000
    for i in range(n):
        px *= math.exp(r.gauss(0.0002, 0.02))
        o = px
        h = o * (1 + abs(r.gauss(0, 0.012)))
        l = o * (1 - abs(r.gauss(0, 0.012)))
        c = r.uniform(l, h)
        out.append([t + i * BAR_MIN * 60_000, o, h, l, c, 1000.0])
        px = c
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=730)
    ap.add_argument('--threshold', type=float, default=75.0)
    ap.add_argument('--exchange', default='gateio')
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()

    if args.selftest:
        print("בדיקה עצמית — נתונים אקראיים סינתטיים, אין בהם יתרון בהגדרה.")
        print("כל שורה שתראה '✅ מרוויח' כאן היא באג, לא ממצא.\n")
        data = {f"SYN{i}": synthetic(3000, 50 + i) for i in range(8)}
        btc = data['SYN0']
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
        need = args.days * BARS_DAY
        since = ex.milliseconds() - args.days * 86_400_000
        print(f"[נתונים] {len(SYMBOLS)} מטבעות · נר {BAR_MIN // 60} שעות · "
              f"{args.days} ימים · {args.exchange}")
        data = {}
        for s in SYMBOLS:
            b = fetch(ex, s, since, need)
            if b:
                data[s] = b
        print(f"[נתונים] נמשכו {len(data)}/{len(SYMBOLS)}\n")
        if 'BTC/USDT' not in data:
            sys.exit("חסר BTC — אין איך לקבוע משטר שוק")
        btc = data['BTC/USDT']

    reg_map = build_regimes(btc)

    # ── the main question ───────────────────────────────────────────────
    print("=" * 74)
    print("  כמה זמן להחזיק?   (אותו איתות כניסה בדיוק, אחרי עמלות)")
    print("=" * 74)
    print(f"  {'':<26}{'עסקאות':>5}{'לעסקה':>11}{'זכייה':>7}")
    for label, d, sl, tp in HOLDS:
        line(label, run(data, reg_map, args.threshold, d, sl, tp,
                        'הכל', 'signal', args.seed))

    print("\n  בקרה — כניסות אקראיות באותה תדירות:")
    for label, d, sl, tp in HOLDS[::3]:
        line(f'אקראי · {label}', run(data, reg_map, args.threshold, d, sl, tp,
                                     'הכל', 'random', args.seed))

    # ── the regime question ─────────────────────────────────────────────
    best = max(HOLDS, key=lambda h: h[1])
    print("\n" + "=" * 74)
    print(f"  האם משטר השוק עוזר?   (החזקה: {best[0]})")
    print("=" * 74)
    print(f"  {'':<26}{'עסקאות':>5}{'לעסקה':>11}{'זכייה':>7}")
    for reg in REGIMES:
        line(reg, run(data, reg_map, args.threshold, best[1], best[2], best[3],
                      reg, 'signal', args.seed))
        line(f'  ↳ אקראי באותו משטר',
             run(data, reg_map, args.threshold, best[1], best[2], best[3],
                 reg, 'random', args.seed))

    print("\n" + "=" * 74)
    print("  איך לקרוא")
    print("=" * 74)
    print("  ✅ מרוויח   הרווח אמיתי — גם הגבול התחתון של הטווח מעל אפס")
    print("  ➖ אפס      לא מרוויח ולא מפסיד. זה מה שנמצא עד היום בכל בדיקה.")
    print("  ❌ מפסיד    מפסיד בוודאות")
    print()
    print("  הכלל: האיתות שווה משהו רק אם הוא מנצח את האקראי שלידו.")
    print("  אם שניהם באותו מקום — האורך שינה, האיתות לא.")


if __name__ == '__main__':
    main()
