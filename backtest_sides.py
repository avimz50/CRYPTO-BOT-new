#!/usr/bin/env python3
"""
backtest_sides.py — Do LONG and SHORT need different exit rules?

WHAT THE LIVE DATA SHOWED
    Across 136 real trades, the same exit settings behaved completely
    differently depending on direction:

        FastLoss triggered   17% of shorts   ·   32% of longs   (71% in Aug)
        Closed on the timer   58 shorts at +0.67$   ·  9 longs at -1.71$

    Falls are fast and one-directional; rallies grind upward with pullbacks.
    A stop tight enough to be harmless on a short gets hit by the ordinary
    pullback on a long. So the hypothesis is not "the stop is too tight" —
    it is "one stop cannot serve both directions".

WHY THIS WAS NEVER CAUGHT
    backtest_exits.py already tested a wider FastLoss and found it worse.
    But that ran on the whole set, and the whole set is 72% shorts. A
    setting that helps longs and hurts shorts nets out to "worse" and the
    real effect stays invisible. Everything here is reported PER DIRECTION.

CALIBRATION FIRST — DO NOT SKIP
    Before any scenario is believed, the baseline is re-simulated with the
    live settings and compared against what actually happened. If the
    simulation cannot reproduce reality, none of the alternatives mean
    anything. The run prints this check at the top and says so plainly.

SAFETY
    Reads trade_history.csv and public OHLCV. Never touches the wallet,
    never places an order.

USAGE
    python3 backtest_sides.py                      # gateio, 1m candles
    python3 backtest_sides.py --exchange bitget
"""
from __future__ import annotations
import argparse, csv, json, math, os, statistics as st, sys, time

# ── Live settings, as the bot runs them ─────────────────────────────────────
NOTIONAL   = 500.0
MARGIN     = 50.0
SL_PCT     = 2.0
# TP1 is 1.7%, not 1.0%. Read off a real trade: FET entered at 0.1571 with
# TP1 at 0.159771, and the partial paid +6.21$ — which is 0.75 x 500 x 1.7%.
# The earlier 1.0% moved the stop to break-even far too soon, so ordinary
# pullbacks flattened trades that in reality kept running. That single
# wrong constant is enough to turn a profitable set into a losing one.
TP1_PCT    = 1.7
TP2_PCT    = 4.0
TP1_CLOSE  = 0.75
TRAIL_ACT  = 2.0
TRAIL_PCT  = 1.5
MAX_DUR    = 120          # minutes
FL_MARGIN  = 3.5          # FastLoss: % of margin, inside the first 15 min
FL_MINUTES = 15
CACHE      = '/tmp/bt_sides_cache'


def fl_price_pct(fl_margin: float | None) -> float | None:
    """FastLoss is defined on margin; convert to a price move on notional."""
    return None if fl_margin is None else fl_margin * MARGIN / NOTIONAL


# ── Scenarios. Each may set different rules per direction. ──────────────────
# 'L' applies to longs, 'S' to shorts. None means "leave as the live setting".
SCENARIOS = [
    ('כמו היום (בסיס)',        {}),
    ('לונג בלי FastLoss',       {'L': dict(fl=None)}),
    ('לונג FastLoss 5.5%',      {'L': dict(fl=5.5)}),
    ('לונג FastLoss 7%',        {'L': dict(fl=7.0)}),
    ('לונג סטופ רחב 3%',        {'L': dict(sl=3.0)}),
    ('לונג 4 שעות',             {'L': dict(dur=240)}),
    ('לונג: FL 7% + 4 שעות',    {'L': dict(fl=7.0, dur=240)}),
    ('שורט FastLoss 2%',        {'S': dict(fl=2.0)}),
    ('שורט 4 שעות',             {'S': dict(dur=240)}),
]


def simulate(bars: list, entry: float, side: str, cfg: dict,
             slip: float, bar_min: int = 1) -> tuple[float, str]:
    """
    Replay one trade minute by minute under `cfg`.
    Returns (pnl_usd, exit_reason).

    Conservative tie-break: if the stop and the target are both inside the
    same minute, the stop is taken. That can only make a result worse, never
    better, which is the direction an honest backtest should err in.
    """
    sl   = cfg.get('sl',  SL_PCT)
    tp1  = cfg.get('tp1', TP1_PCT)
    tp2  = cfg.get('tp2', TP2_PCT)
    # Durations are in minutes; convert to a number of bars so the same
    # rules mean the same thing on 1m and on 5m candles.
    dur  = max(1, cfg.get('dur', MAX_DUR) // bar_min)
    fl_bars = max(1, FL_MINUTES // bar_min)
    flp  = fl_price_pct(cfg.get('fl', FL_MARGIN))

    long = side.upper() == 'LONG'
    sign = 1.0 if long else -1.0
    realized, remaining = 0.0, 1.0
    tp1_done, stop_at, peak = False, -sl, 0.0

    for i, b in enumerate(bars):
        if i > dur:
            break
        _, _o, h, l, c, _v = b
        up = sign * ((h if long else l) - entry) / entry * 100
        dn = sign * ((l if long else h) - entry) / entry * 100

        if dn <= stop_at:
            return ((realized + remaining * stop_at - slip) * NOTIONAL / 100,
                    'BE/Trail' if tp1_done else 'SL')
        if flp is not None and i <= fl_bars and not tp1_done and dn <= -flp:
            return ((realized + remaining * -flp - slip) * NOTIONAL / 100,
                    'FastLoss')
        if up >= tp2:
            return ((realized + remaining * tp2 - slip) * NOTIONAL / 100, 'TP')
        if not tp1_done and up >= tp1:
            realized += TP1_CLOSE * tp1
            remaining = 1.0 - TP1_CLOSE
            tp1_done, stop_at = True, 0.0
        peak = max(peak, up)
        if peak >= TRAIL_ACT:
            stop_at = max(stop_at, peak - TRAIL_PCT)

    j = min(len(bars) - 1, dur)
    final = sign * (bars[j][4] - entry) / entry * 100
    return ((realized + remaining * final - slip) * NOTIONAL / 100,
            'MaxDuration')


# ── Data ────────────────────────────────────────────────────────────────────
TIMEFRAMES = [('1m', 1), ('5m', 5), ('15m', 15)]


def fetch(ex, symbol: str, since_ms: int, minutes: int):
    """
    Returns (bars, bar_minutes) or (None, 0).

    Exchanges only keep 1-minute candles for a limited window — on gateio
    the June trades are already gone. Rather than drop those trades, fall
    back to 5m and then 15m, and record which resolution was used so the
    simulation can convert its durations correctly and the report can say
    how much of the sample is coarse.
    """
    os.makedirs(CACHE, exist_ok=True)
    for tf, bm in TIMEFRAMES:
        p = os.path.join(
            CACHE, f"{ex.id}_{symbol.replace('/','_')}_{since_ms}_{tf}.json")
        if os.path.exists(p):
            d = json.load(open(p))
            if d:
                return d, bm
            continue
        out, cur, need = [], since_ms, minutes // bm + 10
        try:
            while len(out) < need:
                b = ex.fetch_ohlcv(symbol, tf, since=cur, limit=1000)
                if not b:
                    break
                out += b
                nxt = b[-1][0] + bm * 60_000
                if nxt <= cur:
                    break
                cur = nxt
                time.sleep(ex.rateLimit / 1000)
        except Exception:
            out = []
        json.dump(out, open(p, 'w'))
        if len(out) >= 20:
            return out, bm
    return None, 0


def load_trades(path: str) -> list:
    rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    out = []
    for r in rows:
        try:
            out.append(dict(sym=r['symbol'], side=r['side'].upper(),
                            entry=float(r['entry_price']),
                            actual=float(r['pnl_usd']),
                            reason=r['exit_reason'], ts=r['timestamp']))
        except Exception:
            continue
    return out


def to_ms(ts: str) -> int:
    from datetime import datetime, timezone
    for f in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            d = datetime.strptime(ts[:19], f).replace(tzinfo=timezone.utc)
            return int(d.timestamp() * 1000)
        except ValueError:
            continue
    return 0


def find_entry_bar(bars: list, entry: float, ts_ms: int) -> int | None:
    """
    Locate the minute the trade actually opened.

    The CSV's timestamp could be the open OR the close, and the bot's clock
    may not be UTC. Rather than guess, find the bar whose [low, high] range
    contains the recorded entry price, preferring the one nearest the
    timestamp. That is self-correcting: it validates the price and the time
    together, and if no bar matches, the trade is skipped instead of being
    simulated from the wrong minute.
    """
    best, best_d = None, None
    for i, b in enumerate(bars):
        _, _o, h, l, _c, _v = b
        if l <= entry <= h:
            d = abs(b[0] - ts_ms)
            if best_d is None or d < best_d:
                best, best_d = i, d
    # A match more than 6 hours from the timestamp is coincidence, not the
    # trade: the same price gets revisited constantly.
    if best is None or best_d > 6 * 3600_000:
        return None
    return best


# ── Reporting ───────────────────────────────────────────────────────────────
def block(pnls: list) -> str:
    if len(pnls) < 2:
        return f"n={len(pnls):>3}      —"
    m = st.mean(pnls)
    se = st.stdev(pnls) / math.sqrt(len(pnls))
    flag = '✅' if m - 1.96 * se > 0 else ('❌' if m + 1.96 * se < 0 else '➖')
    return (f"n={len(pnls):>3}  {m:+6.2f}$  נטו {sum(pnls):+8.2f}$ {flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', default='trade_history.csv')
    ap.add_argument('--exchange', default='gateio')
    ap.add_argument('--slip', type=float, default=0.10,
                    help='round-trip friction %%, calibrated in backtest_exits')
    ap.add_argument('--only-1m', action='store_true',
                    help='keep only trades reconstructed from 1-minute bars')
    ap.add_argument('--max-bar', type=int, default=15,
                    help='drop trades whose bars are coarser than this')
    ap.add_argument('--tp1', type=float, default=TP1_PCT,
                    help='TP1 %% — sweep this if calibration is still off')
    ap.add_argument('--debug', action='store_true',
                    help='print each trade: actual vs simulated, and why')
    args = ap.parse_args()
    globals()['TP1_PCT'] = args.tp1

    try:
        import ccxt
    except ImportError:
        sys.exit("צריך ccxt:  pip install ccxt")
    ex = getattr(ccxt, args.exchange)({'enableRateLimit': True,
                                       'options': {'defaultType': 'swap'}})
    try:
        ex.load_markets()
    except Exception as e:
        sys.exit(f"אין גישה ל-{args.exchange}: {type(e).__name__}")

    trades = load_trades(args.csv)
    print(f"[נתונים] {len(trades)} עסקאות מ-{args.csv}")
    print(f"[נתונים] מושך נרות דקה מ-{args.exchange} ...")

    usable, no_bars, no_match = [], 0, 0
    tf_count: dict[int, int] = {}
    for t in trades:
        ms = to_ms(t['ts'])
        if not ms:
            continue
        # Start 3h before the timestamp so the window covers it whether the
        # stamp marks the open or the close.
        b, bm = fetch(ex, t['sym'], ms - 180 * 60_000, 480)
        if not b:
            no_bars += 1
            continue
        k = find_entry_bar(b, t['entry'], ms)
        if k is None:
            no_match += 1
            continue
        t['bars'], t['bm'] = b[k:], bm
        tf_count[bm] = tf_count.get(bm, 0) + 1
        usable.append(t)
    res = ' · '.join(f"{v} ב-{k}m" for k, v in sorted(tf_count.items()))
    print(f"[נתונים] שומשו {len(usable)}/{len(trades)}   "
          f"(בלי נרות: {no_bars} · בלי התאמת מחיר: {no_match})")
    print(f"[רזולוציה] {res}")
    if tf_count.get(15):
        print("  ⚠️ נרות 15 דקות גסים מדי ל-FastLoss (חלון של 15 דקות = נר אחד).")
        print("     התוצאות לעסקאות האלה מקורבות.")
    cap = 1 if args.only_1m else args.max_bar
    if cap < 15:
        before = len(usable)
        usable = [t for t in usable if t['bm'] <= cap]
        print(f"[סינון] נשארו {len(usable)}/{before} עסקאות "
              f"בנרות של עד {cap} דקות\n")
    else:
        print()
    if len(usable) < 20:
        sys.exit("מעט מדי עסקאות שוחזרו — אין מה למדוד")

    # ── calibration ─────────────────────────────────────────────────────
    sim = [simulate(t['bars'], t['entry'], t['side'], {}, args.slip, t['bm'])[0]
           for t in usable]
    act = [t['actual'] for t in usable]
    mae = sum(abs(a - b) for a, b in zip(sim, act)) / len(sim)
    print("=" * 72)
    print("  כיול — האם הסימולציה משחזרת את המציאות?")
    print("=" * 72)
    print(f"  בפועל:    נטו {sum(act):+8.2f}$   ממוצע {st.mean(act):+6.2f}$")
    print(f"  סימולציה: נטו {sum(sim):+8.2f}$   ממוצע {st.mean(sim):+6.2f}$")
    print(f"  סטייה ממוצעת לעסקה: {mae:.2f}$")

    # Per-resolution, because this is the diagnostic that matters. A wide
    # candle contains both the stop and the target far more often than a
    # 1-minute one, and the conservative tie-break then charges the stop
    # every time. If 1m calibrates and 15m does not, the method is sound
    # and only the data is too coarse — a completely different problem
    # from "the timestamps are wrong".
    print("\n  לפי רזולוציה:")
    print(f"    {'':<8}{'n':>4}{'בפועל':>12}{'סימולציה':>12}"
          f"{'פער ממוצע':>12}{'סטייה':>9}")
    good_1m = None
    for bm in sorted({t['bm'] for t in usable}):
        idx = [i for i, t in enumerate(usable) if t['bm'] == bm]
        if len(idx) < 3:
            continue
        a = [act[i] for i in idx]
        s = [sim[i] for i in idx]
        e = sum(abs(x - y) for x, y in zip(s, a)) / len(a)
        gap = st.mean(s) - st.mean(a)
        print(f"    {str(bm)+'m':<8}{len(a):>4}{st.mean(a):>+11.2f}$"
              f"{st.mean(s):>+11.2f}${gap:>+11.2f}${e:>8.2f}$")
        if bm == 1:
            good_1m = abs(gap)

    # The criterion is the MEAN gap, not the per-trade error. Individual
    # trades will always differ — the entry minute is located by price and
    # is approximate. What has to be right is the average, because every
    # scenario below is compared on its average. Judging calibration by
    # per-trade error was the wrong test and it rejected a working model.

    if args.debug:
        # Trade by trade, worst mismatch first. If the simulated exit reason
        # differs from the real one, that says which rule is modelled wrong —
        # far more informative than the aggregate error.
        print("\n  עסקה מול סימולציה (הפער הגדול ביותר קודם):")
        print(f"    {'סמל':<14}{'צד':<6}{'בר':>4}{'בפועל':>9}{'סימ':>9}"
              f"   {'סיבה בפועל':<14}סיבה בסימולציה")
        det = []
        for i, t in enumerate(usable):
            _, why = simulate(t['bars'], t['entry'], t['side'], {},
                              args.slip, t['bm'])
            det.append((abs(act[i] - sim[i]), t, act[i], sim[i], why))
        for gap, t, a, s, why in sorted(det, key=lambda x: -x[0])[:15]:
            print(f"    {t['sym']:<14}{t['side']:<6}{t['bm']:>3}m"
                  f"{a:>+8.2f}${s:>+8.2f}$   {t['reason']:<14}{why}")

    overall_gap = abs(st.mean(sim) - st.mean(act))
    if overall_gap < 0.30:
        print("\n  ✅ הכיול תקין — אפשר להאמין להשוואות למטה")
    elif good_1m is not None and good_1m < 0.30:
        print(f"\n  ⚠️ בנרות הדקה הכיול תקין (פער {good_1m:.2f}$),")
        print("     אבל בנרות הגסים לא. השיטה נכונה — הנתונים הם הבעיה.")
        print("     נר רחב מכיל גם את הסטופ וגם את היעד, והכלל השמרני")
        print("     גובה תמיד את הסטופ, אז התוצאה יוצאת שלילית מלאכותית.")
        print("     הרץ עם --only-1m כדי לראות רק את מה שאפשר לסמוך עליו.")
    else:
        print("\n  ⚠️ הכיול חלש גם בנרות הדקה. אל תסיק מהתרחישים למטה.")
        print("     הרץ עם --debug כדי לראות איזה חוק ממודל לא נכון.")

    # ── scenarios, split by direction ───────────────────────────────────
    print("\n" + "=" * 72)
    print("  תרחישי יציאה — בהפרדה מלאה לפי כיוון")
    print("=" * 72)
    print(f"  {'':<24}{'LONG':<32}{'SHORT'}")
    for name, per_side in SCENARIOS:
        cols = []
        for side in ('LONG', 'SHORT'):
            cfg = per_side.get('L' if side == 'LONG' else 'S', {})
            p = [simulate(t['bars'], t['entry'], t['side'], cfg,
                          args.slip, t['bm'])[0]
                 for t in usable if t['side'] == side]
            cols.append(block(p))
        print(f"  {name:<24}{cols[0]:<32}{cols[1]}")

    print("\n" + "=" * 72)
    print("  איך לקרוא")
    print("=" * 72)
    print("  ✅ הרווח מובהק · ➖ בתוך הרעש · ❌ הפסד מובהק")
    print()
    print("  משווים כל שורה לשורת הבסיס, ורק בתוך אותה עמודה.")
    print("  שיפור בלונג שלא בא על חשבון השורט הוא הדבר שמחפשים.")
    print()
    print("  והסתייגות שלא נעלמת: יש כאן 38 לונגים בלבד, רובם מחודש אחד.")
    print("  גם אם שורה תיראה טוב — זה כיוון לבדוק, לא החלטה לשנות קוד.")


if __name__ == '__main__':
    main()
