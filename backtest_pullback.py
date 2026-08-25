#!/usr/bin/env python3
"""
backtest_pullback.py — Chase the breakout, or wait in the corner for it?

THE IDEA BEING TESTED
    The live gate keeps rejecting coins at RSI 77 with "extreme overbought,
    pump exhaustion risk" — and the trade log agrees with it: 12 of the 18
    trades taken after the RSI threshold was loosened were thrown out on
    FastLoss within minutes. The bot is arriving late.

    Avi's proposal: do not discard those coins. Put them on a watchlist and
    wait for the pullback, then enter. Same coins, different moment.

    That is NOT the mean-reversion idea already tested and rejected here.
    Mean reversion buys weakness. This buys strength AFTER it cools — the
    classic trend-pullback entry.

THE TRAP THIS MUST NOT FALL INTO
    A coin that runs and never pulls back is never bought. Those are often
    the best moves. The same thing was measured with limit orders: 70-75%
    filled, and the quarter that never filled contained the winners. So the
    report counts the ones that got away, explicitly. A pullback rule that
    wins on the trades it takes while missing every big move is not an
    improvement, and without that column it would look like one.

NO LOOKAHEAD
    RSI at bar i uses bars[:i+1]. Entry is the OPEN of bar i+1. The pullback
    is detected forward in time from the trigger, one bar at a time, exactly
    as a live watchlist would see it.

SAFETY
    Reads public OHLCV. Never touches the wallet, never places an order.

USAGE
    python3 backtest_pullback.py --selftest
    python3 backtest_pullback.py --days 540 --exchange gateio
"""
from __future__ import annotations
import argparse, json, math, os, random, statistics as st, sys, time

BAR_MIN   = 60          # 1-hour bars: fine enough to time a pullback
NOTIONAL  = 500.0
FEE_PCT   = 0.06
SLIP_PCT  = 0.10
CACHE     = '/tmp/bt_pull_cache'

# Pre-registered. Changing these after seeing results is how backtests lie.
TRIGGER_RSI = 70.0      # the coin is "strong / extended"
PULLBACK_RSI = 55.0     # cooled off enough to enter
WAIT_BARS   = 12        # how long the watchlist keeps a name (12h)

OVERSOLD_RSI = 30.0     # the coin is beaten down
TURN_RSI     = 40.0     # and has started climbing back out
WAIT_LONG    = 72       # the bounce may take days, so watch for three
SL_PCT      = 2.5
TP_PCT      = 5.0
MAX_HOLD    = 24        # bars
COOLDOWN    = 24        # no re-entry on the same coin for a day

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT',
           'AVAX/USDT', 'DOGE/USDT', 'LINK/USDT', 'DOT/USDT', 'ATOM/USDT',
           'NEAR/USDT', 'APT/USDT', 'SUI/USDT', 'ARB/USDT', 'OP/USDT',
           'INJ/USDT', 'FET/USDT', 'LTC/USDT', 'BCH/USDT', 'ETC/USDT']


def rsi_series(closes: list, n: int = 14) -> list:
    """Wilder RSI at every bar, each value using only bars up to that point."""
    out = [50.0] * len(closes)
    if len(closes) < n + 1:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    return out


def simulate(bars: list, start: int) -> tuple[float, str]:
    """Long from the open of `start`. Stop checked before target."""
    entry = bars[start][1]
    if entry <= 0:
        return 0.0, 'bad'
    for k in range(1, MAX_HOLD + 1):
        j = start + k
        if j >= len(bars):
            break
        _, _o, h, l, _c, _v = bars[j]
        if (l - entry) / entry * 100 <= -SL_PCT:
            pct = -SL_PCT
            break
        if (h - entry) / entry * 100 >= TP_PCT:
            pct = TP_PCT
            break
    else:
        pct = None
    if pct is None:
        j = min(start + MAX_HOLD, len(bars) - 1)
        pct = (bars[j][4] - entry) / entry * 100
    cost = FEE_PCT * 2 + SLIP_PCT
    return (pct - cost) * NOTIONAL / 100.0, 'ok'


def run(data: dict, mode: str, seed: int) -> dict:
    """
    mode 'chase'    enter the bar after RSI crosses above TRIGGER_RSI
    mode 'pullback' after that trigger, wait up to WAIT_BARS for RSI to fall
                    to PULLBACK_RSI, then enter. If it never cools, the trade
                    is NOT taken and is counted as "got away".
    mode 'oversold'  enter as soon as RSI drops below OVERSOLD_RSI — catching
                     the falling knife. Already measured twice as worthless;
                     kept here so the comparison is on the same data.
    mode 'turn'      RSI dropped below OVERSOLD_RSI, then climbed back above
                     TURN_RSI within WAIT_LONG bars. Waits for the reversal to
                     be confirmed instead of guessing the bottom.
    mode 'random'   entries at the same rate, at random bars
    """
    rnd = random.Random(seed)
    pnl, missed, triggers = [], 0, 0

    for bars in data.values():
        closes = [b[4] for b in bars]
        rsi = rsi_series(closes)
        last = -10 ** 9
        i = 15
        while i < len(bars) - MAX_HOLD - 2:
            if i < last + COOLDOWN:
                i += 1
                continue

            if mode in ('oversold', 'turn'):
                if not (rsi[i] < OVERSOLD_RSI <= rsi[i - 1]):
                    i += 1
                    continue
                triggers += 1
                if mode == 'oversold':
                    p, ok = simulate(bars, i + 1)
                    if ok == 'ok':
                        pnl.append(p)
                        last = i
                else:
                    hit = None
                    for k in range(1, WAIT_LONG + 1):
                        j = i + k
                        if j >= len(bars) - MAX_HOLD - 2:
                            break
                        if rsi[j] >= TURN_RSI:
                            hit = j
                            break
                    if hit is None:
                        missed += 1      # kept sinking, never turned
                    else:
                        p, ok = simulate(bars, hit + 1)
                        if ok == 'ok':
                            pnl.append(p)
                            last = hit
                i += 1
                continue

            fired = rsi[i] > TRIGGER_RSI >= rsi[i - 1]
            if not fired:
                i += 1
                continue
            triggers += 1

            if mode == 'chase':
                p, ok = simulate(bars, i + 1)
                if ok == 'ok':
                    pnl.append(p)
                    last = i
            elif mode == 'pullback':
                hit = None
                for k in range(1, WAIT_BARS + 1):
                    j = i + k
                    if j >= len(bars) - MAX_HOLD - 2:
                        break
                    if rsi[j] <= PULLBACK_RSI:
                        hit = j
                        break
                if hit is None:
                    missed += 1          # ran away without ever cooling off
                else:
                    p, ok = simulate(bars, hit + 1)
                    if ok == 'ok':
                        pnl.append(p)
                        last = hit
            i += 1

    if mode == 'random':
        # Match the chase arm's trade count, drawn from all eligible bars.
        n_target = triggers
        pool = []
        for sym, bars in data.items():
            for i in range(15, len(bars) - MAX_HOLD - 2):
                pool.append((sym, i))
        for sym, i in rnd.sample(pool, min(n_target, len(pool))):
            p, ok = simulate(data[sym], i + 1)
            if ok == 'ok':
                pnl.append(p)

    return dict(pnl=pnl, missed=missed, triggers=triggers)


def report(label: str, r: dict, show_missed: bool = False):
    p = r['pnl']
    if len(p) < 2:
        print(f"  {label:<24}n={len(p):>4}   —")
        return
    m = st.mean(p)
    se = st.stdev(p) / math.sqrt(len(p))
    flag = '✅' if m - 1.96 * se > 0 else ('❌' if m + 1.96 * se < 0 else '➖')
    win = 100 * len([x for x in p if x > 0]) / len(p)
    extra = ''
    if show_missed:
        tot = len(p) + r['missed']
        extra = f"   ברחו: {r['missed']} ({100*r['missed']/max(tot,1):.0f}%)"
    print(f"  {label:<24}n={len(p):>4}  {m:+6.2f}$  זכייה {win:>3.0f}%  "
          f"נטו {sum(p):+8.2f}$ {flag}{extra}")


# ── Data ────────────────────────────────────────────────────────────────────
def fetch(ex, sym: str, since: int, need: int) -> list | None:
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f"{ex.id}_{sym.replace('/','_')}_{since}.json")
    if os.path.exists(p):
        return json.load(open(p))
    out, cur = [], since
    try:
        while len(out) < need:
            b = ex.fetch_ohlcv(sym, f'{BAR_MIN//60}h', since=cur, limit=1000)
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
    # 200 hourly bars is about eight days — thin, but enough for a coin to
    # contribute a few signals. Demanding 300 silently dropped whole symbols
    # when the exchange capped how far back it would page.
    if len(out) < 200:
        return None
    json.dump(out, open(p, 'w'))
    return out


def synthetic(n: int, seed: int) -> list:
    """Drift-free random walk. Nothing here may come out profitable."""
    r = random.Random(seed)
    sig = 0.012
    px, out, t = 100.0, [], 1_600_000_000_000
    for i in range(n):
        px *= math.exp(r.gauss(-0.5 * sig * sig, sig))
        o = px
        h = o * (1 + abs(r.gauss(0, 0.008)))
        l = o * (1 - abs(r.gauss(0, 0.008)))
        c = r.uniform(l, h)
        out.append([t + i * BAR_MIN * 60_000, o, h, l, c, 1000.0])
        px = c
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=540)
    ap.add_argument('--exchange', default='gateio')
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--sweep', action='store_true',
                    help='vary the pullback level — a real effect is a plateau')
    args = ap.parse_args()

    if args.selftest:
        print("בדיקה עצמית — עולם בלי מגמה. שום שורה לא אמורה לצאת ✅.\n")
        data = {f"SYN{i}": synthetic(4000, 300 + i) for i in range(12)}
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
        need = args.days * 24 * 60 // BAR_MIN
        since = ex.milliseconds() - args.days * 86_400_000
        print(f"[נתונים] {len(SYMBOLS)} מטבעות · נר שעה · {args.days} ימים")
        data, sizes = {}, []
        for s in SYMBOLS:
            b = fetch(ex, s, since, need)
            if b:
                data[s] = b
                sizes.append(len(b))
            else:
                sizes.append(0)
        print(f"[נתונים] נמשכו {len(data)}/{len(SYMBOLS)}  "
              f"· ביקשנו {need} נרות למטבע")
        if sizes:
            got = [x for x in sizes if x]
            print(f"[נתונים] נרות שהתקבלו: מינימום {min(got) if got else 0} · "
                  f"מקסימום {max(got) if got else 0} · "
                  f"חציון {sorted(got)[len(got)//2] if got else 0}")
            if got and max(got) < need * 0.5:
                print(f"  ⚠️ הבורסה החזירה הרבה פחות ממה שביקשנו.")
                print(f"     נסה --days {max(30, int(args.days * max(got) / need))}")
        print()
        if len(data) < 5:
            sys.exit("אין מספיק מטבעות — ראה את שורות הנתונים למעלה")

    print("=" * 78)
    print("  מתי להיכנס? חמש שיטות, אותה יציאה בדיוק")
    print(f"  (סטופ {SL_PCT}% · יעד {TP_PCT}% · עד {MAX_HOLD} שעות)")
    print("=" * 78)

    report('אקראי (הבקרה)', run(data, 'random', args.seed))
    print()
    print(f"  ── מטבע חזק (RSI חוצה {TRIGGER_RSI:.0f} למעלה) ──")
    report('כניסה מיידית', run(data, 'chase', args.seed))
    report(f'חכה לנסיגה (RSI≤{PULLBACK_RSI:.0f})',
           run(data, 'pullback', args.seed), show_missed=True)
    print()
    print(f"  ── מטבע במכירת יתר (RSI חוצה {OVERSOLD_RSI:.0f} למטה) ──")
    report('קנה מיד (סכין נופלת)', run(data, 'oversold', args.seed))
    report(f'חכה להיפוך (RSI≥{TURN_RSI:.0f})',
           run(data, 'turn', args.seed), show_missed=True)

    if args.sweep:
        g = globals()
        print("\n" + "=" * 78)
        print("  סריקת פרמטרים — אפקט אמיתי הוא מישור, לא נקודה")
        print("=" * 78)
        o = g['PULLBACK_RSI']
        for lvl in (45, 50, 55, 60, 65):
            g['PULLBACK_RSI'] = float(lvl)
            report(f'  נסיגה ל-{lvl}', run(data, 'pullback', args.seed),
                   show_missed=True)
        g['PULLBACK_RSI'] = o
        print()
        o = g['TURN_RSI']
        for lvl in (35, 40, 45, 50, 55):
            g['TURN_RSI'] = float(lvl)
            report(f'  היפוך ל-{lvl}', run(data, 'turn', args.seed),
                   show_missed=True)
        g['TURN_RSI'] = o

    print("\n" + "=" * 78)
    print("  איך לקרוא")
    print("=" * 78)
    print("  ✅ מרוויח מובהק · ➖ בתוך הרעש · ❌ מפסיד מובהק")
    print()
    print("  עמודת 'ברחו' היא הבלם: אלה מטבעות שהמשיכו לעלות ולא נתנו")
    print("  נסיגה, ולכן לא נקנו כלל. אם הם רבים, השיטה קונה את החלשים")
    print("  ומפספסת את החזקים — וזה ייראה טוב במספרים ורע בפועל.")
    print()
    print("  הרף: 'חכה לנסיגה' חייבת לנצח גם את האקראי וגם את הכניסה")
    print("  המיידית. אם היא מנצחת רק את המיידית — היא רק פחות גרועה.")


if __name__ == '__main__':
    main()
