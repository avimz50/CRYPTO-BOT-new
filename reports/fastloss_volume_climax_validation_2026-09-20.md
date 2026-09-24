# FastLoss volume-climax validation

**Audit window:** closed LONG trades opened on or after 2026-09-14  
**Source snapshot:** `trade_audit.json`, updated 2026-09-20 14:57:43 +03:00  
**Method:** `reports/validate_fastloss_volume_pattern.py`

## Conclusion

The extreme-volume exhaustion hypothesis is **unresolved, with a strong warning signal in a very small sample**.

- The strict comparable cohort contains **11 trades**, below the 30-trade minimum for a confident recommendation.
- All **4/4** entries combining EMA bypass with a positive FVG ended in FastLoss (0% win rate, 100% FastLoss rate, average net P&L **-$4.47**).
- EMA-bypass-only entries performed materially better in this sample: **3/5 wins**, 40% FastLoss rate, average net P&L **+$3.90**.
- The combined signature is therefore consistent with the proposed same-candle climax/exhaustion mechanism, but four observations cannot distinguish a durable effect from clustering or chance.
- No scoring, threshold, veto, or exit change is justified from this sample alone. Collect at least **19 more fully instrumented comparable trades** to reach 30, then repeat the analysis.

## Cohort and exclusions

The rolling audit contained 100 records. Of these, **25** were closed LONG trades opened in the clean post-fix window. The validation-trial file contains the same 25 records for this window, so records were deduplicated by using the audit file as the canonical source.

To qualify, a trade needed:

1. an entry volume multiplier;
2. an observable EMA-bypass status;
3. a numeric FVG score;
4. positive ATR; and
5. open and close timestamps.

Only the standard score breakdown exposes both EMA-bypass status and FVG score. Breakout and Major Watch breakdowns do not, so absence of an EMA-bypass/FVG tag in those formats was treated as **unknown**, not false.

| Data check | Available | Missing/excluded |
|---|---:|---:|
| Post-fix closed LONG trades | 25 | — |
| Volume multiplier | 24 | 1 |
| EMA-bypass status | 11 | 14 |
| FVG score | 11 | 14 |
| Positive ATR | 25 | 0 |
| Open and close timestamps | 25 | 0 |
| **All required fields** | **11** | **14** |

The structured `volume_ratio` field was null on all 25 records. For 24 trades, the exact multiplier was recoverable from `score_breakdown` (`Vol=…(×N)`, `EMA_bypass(vol×N…)`, or `15m Vol×N`). One Major Watch record had no recoverable volume. This fallback is deterministic but confirms a data-quality gap in the structured audit fields.

The audit is capped at the latest 100 records. It cannot prove that every post-fix trade remains available once the cap rolls forward. At this snapshot, the earliest qualifying post-fix LONG opened on 2026-09-15 03:54 +03:00.

## Volume-bucket outcomes

Win means net P&L greater than zero. Average P&L is net of recorded fees.

| Entry volume | Count | Wins | Win rate | Avg net P&L | FastLoss count | FastLoss rate |
|---|---:|---:|---:|---:|---:|---:|
| <2x | 2 | 0 | 0.0% | -$2.95 | 2 | 100.0% |
| 2–3x | 3 | 2 | 66.7% | +$3.79 | 1 | 33.3% |
| 3–5x | 4 | 1 | 25.0% | +$0.08 | 3 | 75.0% |
| 5–10x | 1 | 0 | 0.0% | -$4.81 | 1 | 100.0% |
| >10x | 1 | 0 | 0.0% | -$5.25 | 1 | 100.0% |

The three trades at 5x or higher all FastLossed, but the <2x bucket also had a 100% FastLoss rate. Volume magnitude alone does not explain outcomes in this cohort. The 3–5x bucket is also mixed because a 3.2x EMA-bypass-only trade won.

## Combined entry signatures

These are mutually exclusive signatures from the same entry breakdown; they are not treated as independent score components.

| Entry signature | Count | Wins | Win rate | Avg net P&L | FastLoss count | FastLoss rate |
|---|---:|---:|---:|---:|---:|---:|
| EMA bypass + positive FVG | 4 | 0 | 0.0% | -$4.47 | 4 | 100.0% |
| EMA bypass only | 5 | 3 | 60.0% | +$3.90 | 2 | 40.0% |
| Positive FVG only | 1 | 0 | 0.0% | -$2.77 | 1 | 100.0% |
| Neither | 1 | 0 | 0.0% | -$3.13 | 1 | 100.0% |

The four combined-signature trades were CELR (4.4x), EVAA (3.7x), ZIL (7.3x), and ALLO (30.1x). Every one closed via FastLoss. By contrast, the EMA-bypass-only group included winners at 2.1x, 2.3x, and 3.2x, while its losses occurred at 2.3x and 3.1x.

This supports evaluating the **combined signature**, not interpreting volume, EMA bypass, Trend, MACD, and FVG as independent evidence when they may arise from the same candle.

## Immediate-reversal comparison

This comparison uses all 25 post-fix LONG trades because ATR, prices, durations, and timestamps are complete even when EMA/FVG status is not. For a LONG, adverse movement is `(entry − close) / entry`; ATR units are `(entry − close) / entry ATR`. Negative MaxDuration values mean the trade closed above entry despite finishing net-negative after fees.

| Exit | Count | Duration mean / median | Adverse move mean / median | ATR move mean / median |
|---|---:|---:|---:|---:|
| FastLoss | 15 | 5.3 / 2.7 min | 0.568% / 0.454% | 0.232 / 0.240 ATR |
| SL | 1 | 117.0 / 117.0 min | 2.174% / 2.174% | 1.464 / 1.464 ATR |
| MaxDuration | 3 | 120.4 / 120.3 min | 0.059% / 0.000% | 0.138 / 0.000 ATR |

FastLoss duration ranged from 0.1 to 13.7 minutes; adverse movement ranged from 0.360% to 0.981%, or 0.060 to 0.461 ATR. **Eight of 15** FastLoss exits closed within three minutes, and all 15 closed inside 0.5 ATR. This is clearly distinct from the single regular SL: much faster and much shallower. MaxDuration losses are also shallow, but occur at the opposite time extreme near 120 minutes, so they do not share the immediate-reversal timing signature.

The SL comparison is only one trade and cannot establish a stable regular-stop baseline.

## Comparison with the September observations

- **September 9:** the earlier observation reported a pre-fix cluster with volume 1.9x–8.1x, extended directional RSI, and EMA bypass in several trades. It is excluded from the numerical cohort because it predates the 2026-09-14 clean window. It is contextual evidence only.
- **September 20:** the six named standard-score FastLoss trades were ZAMA, CELR, EVAA, ZIL, 龙虾, and ALLO. Four of those six had the combined EMA-bypass-plus-positive-FVG signature; all four had extreme volume of 3.7x or more. The four named winners did not have that combined signature: AR, C, and SKL were EMA-bypass-only, while the Major Watch AVAX record did not expose comparable EMA/FVG fields.
- The post-fix data therefore reproduces the narrow combined-signature warning seen on September 20, and is directionally consistent with the broader September 9 volume/EMA concern. It does **not** validate an RSI rule, a volume-only rule, or a causal same-candle mechanism.

## Decision

**Leave the hypothesis unresolved and gather more data.** The observed 4/4 failure rate for EMA bypass plus positive FVG is important enough to monitor, but the strict sample is 11 total trades and only four combined-signature trades. A targeted strategy change now would overfit one clustered day.

The next analysis should use the same mutually exclusive signatures after at least 30 fully instrumented post-fix LONG trades are available, while preserving more than the rolling 100-trade audit window and recording volume, EMA-bypass status, and FVG score as structured fields.