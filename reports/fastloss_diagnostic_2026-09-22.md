# FastLoss Diagnostic Report — 2026-09-22

## Scope

This diagnostic covers the 18 LONG trades closed during the 24-hour report window from 2026-09-21 11:19:28 through 2026-09-22 11:19:28 Israel time. It also compares them with the earlier clean cohort beginning 2026-09-14. No scoring, threshold, or trading-logic changes were made.

## Executive findings

- The report window contains exactly 18 closed LONG trades, including 10 FastLoss exits.
- Twelve of today's 18 records (66.7%) contain EMA-bypass, volume, and FVG values recoverable from score_breakdown, compared with the previously reported 11/25 (44%).
- The structured logging fix was not active for the runtime that opened these trades: all 18 records still have null structured EMA/FVG fields, and all six Breakout/Major Watch records remain incomplete.
- The high-volume signature remains striking: every observed trade with EMA bypass + positive FVG + entry volume at least 3.7x ended in FastLoss.
- The result is strongly supportive but not yet conclusive because the specific signature has only five cases in the requested 43-trade reconstruction, or six in the actual continuous 57-trade cohort.
- The 10 FastLoss entries span roughly 13 hours. Seven occurred in three short bursts, but they were not one continuous time-localized cluster.
- Every FastLoss occurred in a BULLISH regime, mostly at FNG 70 and later at FNG 78, so the sample has little regime diversity.

## Logging completeness

### Today's 18 trades

- Fully parseable from score_breakdown: 12/18 (66.7%).
- Structured ema_bypass/fvg_score evaluation fields populated: 0/18.
- Breakout/Major Watch records with complete EMA/FVG checks: 0/6.
- Swing records with parseable EMA/FVG context: 12/12.

The increase from 44% to 66.7% is caused by today's strategy mix containing more Swing trades. It is not evidence that the Breakout/Major Watch logging fix was active.

## Cohort consistency warning

The requested arithmetic of 25 previous trades plus 18 new trades produces 43 trades, but it is not a continuous 2026-09-14-onward dataset.

- The reconstructed first 25 clean LONG closures end at 2026-09-20 17:28.
- The new report cohort begins at 2026-09-21 11:21.
- Fourteen additional LONG closures occurred between those cohorts.
- Requested comparison cohort: 43 trades.
- Actual continuous clean cohort through the report: 57 trades.

Both versions are reported below.

## Requested 43-trade comparison

| Group | Trades | Wins | Win rate | FastLoss | FastLoss rate | Net P&L |
|---|---:|---:|---:|---:|---:|---:|
| All trades | 43 | 14 | 32.6% | 25 | 58.1% | -$5.15 |
| Complete comparable records | 24 | 5 | 20.8% | 18 | 75.0% | -$26.19 |
| EMA bypass + FVG > 0 + volume >=3.7x | 5 | 0 | 0.0% | 5 | 100.0% | -$22.41 |
| Other complete signatures | 19 | 5 | 26.3% | 13 | 68.4% | -$3.78 |

The source-backed reconstruction contains 12 complete prior records rather than the previously reported 11. If the old 11/25 figure is held as authoritative, the combined comparable count is 23 rather than 24; the high-volume signature result remains unchanged.

## Actual continuous 57-trade comparison

| Group | Trades | Wins | Win rate | FastLoss | FastLoss rate | Net P&L |
|---|---:|---:|---:|---:|---:|---:|
| All continuous trades | 57 | 19 | 33.3% | 31 | 54.4% | +$10.62 |
| Complete comparable records | 32 | 7 | 21.9% | 23 | 71.9% | -$22.81 |
| EMA bypass + FVG > 0 + volume >=3.7x | 6 | 0 | 0.0% | 6 | 100.0% | -$25.16 |
| Other complete signatures | 26 | 7 | 26.9% | 17 | 65.4% | +$2.35 |

## Confidence and updated verdict

The continuous dataset now has 32 complete comparable records, which passes a general 30-record threshold. However, only six records exhibit the specific high-volume triple signature.

Updated verdict: strongly supportive but not confirmed.

The signature has produced six FastLoss exits in six observations and no wins. That is substantially worse than the already-high 65.4% FastLoss rate among other complete signatures. Nevertheless, six direct observations are too few for a confident causal conclusion, especially because all occurred in one broad bullish/greed environment.

## FastLoss time concentration

The ten FastLoss entries ran from 2026-09-21 15:27 through 2026-09-22 04:32.

Three short bursts account for seven trades:

1. 18:28–18:56 — SUI, MSTU, and ETH.
2. 21:29 — SYN and INTW opened three seconds apart.
3. 04:32 — WLD and EVAA opened fourteen seconds apart.

NIL, EGLD, and BTW were isolated entries. The cluster was therefore spread across approximately 13 hours but contained synchronized scanner-level bursts. Unlike a multi-regime sample, every FastLoss record was marked BULLISH.

# Detailed breakdown of all 18 trades

### 1. XRP — MaxDuration
- **Entry:** 2026-09-21T09:21:18+03:00 @ `1.4409` | **Exit:** 2026-09-21T11:21:54+03:00 @ `1.4451` | **Duration:** 120.6 min
- **Strategy/timeframe:** Breakout / Breakout | **RSI:** 68.6 | **Total score:** 95/100 | **Net P&L:** $+0.86
- **EMA bypass:** N/A (not evaluated) | **Volume:** 1.5x | **FVG score:** N/A
- **Full score_breakdown:** `Breakout Strategy LONG: 15m $1.4409 > 10H Resistance `$1.4392` | FNG=70 | RSI=68.6 | 15m Vol×1.5`

### 2. EPIC — TP1+Trail
- **Entry:** 2026-09-21T11:25:46+03:00 @ `0.5889` | **Exit:** 2026-09-21T11:58:31+03:00 @ `0.5942` | **Duration:** 32.8 min
- **Strategy/timeframe:** Swing / 15m | **RSI:** 52.7 | **Total score:** 87/100 | **Net P&L:** $+6.90
- **EMA bypass:** YES | **Volume:** 1.8x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×1.8≥1.5) | Trend=25/25 | MACD=10/15 | RSI=5/10(=53) | BB=12/20 | Vol=20/30(×1.8) | Candles=0/0(filtered) | Flag=0(no:flag length 15 not in 4–10 ran) | FVG=+10(FVG(LONG) gap=1.68% [0.584–0.5938] mid=0.5889 IN_ZONE) | OB=0(OB(LONG) zone=[0.5371–0.5822] AWAY() | Squeeze=0(no squeeze (width=0.55× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 31 i) | PumpSurge=0(vol×1.8) | FNG=70(+5) | LateEntry=0  →  TOTAL=87/100`

### 3. SEI — TP
- **Entry:** 2026-09-21T10:25:20+03:00 @ `0.05632` | **Exit:** 2026-09-21T12:30:58+03:00 @ `0.05858` | **Duration:** 125.6 min
- **Strategy/timeframe:** Swing / 4H | **RSI:** 81.5 | **Total score:** 90/100 | **Net P&L:** $+10.79
- **EMA bypass:** YES | **Volume:** 2.3x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.3≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=81⚠️) | BB=12/20 | Vol=30/30(×2.3) | Candles=0/0(filtered) | Flag=0(no:flag length 13 not in 4–10 ran) | FVG=+10(FVG(LONG) gap=0.36% [0.05553–0.05573] mid=0.05563 NEAR) | OB=+3(OB(LONG) [0.05435–0.05594] gap=2.93% mid=0.05514 NEAR) | Squeeze=0(no squeeze (width=1.57× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 31 i) | PumpSurge=0(vol×2.3) | FNG=70(+5) | LateEntry=-10(RSI=81)  →  TOTAL=90/100`

### 4. DOGE — TP
- **Entry:** 2026-09-21T11:04:55+03:00 @ `0.0902` | **Exit:** 2026-09-21T12:36:06+03:00 @ `0.09398` | **Duration:** 91.2 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 75.6 | **Total score:** 88/100 | **Net P&L:** $+11.02
- **EMA bypass:** N/A (not evaluated) | **Volume:** N/A | **FVG score:** N/A
- **Full score_breakdown:** `Major Watch Breakout — 1H > 4H High ($0.08981)`

### 5. ATOM — MaxDuration
- **Entry:** 2026-09-21T12:24:17+03:00 @ `1.7879` | **Exit:** 2026-09-21T14:25:04+03:00 @ `1.795` | **Duration:** 120.8 min
- **Strategy/timeframe:** Breakout / Breakout | **RSI:** 74.7 | **Total score:** 95/100 | **Net P&L:** $+1.39
- **EMA bypass:** N/A (not evaluated) | **Volume:** 1.5x | **FVG score:** N/A
- **Full score_breakdown:** `Breakout Strategy LONG: 15m $1.7879 > 10H Resistance `$1.7832` | FNG=70 | RSI=74.7 | 15m Vol×1.5`

### 6. NIL — FastLoss
- **Entry:** 2026-09-21T15:27:12+03:00 @ `0.06556` | **Exit:** 2026-09-21T15:33:09+03:00 @ `0.06503` | **Duration:** 6.0 min
- **Strategy/timeframe:** Swing / 4H | **RSI:** 77.99 | **Total score:** 87/100 | **Net P&L:** $-4.64
- **EMA bypass:** YES | **Volume:** 2.1x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.1≥1.5) | Trend=25/25 | MACD=10/15 | RSI=0/10(=78) | BB=12/20 | Vol=30/30(×2.1) | Candles=0/0(filtered) | Flag=0(no:flag too wide (0.00802 > 60% o) | FVG=+10(FVG(LONG) gap=2.97% [0.06457–0.06649] mid=0.06553 IN_ZONE) | OB=0(OB(LONG) zone=[0.06165–0.06433] AWA) | Squeeze=0(no squeeze (width=0.61× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 32 i) | PumpSurge=0(vol×2.1) | FNG=70(+5) | LateEntry=-5(RSI=78)  →  TOTAL=87/100`

### 7. ADA — TP1+Trail
- **Entry:** 2026-09-21T12:31:03+03:00 @ `0.2395` | **Exit:** 2026-09-21T16:15:48+03:00 @ `0.2448` | **Duration:** 224.8 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 70.7 | **Total score:** 88/100 | **Net P&L:** $+8.55
- **EMA bypass:** N/A (not evaluated) | **Volume:** N/A | **FVG score:** N/A
- **Full score_breakdown:** `Major Watch Breakout — 1H > 4H High ($0.238)`

### 8. BNB — MaxDuration
- **Entry:** 2026-09-21T14:17:26+03:00 @ `787.9` | **Exit:** 2026-09-21T17:05:38+03:00 @ `793.76` | **Duration:** 168.2 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 58.3 | **Total score:** 88/100 | **Net P&L:** $+3.12
- **EMA bypass:** N/A (not evaluated) | **Volume:** N/A | **FVG score:** N/A
- **Full score_breakdown:** `Major Watch Breakout — 1H > 4H High ($786.53)`

### 9. EGLD — FastLoss
- **Entry:** 2026-09-21T17:28:03+03:00 @ `4.234` | **Exit:** 2026-09-21T17:40:52+03:00 @ `4.212` | **Duration:** 12.8 min
- **Strategy/timeframe:** Swing / 4H | **RSI:** 59.69 | **Total score:** 92/100 | **Net P&L:** $-3.20
- **EMA bypass:** YES | **Volume:** 1.5x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×1.5≥1.5) | Trend=25/25 | MACD=15/15 | RSI=5/10(=60) | BB=12/20 | Vol=20/30(×1.5) | Candles=0/0(filtered) | Flag=0(no:flag too wide (0.484 > 60% of ) | FVG=+10(FVG(LONG) gap=0.17% [4.225–4.232] mid=4.229 NEAR) | OB=0(OB(LONG) zone=[3.899–4.183] AWAY(4.) | Squeeze=0(no squeeze (width=1.26× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 30 i) | PumpSurge=0(vol×1.5) | FNG=70(+5) | LateEntry=0  →  TOTAL=92/100`

### 10. SUI — FastLoss
- **Entry:** 2026-09-21T18:28:28+03:00 @ `1.0101` | **Exit:** 2026-09-21T18:28:55+03:00 @ `1.0061` | **Duration:** 0.5 min
- **Strategy/timeframe:** Swing / 4H | **RSI:** 79.48 | **Total score:** 90/100 | **Net P&L:** $-2.58
- **EMA bypass:** YES | **Volume:** 2.8x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.8≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=79⚠️) | BB=12/20 | Vol=30/30(×2.8) | Candles=0/0(filtered) | Flag=0(no:flag length 21 not in 4–10 ran) | FVG=+10(FVG(LONG) gap=0.84% [1.005–1.014] mid=1.009 IN_ZONE) | OB=+3(OB(LONG) [0.9794–1.022] gap=4.31% mid=1.001 IN_ZONE) | Squeeze=0(no squeeze (width=1.12× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 31 i) | PumpSurge=0(vol×2.8) | FNG=70(+5) | LateEntry=-10(RSI=79)  →  TOTAL=90/100`

### 11. MSTU — FastLoss
- **Entry:** 2026-09-21T18:28:31+03:00 @ `47.4644` | **Exit:** 2026-09-21T18:29:58+03:00 @ `47.1796` | **Duration:** 1.4 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 72.66 | **Total score:** 87/100 | **Net P&L:** $-3.60
- **EMA bypass:** YES | **Volume:** 2.4x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.4≥1.5) | Trend=25/25 | MACD=10/15 | RSI=0/10(=73) | BB=12/20 | Vol=30/30(×2.4) | Candles=0/0(filtered) | Flag=0(no:no pole found) | FVG=+10(FVG(LONG) gap=0.12% [46.91–46.97] mid=46.94 NEAR) | OB=0(OB(LONG) zone=[45.5–46.08] AWAY(3.5) | Squeeze=0(no squeeze (width=0.68× avg)) | VolBuild=0(no buildup (streak=1)) | RSIDiv=0(RSI div error: index 30 i) | PumpSurge=0(vol×2.4) | FNG=70(+5) | LateEntry=-5(RSI=73)  →  TOTAL=87/100`

### 12. ETH — FastLoss
- **Entry:** 2026-09-21T18:56:56+03:00 @ `2760.0` | **Exit:** 2026-09-21T19:04:08+03:00 @ `2746.65` | **Duration:** 7.2 min
- **Strategy/timeframe:** Breakout / Breakout | **RSI:** 68.9 | **Total score:** 95/100 | **Net P&L:** $-3.02
- **EMA bypass:** N/A (not evaluated) | **Volume:** 2.2x | **FVG score:** N/A
- **Full score_breakdown:** `Breakout Strategy LONG: 15m $2760 > 10H Resistance `$2757.24` | FNG=70 | RSI=68.9 | 15m Vol×2.2`

### 13. SYN — FastLoss
- **Entry:** 2026-09-21T21:29:42+03:00 @ `0.25403` | **Exit:** 2026-09-21T21:30:10+03:00 @ `0.25297` | **Duration:** 0.5 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 69.59 | **Total score:** 87/100 | **Net P&L:** $-2.69
- **EMA bypass:** YES | **Volume:** 2.4x | **FVG score:** +0
- **Full score_breakdown:** `EMA_bypass(vol×2.4≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=70) | BB=12/20 | Vol=30/30(×2.4) | Candles=0/0(filtered) | Flag=0(no:no pole found) | FVG=0(FVG(LONG) gap=0.3% [0.2328–0.2334] ) | OB=0(OB(LONG) zone=[0.2346–0.2392] AWAY() | Squeeze=0(no squeeze (width=2.51× avg)) | VolBuild=0(no buildup (streak=1)) | RSIDiv=0(RSI div error: index 31 i) | PumpSurge=0(vol×2.4) | FNG=70(+5) | LateEntry=0  →  TOTAL=87/100`

### 14. INTW — FastLoss
- **Entry:** 2026-09-21T21:29:45+03:00 @ `32.437` | **Exit:** 2026-09-21T21:33:16+03:00 @ `32.243` | **Duration:** 3.5 min
- **Strategy/timeframe:** Swing / 4H | **RSI:** 76.81 | **Total score:** 92/100 | **Net P&L:** $-3.59
- **EMA bypass:** YES | **Volume:** 3.1x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×3.1≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=77) | BB=12/20 | Vol=30/30(×3.1) | Candles=0/0(filtered) | Flag=0(no:flag length 17 not in 4–10 ran) | FVG=+10(FVG(LONG) gap=4.33% [31.41–32.77] mid=32.09 IN_ZONE) | OB=0(OB(LONG) zone=[28.8–29.25] AWAY(10.) | Squeeze=0(no squeeze (width=2.20× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 30 i) | PumpSurge=0(vol×3.1) | FNG=70(+5) | LateEntry=-5(RSI=77)  →  TOTAL=92/100`

### 15. BTW — FastLoss
- **Entry:** 2026-09-22T00:31:00+03:00 @ `0.903121` | **Exit:** 2026-09-22T00:32:20+03:00 @ `0.896014` | **Duration:** 1.3 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 58.52 | **Total score:** 90/100 | **Net P&L:** $-4.53
- **EMA bypass:** YES | **Volume:** 7.9x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×7.9≥1.5) | Trend=25/25 | MACD=0/15 | RSI=5/10(=59) | BB=12/20 | Vol=30/30(×7.9) | Candles=0/0(filtered) | Flag=0(no:no pole found) | FVG=+10(FVG(LONG) gap=0.41% [0.9132–0.917] mid=0.9151 NEAR) | OB=+3(OB(LONG) [0.8343–0.905] gap=8.47% mid=0.8697 IN_ZONE) | Squeeze=0(no squeeze (width=1.70× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 41 i) | PumpSurge=0(vol×7.9) | FNG=70(+5) | LateEntry=0  →  TOTAL=90/100`

### 16. WLD — FastLoss
- **Entry:** 2026-09-22T04:32:41+03:00 @ `0.4709` | **Exit:** 2026-09-22T04:34:18+03:00 @ `0.4691` | **Duration:** 1.6 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 63.63 | **Total score:** 97/100 | **Net P&L:** $-2.51
- **EMA bypass:** YES | **Volume:** 2.4x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.4≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=64) | BB=12/20 | Vol=30/30(×2.4) | Candles=0/0(filtered) | Flag=0(no:flag length 18 not in 4–10 ran) | FVG=+10(FVG(LONG) gap=1.15% [0.4601–0.4654] mid=0.4627 NEAR) | OB=0(OB(LONG) zone=[0.4567–0.4599] AWAY() | Squeeze=0(no squeeze (width=0.69× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 30 i) | PumpSurge=0(vol×2.4) | FNG=78(+5) | LateEntry=0  →  TOTAL=97/100`

### 17. EVAA — FastLoss
- **Entry:** 2026-09-22T04:32:27+03:00 @ `0.8251` | **Exit:** 2026-09-22T04:38:24+03:00 @ `0.8188` | **Duration:** 6.0 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 56.89 | **Total score:** 100/100 | **Net P&L:** $-4.42
- **EMA bypass:** YES | **Volume:** 2.2x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.2≥1.5) | Trend=25/25 | MACD=10/15 | RSI=5/10(=57) | BB=12/20 | Vol=30/30(×2.2) | Candles=0/0(filtered) | Flag=0(no:no pole found) | FVG=+10(FVG(LONG) gap=0.11% [0.8712–0.8722] mid=0.8717 NEAR) | OB=+3(OB(LONG) [0.8209–0.8333] gap=1.51% mid=0.8271 IN_ZONE) | Squeeze=0(no squeeze (width=1.13× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 42 i) | PumpSurge=0(vol×2.2) | FNG=78(+5) | LateEntry=0  →  TOTAL=100/100`

### 18. WIF — MaxDuration
- **Entry:** 2026-09-22T03:32:06+03:00 @ `0.2484` | **Exit:** 2026-09-22T06:20:57+03:00 @ `0.246` | **Duration:** 168.8 min
- **Strategy/timeframe:** Swing / 15m | **RSI:** 64.84 | **Total score:** 87/100 | **Net P&L:** $-5.43
- **EMA bypass:** YES | **Volume:** 4.1x | **FVG score:** +0
- **Full score_breakdown:** `EMA_bypass(vol×4.1≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=65) | BB=12/20 | Vol=30/30(×4.1) | Candles=0/0(filtered) | Flag=0(no:flag too wide (0.0248 > 60% of) | FVG=0(FVG(LONG) gap=1.26% [0.2387–0.2417]) | OB=0(OB(LONG) zone=[0.2008–0.2031] AWAY() | Squeeze=0(no squeeze (width=1.58× avg)) | VolBuild=0(no buildup (streak=1)) | RSIDiv=0(RSI div error: index 30 i) | PumpSurge=0(vol×4.1) | FNG=70(+5) | LateEntry=0  →  TOTAL=87/100`
# FastLoss Diagnostic Report — 2026-09-22

## Scope

This diagnostic covers the 18 LONG trades closed during the 24-hour report window from 2026-09-21 11:19:28 through 2026-09-22 11:19:28 Israel time. It also compares them with the earlier clean cohort beginning 2026-09-14. No scoring, threshold, or trading-logic changes were made.

## Executive findings

- The report window contains exactly 18 closed LONG trades, including 10 FastLoss exits.
- Twelve of today's 18 records (66.7%) contain EMA-bypass, volume, and FVG values recoverable from score_breakdown, compared with the previously reported 11/25 (44%).
- The structured logging fix was not active for the runtime that opened these trades: all 18 records still have null structured EMA/FVG fields, and all six Breakout/Major Watch records remain incomplete.
- The high-volume signature remains striking: every observed trade with EMA bypass + positive FVG + entry volume at least 3.7x ended in FastLoss.
- The result is strongly supportive but not yet conclusive because the specific signature has only five cases in the requested 43-trade reconstruction, or six in the actual continuous 57-trade cohort.
- The 10 FastLoss entries span roughly 13 hours. Seven occurred in three short bursts, but they were not one continuous time-localized cluster.
- Every FastLoss occurred in a BULLISH regime, mostly at FNG 70 and later at FNG 78, so the sample has little regime diversity.

## Logging completeness

### Today's 18 trades

- Fully parseable from score_breakdown: 12/18 (66.7%).
- Structured ema_bypass/fvg_score evaluation fields populated: 0/18.
- Breakout/Major Watch records with complete EMA/FVG checks: 0/6.
- Swing records with parseable EMA/FVG context: 12/12.

The increase from 44% to 66.7% is caused by today's strategy mix containing more Swing trades. It is not evidence that the Breakout/Major Watch logging fix was active.

## Cohort consistency warning

The requested arithmetic of 25 previous trades plus 18 new trades produces 43 trades, but it is not a continuous 2026-09-14-onward dataset.

- The reconstructed first 25 clean LONG closures end at 2026-09-20 17:28.
- The new report cohort begins at 2026-09-21 11:21.
- Fourteen additional LONG closures occurred between those cohorts.
- Requested comparison cohort: 43 trades.
- Actual continuous clean cohort through the report: 57 trades.

Both versions are reported below.

## Requested 43-trade comparison

| Group | Trades | Wins | Win rate | FastLoss | FastLoss rate | Net P&L |
|---|---:|---:|---:|---:|---:|---:|
| All trades | 43 | 14 | 32.6% | 25 | 58.1% | -$5.15 |
| Complete comparable records | 24 | 5 | 20.8% | 18 | 75.0% | -$26.19 |
| EMA bypass + FVG > 0 + volume >=3.7x | 5 | 0 | 0.0% | 5 | 100.0% | -$22.41 |
| Other complete signatures | 19 | 5 | 26.3% | 13 | 68.4% | -$3.78 |

The source-backed reconstruction contains 12 complete prior records rather than the previously reported 11. If the old 11/25 figure is held as authoritative, the combined comparable count is 23 rather than 24; the high-volume signature result remains unchanged.

## Actual continuous 57-trade comparison

| Group | Trades | Wins | Win rate | FastLoss | FastLoss rate | Net P&L |
|---|---:|---:|---:|---:|---:|---:|
| All continuous trades | 57 | 19 | 33.3% | 31 | 54.4% | +$10.62 |
| Complete comparable records | 32 | 7 | 21.9% | 23 | 71.9% | -$22.81 |
| EMA bypass + FVG > 0 + volume >=3.7x | 6 | 0 | 0.0% | 6 | 100.0% | -$25.16 |
| Other complete signatures | 26 | 7 | 26.9% | 17 | 65.4% | +$2.35 |

## Confidence and updated verdict

The continuous dataset now has 32 complete comparable records, which passes a general 30-record threshold. However, only six records exhibit the specific high-volume triple signature.

Updated verdict: strongly supportive but not confirmed.

The signature has produced six FastLoss exits in six observations and no wins. That is substantially worse than the already-high 65.4% FastLoss rate among other complete signatures. Nevertheless, six direct observations are too few for a confident causal conclusion, especially because all occurred in one broad bullish/greed environment.

## FastLoss time concentration

The ten FastLoss entries ran from 2026-09-21 15:27 through 2026-09-22 04:32.

Three short bursts account for seven trades:

1. 18:28–18:56 — SUI, MSTU, and ETH.
2. 21:29 — SYN and INTW opened three seconds apart.
3. 04:32 — WLD and EVAA opened fourteen seconds apart.

NIL, EGLD, and BTW were isolated entries. The cluster was therefore spread across approximately 13 hours but contained synchronized scanner-level bursts. Unlike a multi-regime sample, every FastLoss record was marked BULLISH.

# Detailed breakdown of all 18 trades

### 1. XRP — MaxDuration
- **Entry:** 2026-09-21T09:21:18+03:00 @ `1.4409` | **Exit:** 2026-09-21T11:21:54+03:00 @ `1.4451` | **Duration:** 120.6 min
- **Strategy/timeframe:** Breakout / Breakout | **RSI:** 68.6 | **Total score:** 95/100 | **Net P&L:** $+0.86
- **EMA bypass:** N/A (not evaluated) | **Volume:** 1.5x | **FVG score:** N/A
- **Full score_breakdown:** `Breakout Strategy LONG: 15m $1.4409 > 10H Resistance `$1.4392` | FNG=70 | RSI=68.6 | 15m Vol×1.5`

### 2. EPIC — TP1+Trail
- **Entry:** 2026-09-21T11:25:46+03:00 @ `0.5889` | **Exit:** 2026-09-21T11:58:31+03:00 @ `0.5942` | **Duration:** 32.8 min
- **Strategy/timeframe:** Swing / 15m | **RSI:** 52.7 | **Total score:** 87/100 | **Net P&L:** $+6.90
- **EMA bypass:** YES | **Volume:** 1.8x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×1.8≥1.5) | Trend=25/25 | MACD=10/15 | RSI=5/10(=53) | BB=12/20 | Vol=20/30(×1.8) | Candles=0/0(filtered) | Flag=0(no:flag length 15 not in 4–10 ran) | FVG=+10(FVG(LONG) gap=1.68% [0.584–0.5938] mid=0.5889 IN_ZONE) | OB=0(OB(LONG) zone=[0.5371–0.5822] AWAY() | Squeeze=0(no squeeze (width=0.55× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 31 i) | PumpSurge=0(vol×1.8) | FNG=70(+5) | LateEntry=0  →  TOTAL=87/100`

### 3. SEI — TP
- **Entry:** 2026-09-21T10:25:20+03:00 @ `0.05632` | **Exit:** 2026-09-21T12:30:58+03:00 @ `0.05858` | **Duration:** 125.6 min
- **Strategy/timeframe:** Swing / 4H | **RSI:** 81.5 | **Total score:** 90/100 | **Net P&L:** $+10.79
- **EMA bypass:** YES | **Volume:** 2.3x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.3≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=81⚠️) | BB=12/20 | Vol=30/30(×2.3) | Candles=0/0(filtered) | Flag=0(no:flag length 13 not in 4–10 ran) | FVG=+10(FVG(LONG) gap=0.36% [0.05553–0.05573] mid=0.05563 NEAR) | OB=+3(OB(LONG) [0.05435–0.05594] gap=2.93% mid=0.05514 NEAR) | Squeeze=0(no squeeze (width=1.57× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 31 i) | PumpSurge=0(vol×2.3) | FNG=70(+5) | LateEntry=-10(RSI=81)  →  TOTAL=90/100`

### 4. DOGE — TP
- **Entry:** 2026-09-21T11:04:55+03:00 @ `0.0902` | **Exit:** 2026-09-21T12:36:06+03:00 @ `0.09398` | **Duration:** 91.2 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 75.6 | **Total score:** 88/100 | **Net P&L:** $+11.02
- **EMA bypass:** N/A (not evaluated) | **Volume:** N/A | **FVG score:** N/A
- **Full score_breakdown:** `Major Watch Breakout — 1H > 4H High ($0.08981)`

### 5. ATOM — MaxDuration
- **Entry:** 2026-09-21T12:24:17+03:00 @ `1.7879` | **Exit:** 2026-09-21T14:25:04+03:00 @ `1.795` | **Duration:** 120.8 min
- **Strategy/timeframe:** Breakout / Breakout | **RSI:** 74.7 | **Total score:** 95/100 | **Net P&L:** $+1.39
- **EMA bypass:** N/A (not evaluated) | **Volume:** 1.5x | **FVG score:** N/A
- **Full score_breakdown:** `Breakout Strategy LONG: 15m $1.7879 > 10H Resistance `$1.7832` | FNG=70 | RSI=74.7 | 15m Vol×1.5`

### 6. NIL — FastLoss
- **Entry:** 2026-09-21T15:27:12+03:00 @ `0.06556` | **Exit:** 2026-09-21T15:33:09+03:00 @ `0.06503` | **Duration:** 6.0 min
- **Strategy/timeframe:** Swing / 4H | **RSI:** 77.99 | **Total score:** 87/100 | **Net P&L:** $-4.64
- **EMA bypass:** YES | **Volume:** 2.1x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.1≥1.5) | Trend=25/25 | MACD=10/15 | RSI=0/10(=78) | BB=12/20 | Vol=30/30(×2.1) | Candles=0/0(filtered) | Flag=0(no:flag too wide (0.00802 > 60% o) | FVG=+10(FVG(LONG) gap=2.97% [0.06457–0.06649] mid=0.06553 IN_ZONE) | OB=0(OB(LONG) zone=[0.06165–0.06433] AWA) | Squeeze=0(no squeeze (width=0.61× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 32 i) | PumpSurge=0(vol×2.1) | FNG=70(+5) | LateEntry=-5(RSI=78)  →  TOTAL=87/100`

### 7. ADA — TP1+Trail
- **Entry:** 2026-09-21T12:31:03+03:00 @ `0.2395` | **Exit:** 2026-09-21T16:15:48+03:00 @ `0.2448` | **Duration:** 224.8 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 70.7 | **Total score:** 88/100 | **Net P&L:** $+8.55
- **EMA bypass:** N/A (not evaluated) | **Volume:** N/A | **FVG score:** N/A
- **Full score_breakdown:** `Major Watch Breakout — 1H > 4H High ($0.238)`

### 8. BNB — MaxDuration
- **Entry:** 2026-09-21T14:17:26+03:00 @ `787.9` | **Exit:** 2026-09-21T17:05:38+03:00 @ `793.76` | **Duration:** 168.2 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 58.3 | **Total score:** 88/100 | **Net P&L:** $+3.12
- **EMA bypass:** N/A (not evaluated) | **Volume:** N/A | **FVG score:** N/A
- **Full score_breakdown:** `Major Watch Breakout — 1H > 4H High ($786.53)`

### 9. EGLD — FastLoss
- **Entry:** 2026-09-21T17:28:03+03:00 @ `4.234` | **Exit:** 2026-09-21T17:40:52+03:00 @ `4.212` | **Duration:** 12.8 min
- **Strategy/timeframe:** Swing / 4H | **RSI:** 59.69 | **Total score:** 92/100 | **Net P&L:** $-3.20
- **EMA bypass:** YES | **Volume:** 1.5x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×1.5≥1.5) | Trend=25/25 | MACD=15/15 | RSI=5/10(=60) | BB=12/20 | Vol=20/30(×1.5) | Candles=0/0(filtered) | Flag=0(no:flag too wide (0.484 > 60% of ) | FVG=+10(FVG(LONG) gap=0.17% [4.225–4.232] mid=4.229 NEAR) | OB=0(OB(LONG) zone=[3.899–4.183] AWAY(4.) | Squeeze=0(no squeeze (width=1.26× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 30 i) | PumpSurge=0(vol×1.5) | FNG=70(+5) | LateEntry=0  →  TOTAL=92/100`

### 10. SUI — FastLoss
- **Entry:** 2026-09-21T18:28:28+03:00 @ `1.0101` | **Exit:** 2026-09-21T18:28:55+03:00 @ `1.0061` | **Duration:** 0.5 min
- **Strategy/timeframe:** Swing / 4H | **RSI:** 79.48 | **Total score:** 90/100 | **Net P&L:** $-2.58
- **EMA bypass:** YES | **Volume:** 2.8x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.8≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=79⚠️) | BB=12/20 | Vol=30/30(×2.8) | Candles=0/0(filtered) | Flag=0(no:flag length 21 not in 4–10 ran) | FVG=+10(FVG(LONG) gap=0.84% [1.005–1.014] mid=1.009 IN_ZONE) | OB=+3(OB(LONG) [0.9794–1.022] gap=4.31% mid=1.001 IN_ZONE) | Squeeze=0(no squeeze (width=1.12× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 31 i) | PumpSurge=0(vol×2.8) | FNG=70(+5) | LateEntry=-10(RSI=79)  →  TOTAL=90/100`

### 11. MSTU — FastLoss
- **Entry:** 2026-09-21T18:28:31+03:00 @ `47.4644` | **Exit:** 2026-09-21T18:29:58+03:00 @ `47.1796` | **Duration:** 1.4 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 72.66 | **Total score:** 87/100 | **Net P&L:** $-3.60
- **EMA bypass:** YES | **Volume:** 2.4x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.4≥1.5) | Trend=25/25 | MACD=10/15 | RSI=0/10(=73) | BB=12/20 | Vol=30/30(×2.4) | Candles=0/0(filtered) | Flag=0(no:no pole found) | FVG=+10(FVG(LONG) gap=0.12% [46.91–46.97] mid=46.94 NEAR) | OB=0(OB(LONG) zone=[45.5–46.08] AWAY(3.5) | Squeeze=0(no squeeze (width=0.68× avg)) | VolBuild=0(no buildup (streak=1)) | RSIDiv=0(RSI div error: index 30 i) | PumpSurge=0(vol×2.4) | FNG=70(+5) | LateEntry=-5(RSI=73)  →  TOTAL=87/100`

### 12. ETH — FastLoss
- **Entry:** 2026-09-21T18:56:56+03:00 @ `2760.0` | **Exit:** 2026-09-21T19:04:08+03:00 @ `2746.65` | **Duration:** 7.2 min
- **Strategy/timeframe:** Breakout / Breakout | **RSI:** 68.9 | **Total score:** 95/100 | **Net P&L:** $-3.02
- **EMA bypass:** N/A (not evaluated) | **Volume:** 2.2x | **FVG score:** N/A
- **Full score_breakdown:** `Breakout Strategy LONG: 15m $2760 > 10H Resistance `$2757.24` | FNG=70 | RSI=68.9 | 15m Vol×2.2`

### 13. SYN — FastLoss
- **Entry:** 2026-09-21T21:29:42+03:00 @ `0.25403` | **Exit:** 2026-09-21T21:30:10+03:00 @ `0.25297` | **Duration:** 0.5 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 69.59 | **Total score:** 87/100 | **Net P&L:** $-2.69
- **EMA bypass:** YES | **Volume:** 2.4x | **FVG score:** +0
- **Full score_breakdown:** `EMA_bypass(vol×2.4≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=70) | BB=12/20 | Vol=30/30(×2.4) | Candles=0/0(filtered) | Flag=0(no:no pole found) | FVG=0(FVG(LONG) gap=0.3% [0.2328–0.2334] ) | OB=0(OB(LONG) zone=[0.2346–0.2392] AWAY() | Squeeze=0(no squeeze (width=2.51× avg)) | VolBuild=0(no buildup (streak=1)) | RSIDiv=0(RSI div error: index 31 i) | PumpSurge=0(vol×2.4) | FNG=70(+5) | LateEntry=0  →  TOTAL=87/100`

### 14. INTW — FastLoss
- **Entry:** 2026-09-21T21:29:45+03:00 @ `32.437` | **Exit:** 2026-09-21T21:33:16+03:00 @ `32.243` | **Duration:** 3.5 min
- **Strategy/timeframe:** Swing / 4H | **RSI:** 76.81 | **Total score:** 92/100 | **Net P&L:** $-3.59
- **EMA bypass:** YES | **Volume:** 3.1x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×3.1≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=77) | BB=12/20 | Vol=30/30(×3.1) | Candles=0/0(filtered) | Flag=0(no:flag length 17 not in 4–10 ran) | FVG=+10(FVG(LONG) gap=4.33% [31.41–32.77] mid=32.09 IN_ZONE) | OB=0(OB(LONG) zone=[28.8–29.25] AWAY(10.) | Squeeze=0(no squeeze (width=2.20× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 30 i) | PumpSurge=0(vol×3.1) | FNG=70(+5) | LateEntry=-5(RSI=77)  →  TOTAL=92/100`

### 15. BTW — FastLoss
- **Entry:** 2026-09-22T00:31:00+03:00 @ `0.903121` | **Exit:** 2026-09-22T00:32:20+03:00 @ `0.896014` | **Duration:** 1.3 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 58.52 | **Total score:** 90/100 | **Net P&L:** $-4.53
- **EMA bypass:** YES | **Volume:** 7.9x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×7.9≥1.5) | Trend=25/25 | MACD=0/15 | RSI=5/10(=59) | BB=12/20 | Vol=30/30(×7.9) | Candles=0/0(filtered) | Flag=0(no:no pole found) | FVG=+10(FVG(LONG) gap=0.41% [0.9132–0.917] mid=0.9151 NEAR) | OB=+3(OB(LONG) [0.8343–0.905] gap=8.47% mid=0.8697 IN_ZONE) | Squeeze=0(no squeeze (width=1.70× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 41 i) | PumpSurge=0(vol×7.9) | FNG=70(+5) | LateEntry=0  →  TOTAL=90/100`

### 16. WLD — FastLoss
- **Entry:** 2026-09-22T04:32:41+03:00 @ `0.4709` | **Exit:** 2026-09-22T04:34:18+03:00 @ `0.4691` | **Duration:** 1.6 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 63.63 | **Total score:** 97/100 | **Net P&L:** $-2.51
- **EMA bypass:** YES | **Volume:** 2.4x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.4≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=64) | BB=12/20 | Vol=30/30(×2.4) | Candles=0/0(filtered) | Flag=0(no:flag length 18 not in 4–10 ran) | FVG=+10(FVG(LONG) gap=1.15% [0.4601–0.4654] mid=0.4627 NEAR) | OB=0(OB(LONG) zone=[0.4567–0.4599] AWAY() | Squeeze=0(no squeeze (width=0.69× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 30 i) | PumpSurge=0(vol×2.4) | FNG=78(+5) | LateEntry=0  →  TOTAL=97/100`

### 17. EVAA — FastLoss
- **Entry:** 2026-09-22T04:32:27+03:00 @ `0.8251` | **Exit:** 2026-09-22T04:38:24+03:00 @ `0.8188` | **Duration:** 6.0 min
- **Strategy/timeframe:** Swing / 1H | **RSI:** 56.89 | **Total score:** 100/100 | **Net P&L:** $-4.42
- **EMA bypass:** YES | **Volume:** 2.2x | **FVG score:** +10
- **Full score_breakdown:** `EMA_bypass(vol×2.2≥1.5) | Trend=25/25 | MACD=10/15 | RSI=5/10(=57) | BB=12/20 | Vol=30/30(×2.2) | Candles=0/0(filtered) | Flag=0(no:no pole found) | FVG=+10(FVG(LONG) gap=0.11% [0.8712–0.8722] mid=0.8717 NEAR) | OB=+3(OB(LONG) [0.8209–0.8333] gap=1.51% mid=0.8271 IN_ZONE) | Squeeze=0(no squeeze (width=1.13× avg)) | VolBuild=0(no buildup (streak=0)) | RSIDiv=0(RSI div error: index 42 i) | PumpSurge=0(vol×2.2) | FNG=78(+5) | LateEntry=0  →  TOTAL=100/100`

### 18. WIF — MaxDuration
- **Entry:** 2026-09-22T03:32:06+03:00 @ `0.2484` | **Exit:** 2026-09-22T06:20:57+03:00 @ `0.246` | **Duration:** 168.8 min
- **Strategy/timeframe:** Swing / 15m | **RSI:** 64.84 | **Total score:** 87/100 | **Net P&L:** $-5.43
- **EMA bypass:** YES | **Volume:** 4.1x | **FVG score:** +0
- **Full score_breakdown:** `EMA_bypass(vol×4.1≥1.5) | Trend=25/25 | MACD=15/15 | RSI=0/10(=65) | BB=12/20 | Vol=30/30(×4.1) | Candles=0/0(filtered) | Flag=0(no:flag too wide (0.0248 > 60% of) | FVG=0(FVG(LONG) gap=1.26% [0.2387–0.2417]) | OB=0(OB(LONG) zone=[0.2008–0.2031] AWAY() | Squeeze=0(no squeeze (width=1.58× avg)) | VolBuild=0(no buildup (streak=1)) | RSIDiv=0(RSI div error: index 30 i) | PumpSurge=0(vol×4.1) | FNG=70(+5) | LateEntry=0  →  TOTAL=87/100`
