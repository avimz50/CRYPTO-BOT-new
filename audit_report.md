# Technical Indicator, Trading Logic, and Risk Management Audit

**Audit date:** 2026-09-08  
**Scope:** Static, read-only review of project dependency manifests, Python source files, trading logic, indicator implementations, pre-trade filters, external enrichment, lifecycle controls, persistence, and backtest/research modules.  
**Change boundary:** No application code, configuration, strategy settings, runtime state, or workflow was modified. This file is the sole audit deliverable.

## Executive Summary

The project already contains a broad custom technical-analysis stack: classical indicators, ICT-style order blocks and fair value gaps, BTC/Fear-and-Greed regime gating, multi-timeframe scoring, volume/open-interest/momentum filters, order-book and funding enrichment, several paper-trading strategies, and a substantial trade lifecycle with stop-loss, break-even, partial exits, trailing exits, cooldowns, and a daily loss circuit breaker.

The strongest implementation is the breadth of signal and lifecycle coverage. The principal weaknesses are architectural and evidentiary: core decisions are concentrated in a very large orchestration module; indicator calculations and gate behavior are not uniformly validated or replayable; some AI and data-enrichment paths fail open; portfolio-level exposure controls are limited; order-flow analysis is shallow; and research/backtest implementations do not form one governed, live-parity promotion pipeline. This remains a paper-trading system and does not place real exchange orders in the audited configuration.

## 1. Dependency and Architecture Audit

Read-only inventory complete.

DEPENDENCIES/ENV
- requirements.txt:1-15 and pyproject.toml:6-20 declare ccxt, pandas, pandas-ta, aiohttp, requests, anthropic, google-generativeai, Google APIs/storage; no `ta` package and no `smartmoneyconcepts` package. xlsxwriter is only in requirements.txt, not pyproject.toml.
- uv.lock resolves aiohttp/anthropic/ccxt/google-generativeai/pandas/pandas-ta (uv.lock:38,160,210,724,1208,1320); no ta/smartmoneyconcepts. pnpm-lock is frontend-only. No .env files found. Environment values are read directly in config.py:24-27 (Gemini), config.py:284-286 (Make secret), bot.py:2179-2184 (Bitget credentials), claude_gate.py:36 (Anthropic key).
- Runtime JSON config/state: config.json, fng_settings.json, .local/trade_settings.json; paths and defaults in config.py:10-18,289-319. Persistent state uses artifacts dashboard JSON plus state_store/Object Storage.

TECHNICAL/SCANNING/SCORING
- strategy_engine.py:24-125: pandas-ta ATR/RSI/EMA and fixed SL/TP calculator; pure math boundary.
- market_logic.py:25-169: Fear & Greed API/cache, synthetic/blended FNG; :176-294 ICT-style order-block detector implemented locally (not smartmoneyconcepts); :301 onward candle patterns, flags, FVG, BB squeeze, volume and momentum logic. It imports pandas_ta directly at market_logic.py:7-9.
- bot.py:2160-2286: synchronous OHLCV and async ccxt OHLCV/OI prefetch/cache for 4h/1h/15m; default limit 250, parallel gather. bot.py:7308-7643 is main scan pipeline: sentiment/adaptive threshold, prefetch, regime gate, symbol/blacklist/cooldown/duplicate/circuit-breaker checks, multi-timeframe score_symbol, momentum gate, RSI/EMA context, sector guard, OB extraction, then open_demo_trade.
- bot.py:7027-7053 wraps scan persistence; :6777-6905 manual scan; :9337 onward scheduled research/scan loops.
- Strategy-specific open paths: bot.py:2937 open_demo_trade, :7833 open_scalp_trade, :8070 open_cliff_trade (Velocity), :8495 open_breakout_trade; swing path invokes claude_gate around bot.py:2986-3068. Strategy enable/threshold constants are config.py:96-120,153-190.
- Scoring/selection is distributed across market_logic.py and bot.py rather than a single typed decision object. bot.py:7488-7530 selects best of 4H→1H→15m; bot.py:7550 onward applies final gates and opens.

PRE-TRADE GATES/RISK CONTROLS
- bot.py:1783-1866 place_order is the atomic final gate: canonical USDT swap validation, regime/direction check, bullish validation score threshold, entry restraint/cooldown, spread, one-symbol rule, MAX_TRADES, balance/fee check under trades_lock, persistence/webhook.
- Additional config controls: config.py:42-83 fixed risk targets, daily loss breaker, spread/re-entry controls, RSI veto/EMA proximity; config.py:184-191 momentum/OI/volume gate.
- bot.py:259 check_daily_circuit_breaker; :296 sector concentration; :1707 entry restraint; :1763 spread. Trade monitoring/lifecycle is bot.py:3443 onward.
- Important flow inconsistency: the normal scan explicitly labels Claude as advisor-only and proceeds regardless (bot.py:7573-7584). Claude is a hard gate only in strategy-specific paths (scalp/velocity/breakout around :7909, :8113, :8550) and some swing flow.

AI/NEWS/EXTERNAL ENRICHMENT
- claude_gate.py:140-189 enriches each signal with ccxt funding rate, top-5 order-book imbalance, and ticker 24h stats; :118-137 DuckDuckGo search; :216-407 Anthropic gate, JSON decision, stats/fallback; :410-414 weighted score.
- claude_gate.py:230-240 explicitly falls back to approve/70 when client unavailable or errors. This is fail-open, not institutional pre-trade risk control.
- claude_research.py:111-227 uses CoinGecko trending/movers/coin data and DeFiLlama TVL; :230-322 exchange TA (manual RSI/EMA), :281-300 DuckDuckGo news; :327-461 Anthropic tool orchestration/prompts; :524 onward evidence tracking and candidate execution. It requires TA + fundamentals evidence in its candidate pipeline, but execution callback is separately injected from bot.py.
- bot.py:5465-5584 Claude/Gemini news analysis; :630-722 Claude sandbox analysis; :1078-1113 Make.com news webhook. gdrive_reporter.py:148-272 and :440-521 Gemini daily reporting/AI analysis.

INSTITUTIONAL-GRADE ARCHITECTURE GAPS
1. No `ta` or `smartmoneyconcepts` dependency; proprietary/manual indicators are unversioned and not independently validated. No indicator metadata, feature store, or deterministic feature snapshots.
2. No canonical event-driven order/decision model: scoring, gates, strategy entry functions, persistence and monitoring are spread through huge bot.py; difficult to prove gate ordering, replay, or audit a decision.
3. AI/news is not a reliable control plane: Claude gate is fail-open on missing key/timeouts/errors, normal swing scan ignores its approval, DuckDuckGo Instant Answer is not a timestamped/headline-quality news feed, and AI outputs are not cryptographically/versioned captured with prompt/model/input snapshot.
4. Order-book enrichment is shallow top-5 volume imbalance, no spread/depth/slippage/market-impact calculation, stale-book detection, quote age, or execution-quality check. Funding/ticker calls lack freshness/consistency policy.
5. Pre-trade controls omit institutional essentials: independent risk service, per-order notional/leverage/margin limits, portfolio VaR/correlation/beta, cross-strategy exposure/net delta, liquidation buffer, volatility-scaled sizing, max slippage, price collars, kill switch, exchange status, reduce-only/position-mode verification, idempotent client order IDs, and post-submit fill/reconciliation.
6. Data integrity gaps: 250-bar fetches are hard-coded, no timestamp completeness/duplicate/outlier checks, no clock synchronization, candle-close policy, multi-source reconciliation, or durable market-data quality scores. Async cache has no TTL and is reset per scan (bot.py:7332-7339).
7. Concurrency/persistence is only partially atomic: active trade mutation is locked, but external calls and file/Object Storage persistence/webhook are not transactional; crash recovery, exactly-once events, exchange-vs-local reconciliation and orphan order handling are absent.
8. Risk model is mostly fixed-percent SL/TP and fixed $50 margin (config.py:29-45), not volatility/liquidity/correlation aware; no pre-trade stress/scenario tests or formal limit hierarchy.
9. Backtests exist (backtest_*.py) but no evident unified walk-forward, transaction-cost/slippage/funding/latency model, live-vs-backtest feature parity, or promotion governance. No institutional monitoring/alerting/SLO or immutable audit trail.
10. Symbol/universe and exchange assumptions are Bitget-centric and hard-coded; no venue routing, failover, or market-hours/maintenance handling.

## 2. Indicator, SMC, Trading Logic, and Pre-Trade Filter Map

Python implementation map (read-only scan)

PRIMARY LIVE ENGINE
- `market_logic.py`
  - `compute_synthetic_fng(df_btc)` lines 50-96: synthetic BTC macro/Fear&Greed proxy; RSI14 40%, volume direction/ratio 25%, ATR14 relative volatility 20%, EMA50 distance 15%; fallback 50.
  - `get_fear_greed()` 25-42: cached Alternative.me FNG API (1h), retains prior cache on errors.
  - `get_fng_regime()` 110-129: official FNG while API healthy; synthetic BTC fallback when offline.
  - `get_fng_blended()` 141-169: 70% official + 30% synthetic when available; synthetic-only offline.
  - `detect_order_blocks(df,direction,lookback=50)` 176-290: ICT-style OB; last opposite candle before 3 same-direction candles and >=1.5% impulse; nearest zone selected; LONG valid in/within 1% above bullish OB, SHORT in/within 1% below bearish OB. Returns hit/high/low/description. This is the sole substantive live OB detector.
  - `score_candles(df_15m,direction)` 301-362: candle scoring helper (currently explicitly filtered/zeroed by master scorer at 981-982; effectively partial/dead).
  - `detect_flag(df,direction)` 364-454: 1H pole/flag breakout with volume confirmation; +15 in scorer.
  - `detect_fvg(df,direction,lookback=20)` 460-522: ICT 3-candle gap (`low[i]>high[i-2]` bullish; inverse bearish), nearest gap; hit only in/near zone (1.5% directional tolerance); +10.
  - `detect_bb_squeeze(df,direction)` 529-590: pandas-ta BB20/2 width vs historical lookback ratio; directional breakout-start test; +12 or pending +6.
  - `detect_volume_buildup(df,candles=5)` 597-623: consecutive >=5% rising volume; +8 for >=4 streak, +4 for 3.
  - `detect_rsi_divergence(df,direction,lookback=30)` 630-676: RSI14 half-window swing comparison; bullish lower price low/higher RSI, bearish opposite; +10 or +5.
  - `momentum_gate(symbol,df_15m,df_1h,direction,volume_usd_24h,oi_data)` 683-806: hard liquidity Volume/OI ratio, EMA9/21/50 alignment on 1H, rolling 24-bar VWAP directional filter; OI/VWAP exception paths soft-pass, missing history fails EMA gate, unexpected exception soft-passes.
  - `score_symbol(df_3h,df_1h,symbol,direction,...)` 813-1140: master scorer. Calculates EMA200, MACD(12,26,9), RSI14, BB20/2, ATR14; trend +25, MACD +15, RSI +10 with extreme vetoes, BB +20, volume +30 with hard <1.5x veto; flag/FVG/OB/squeeze/volume-buildup/RSI-divergence/pump bonuses; FNG adjustment and late-entry penalties/vetoes. Note variable `ema200_1h` is calculated from `df_3h` (naming inconsistency).
  - `sentiment_check()` 1161-1185; `adaptive_threshold()` 1209-1281: macro/FNG bands plus BTC regime/direction deltas, RSI veto adjustment and score clamp.
  - `adaptive_exit_params()` 1302-1346: FNG/regime-dependent TP1 and max duration.

BOT REGIME/FILTER IMPLEMENTATIONS
- `bot.py:get_btc_regime()` 2625-2643: simple BTC 4H close vs EMA20: BULL/BEAR, NEUTRAL on error. Legacy/duplicate relative to richer regime logic.
- `bot.py:get_market_regime()` 2651-2712: cached 5m BTC EMA20(4H)+FNG regime; BEARISH if FNG<38 OR BTC below EMA, BULLISH only FNG>60 AND BTC above, else NEUTRAL; fail-safe defaults FNG=0/BTC below.
- `bot.py:is_direction_allowed()` 2715 onward: central direction gate (BEARISH blocks LONG, BULLISH blocks SHORT, NEUTRAL capacity/validation rules).
- `bot.py:_sandbox_value_levels()` 552-610: display/advisor-only EMA200 levels; not execution logic.
- `bot.py:_btc_above_ema20_15m()` 8323-8339: BTC 15m EMA20 correlation filter, fail-safe false; used by SOL/related specialized entries.
- `bot.py:get_btc_intraday_bias()` 8349-8398: cached 3m BTC EMA20(15m)+daily-open BULLISH/BEARISH/SIDEWAYS.
- `bot.py:_coin_1h_breakout_above_4h_high()` 8420-8437: 1H breakout over prior 10 complete candles + 4H RSI14.
- `bot.py:_coin_breakout_full()` 8454-8492: generic LONG/SHORT 1H breakout/breakdown, 4H RSI14, volume ratio; SHORT requires >=1.5x volume.
- `bot.py:open_breakout_trade()` 8495 onward: breakout entry gate; consults regime/FNG and computes ATR14 around lines 8542-8595.
- `bot.py:_cliff_detect()` 7992-8054 and `_rsi_from_series()` 8056-8064: specialized cliff/scalp RSI helper; RSI helper is small duplicate of generic indicator calculation.
- `bot.py:_scan_batch()` 7027-7053 and `_scan_batch_inner()` 7308 onward: live scan wrapper, prefetches 4H/1H/15m, applies regime gate, then momentum gate and `score_symbol` across timeframes. `_score_regime_watch_candidate()` 7104-7168 repeats this scoring path for observation-only candidates.

PURE INDICATOR/DISCRETE ENGINES
- `strategy_engine.py:calc_atr(df,period)` 24-42: pandas-ta ATR, latest positive value or 0. `calc_rsi()` 99-110: RSI14/latest, fallback 50. `calc_ema()` 114-125: EMA/latest, fallback 0. `calc_targets()` 46-95: fixed percentage SL/TP, unrelated to signal scoring.
- `pattern_scorer.py:PatternScorer` class 31-101; `detect()` 48-101: strict no-lookahead liquidity sweep. LONG sweeps prior N-bar low and reclaims above; SHORT prior high and closes below; ATR-relative pierce and reclaim thresholds, score 0-100. `DEFAULT` 105 and `score_sweep()` 108-120 adapter. Used only by `backtest_entries.py`, not live bot.

BACKTEST/DUPLICATE IMPLEMENTATIONS
- `backtest_entries.py`: `ema_series`, `rsi_at`, `atr_pct_at`, `features_at` around 72-132 (manual EMA/RSI/ATR feature copies); `score_bot_like` 148-165 duplicates live EMA/MACD/RSI/volume shape; `score_trend` 167-176; sweep scorer registration/use 199-200, 477-493. Research-only, not imported by bot.
- `backtest_swing.py`: `ema_series` 71-79, `rsi_at` 81-91, `features_at` 94-104 (manual EMA/RSI/MACD); `score_bot_like` 106-119; `build_regimes` 126-149 (past-only BTC EMA regime); `REGIMES` 152 onward and simulation 162-244. Separate research regime model, duplicate/inconsistent with live FNG+EMA regime.
- `backtest_pullback.py`: `rsi_series()` 66-83 and pullback simulation 86 onward; duplicate RSI-only research logic.
- `backtest_ranking.py`: `_vol()` 59-63 and `bot_score_at()` 80-86; simplified volume/trend ranking, not live implementation.
- `backtest_daily.py`: `sma()` 68-76 and `run_portfolio()` 78-151; daily SMA trend filter only, separate research strategy.
- `exports/backtest_daily.py`: copied/exported duplicate of `backtest_daily.py` (daily SMA trend, BTC pair fallback around 345-359); dead/export artifact.

PARTIAL/DEAD/NOT FOUND
- No Python class named SMC and no standalone SMC aggregate implementation found; SMC exists only as ICT-style OB/FVG plus liquidity sweep research. `pattern_scorer.py` explicitly distinguishes sweep from EMA/RSI/MACD and is not live-wired.
- No dedicated live liquidity-sweep detector in `market_logic.py`/`bot.py`; only `pattern_scorer.PatternScorer` backtest implementation.
- No standalone live MACD function/class; MACD exists inline only in `score_symbol` 843-865 and manual backtest approximations (`backtest_entries.py`, `backtest_swing.py`).
- No standalone live Bollinger indicator helper; BB calculation is inline in `score_symbol` 846 and `detect_bb_squeeze` 539, with dashboard plotting/use around `bot.py` chart code 2291-2474.
- ATR is duplicated: canonical `strategy_engine.calc_atr`; inline `market_logic.score_symbol`; synthetic FNG ATR; breakout/scalp inline `bot.py`; backtest ATR approximation. 
- EMA/RSI are heavily duplicated inline across market_logic, bot specialized loops, strategy_engine, and research scripts. `get_btc_regime()` is a legacy/simple duplicate of `get_market_regime()`.
- Volume/trend/macro filters are substantive in `momentum_gate` and `score_symbol`; additional specialized BTC EMA, breakout volume, FNG and daily-open filters in bot. `backtest_daily`/`backtest_swing` are research-only duplicates, not execution paths.

## 3. Risk Management and Trade Lifecycle Inventory

Python risk/trade-lifecycle inventory (read-only)

- `config.py:29-37`: fixed sizing: LEVERAGE=10, MARGIN=$50, POSITION_SIZE=$500; estimated taker fee 0.06%/filled side (paper assumption, not exchange-reported). `config.py:42-48`: fixed SL 2%, TP1 1%, TP2 4%, daily loss limit -$15. `config.py:50-56`: max spread 0.8%, symbol reentry 12h, loss reentry 24h, swing loss streak limit 3/pause 2h. `config.py:73-80`: trailing activation 2%, BE buffer 2%, BE lock buffer 0.1%, trailing 1.5%, ATR multiplier 1.5, partial trigger/drop 5%/1%. `config.py:96-141`: strategy-specific scalp/swing sizing and SL/TP, scalp max duration 48m, smart timeout 4h unless >=1% profit, swing/breakout max duration 120m, fast-loss 3.5% margin loss in first 15m. `config.py:143-150`: RSI reversal guard thresholds (after 15m). Additional cliff sizing/duration: `config.py:177-180` (10x/$50/$500/24m).

- `strategy_engine.py:24-42` `calc_atr`: ATR with safe 0 fallback. `strategy_engine.py:46-90` `calc_targets`: direction-aware fixed SL/TP1/TP2 and BE=entry; TP1 intended to trigger BE + 75% close. No broker-native protective-order placement here.

- `bot.py:259-281` `check_daily_circuit_breaker`: blocks new entries when in-memory daily_stats P&L <= DAILY_LOSS_LIMIT, alerts once; resets at date rollover and can be manually released if P&L recovers. Existing trades continue. `bot.py:290-315` `get_sector`/`check_sector_concentration`: blocks same-sector/same-direction concurrent positions, but unmapped `Other` is unrestricted. `bot.py:397-429`: RLock and in-memory cooldown maps: breakout SL 5h, regime close 15m, generic close 2h, max-duration 2h; these maps are not persisted.

- `bot.py:466-469` `add_daily_pnl`: locked in-memory daily P&L accumulator. `bot.py:3443-3640` `track_trades`: every 60s, batch-fetches prices, emergency regime-closes LONGs in bearish regime/SHORTs in bullish regime after 3m grace, updates snapshots, direction-aware SL/TP/BE checks. `bot.py:3641+` scalp lifecycle: SL/TP/time expiry, BE moves SL to entry (`bot.py:3663-3671`), closes at `SCALP_MAX_DURATION_MIN`; normal lifecycle continues after line 3690 through the large `track_trades` body and implements TP1 partial, BE, trailing, max-duration, fast-loss, RSI reversal, stagnation and exhaustion exits. `bot.py:3370-3440` `check_momentum_exhaustion`: only checks when floating profit >=$15; 5m RSI reversal plus >=2.2x volume closes to protect profit.

- `bot.py:7003-7023` `trade_monitor_loop`: independent 60-second lifecycle thread, so management is not dependent on scan cadence. `bot.py:7308-7350` `_scan_batch_inner`: prefetches 4h/1h/15m and applies sentiment/adaptive thresholds/regime gates before entries. `bot.py:7380-7425` (inside scan entry path): capacity, generic-close and max-duration cooldown enforcement. `bot.py:7840-7870` scalp entry cooldown enforcement. `bot.py:8520-8540` breakout entry cooldown enforcement; `bot.py:8750-8770` breakout loop additionally checks SL cooldown.

- `bot.py:1783-1862` `place_order`: canonical swap-only symbol validation, direction/regime/validation checks, reentry/spread checks, duplicate symbol and atomic MAX_TRADES cap, atomic balance check including entry fee, then registers order. `bot.py:1601+` `wallet_credit`; `bot.py:1630-1661` fee accounting records entry fee and per-close fees. `bot.py:2021-2083` `_log_closed_trade`: persists gross/net P&L, fees, close reason and lifecycle metadata to audit.

- `bot.py:3602-3623`: per-trade defaults preserve monitoring for old records; remaining notional is derived for partial lifecycle. TP1 uses 75% close/25% remainder (`bot.py:3618-3620`). `bot.py:9582-9614` `reconcile_with_exchange` explicitly preserves lifecycle basis after partial/TP1 to avoid double-reducing position size, overlays SL/TP/phase/partial/peak/trailing/BE/fee metadata. Exchange-only imports at `bot.py:9622-9638` get `sl/tp/tp1=None`, i.e. no protective levels.

- `bot.py:9525-9666` `reconcile_with_exchange`: startup authoritative exchange sync; derives entry/contracts/leverage/position size/margin, overlays local metadata, preserves virtual local trades absent on exchange, imports unknown live positions. `bot.py:9680-9687` startup order loads active state, bootstraps, reconciles, then loads wallet; important synchronization exists but exchange fetch failure simply logs and returns (`bot.py:9535-9538`).

- `database_manager.py:26-34` `calc_equity`: starting + realized + floating. `database_manager.py:37-49` `reconcile_balance`: only forces balance=equity when no margin locked. `database_manager.py:69-95` `save_wallet`: recomputes floating, locked margin, equity and available balance. `database_manager.py:167-213`: active trades/audit persistence. `database_manager.py:217-233` `calc_floating_pnl`: direction-aware snapshot P&L. `state_store.py:124-172` `load_state/save_state/reset_state`: Object Storage authoritative with disk-cache fallback/write-through under a process lock.

Missing/weak institutional controls:
- No durable daily-risk ledger/UTC trading-day reconciliation; daily breaker is process-memory and can reset/release on recovered P&L, with no explicit kill-switch latch, realized-vs-unrealized policy, or broker equity authority.
- No portfolio-level gross/net exposure, notional/leverage cap, margin-utilization, liquidation-distance, per-asset/per-sector aggregate cap beyond one same-sector/same-direction block; fixed $50 sizing is not volatility/equity risk-based.
- No persistent cooldowns, loss-streak state, or restart-safe daily limits; no distributed lock/idempotency across multiple bot instances.
- No durable broker-native SL/TP verification/repair loop. Imported exchange positions have null protection; exchange sync failure leaves potentially stale local protection. No stale-price/data-age kill switch, order acknowledgement/retry/reconciliation, or partial-fill handling.
- Slippage is not modeled in live lifecycle P&L (only estimated taker fees); no spread/depth/market-impact model, maker/taker differentiation, funding/borrow costs, liquidation fees, or fee reconciliation against exchange fills.
- No max order rate/notional turnover, cancel/replace governance, venue outage mode, stale quote protection, or circuit breakers for API/data errors. AI advisor is explicitly informational/non-blocking (`bot.py:320-387`).
- No formal audit immutability/sequence IDs, append-only external ledger, UTC timestamp normalization, or independent risk service; state writes are best-effort and may silently fall back to disk.

## 4. Consolidated Gap Analysis Against an Institutional-Grade Pre-Trade Pipeline

### Critical gaps

1. **No independent, fail-closed risk boundary.** Risk checks, scoring, entry orchestration, lifecycle management, and persistence are coupled. Several enrichment failures soft-pass or approve by fallback, so degraded dependencies can weaken controls rather than halt entries.
2. **No transactional decision/order state machine.** There is no canonical immutable event sequence covering market-data snapshot, feature values, gate outcomes, intended order, simulated fill, protective orders, and reconciliation. This limits replayability and makes exactly-once recovery difficult.
3. **No venue-native protective-order assurance.** The project is paper trading; lifecycle protection is local process logic. An institutional live pipeline would require exchange-side reduce-only SL/TP placement, acknowledgement, reconciliation, and orphan-order handling before any real-money use.
4. **Insufficient portfolio risk model.** Existing concurrency, direction, sector, and daily-loss controls do not amount to portfolio VaR, expected shortfall, correlation/beta limits, net/gross exposure limits, liquidation-distance constraints, or stress testing.

### High-priority gaps

1. **Market-data quality controls:** no unified candle completeness, duplicate/outlier, clock-drift, quote-age, stale-book, cross-source reconciliation, or feature-freshness policy.
2. **Order-flow/liquidity depth:** top-level imbalance and volume/OI checks exist, but there is no robust depth curve, spread/impact estimate, trade-flow delta, liquidation map, spoofing resilience, or fill-probability model.
3. **Regime classification breadth:** BTC EMA/FNG direction gating exists, but there is no probabilistic multi-state model combining volatility, trend strength, liquidity, correlation, funding basis, breadth, and transition confidence.
4. **Sizing and risk budgeting:** primary sizing remains largely fixed margin/notional rather than volatility-, liquidity-, correlation-, and stop-distance-adjusted risk allocation.
5. **Backtest/live parity and governance:** multiple backtest/research scripts exist, but there is no single walk-forward framework with identical feature code, realistic fees/funding/slippage/latency, purged validation, promotion criteria, and versioned strategy manifests.
6. **Observability and auditability:** logs and JSON ledgers provide useful evidence but are not a durable immutable decision ledger with feature snapshots, code/config version, data timestamps, and deterministic replay.

### Medium-priority gaps

1. Centralize duplicated indicator formulas and make timeframe/candle-close semantics explicit.
2. Separate strategy signal generation from portfolio approval, execution simulation, lifecycle management, and reporting.
3. Replace broad exception soft-passes with typed failure policy: fail closed for mandatory risk inputs and explicitly degrade only optional enrichment.
4. Add per-symbol/strategy performance suspension, rolling expectancy, drawdown, and transaction-cost diagnostics.
5. Add exchange maintenance/status checks, API freshness budgets, venue failover policy, and deterministic idempotency controls.

## 5. Recommended Institutional-Grade Pipeline Shape

A stronger pipeline would be ordered and independently auditable:

1. **Market-data validation:** freshness, candle closure, gaps, duplicates, outliers, spread, depth, and exchange status.
2. **Feature snapshot:** versioned indicator/SMC/order-flow values computed once from a canonical data snapshot.
3. **Regime classification:** trend/volatility/liquidity/correlation state with confidence and transition handling.
4. **Strategy signal:** strategy-specific setup and invalidation, without position sizing or order side effects.
5. **Liquidity and execution gate:** spread, expected impact, depth, slippage collar, funding/basis, and quote age.
6. **Portfolio risk gate:** stop-distance risk, volatility sizing, concentration, correlation, net/gross exposure, drawdown, and daily limits.
7. **Order-intent state machine:** idempotent intent, simulated or real acknowledgement, protective-order confirmation, and reconciliation.
8. **Lifecycle and post-trade attribution:** direction-correct gross P&L, per-fill fees, slippage, funding, exit attribution, and immutable event history.
9. **Promotion governance:** backtest → walk-forward → shadow/paper → limited live, each with predeclared acceptance and rollback criteria.

## 6. Audit Limitations

- This was static analysis; no strategy parameters were changed and no trades were simulated as part of the audit.
- Function line references in the inventories are approximate and reflect the code at audit time.
- Exchange execution guarantees cannot be evaluated from paper fills.
- The audit maps present implementations and architectural gaps; it does not establish that any strategy has positive out-of-sample expectancy.
- Generated exports and legacy/research files may contain historical implementations that are not reachable from the current live path; those are labeled accordingly in the inventories.
