---
name: Trading bot exit-check ordering
description: Why an uncapped-loss exit mechanism can override the intended stop-loss cap in the trading bot's per-trade monitoring loop.
---

The trade monitor loop evaluates exit conditions for each open trade in a fixed sequence per iteration (FastLoss → RSI ReversalGuard → MaxDuration → SmartTimeout → Trailing SL → TP1 → normal SL), using an early `continue` on the first condition that fires. Only the plain SL check enforces the configured loss cap; other exit paths close "at market" with no cap.

**Why:** A real incident (XPL/USDT SHORT, 2026-07-01) lost $19.25 against a configured 2% SL (~$10 cap). The position sat under the SL for ~2h45m, then in one 60s tick the price gapped past the SL threshold at the same moment the RSI-reversal condition also became true. Because ReversalGuard is checked earlier in the loop than the plain SL check, it fired first and closed the trade at the gapped price with no loss cap — the SL check never got a chance to run that iteration. FastLoss already had a guard for this (`_fl_sl_ok`: skip if price already crossed SL) but ReversalGuard did not.

**How to apply:** Any new or modified early-exit condition in this loop that isn't the canonical SL check should gate on "SL not yet crossed" (mirror the `_*_sl_ok` pattern) before allowing itself to fire — otherwise it can silently bypass the intended max-loss cap during fast/gappy price moves. Applies to `bot.py`'s per-trade monitoring loop (`track_trades`/exit-check block).
