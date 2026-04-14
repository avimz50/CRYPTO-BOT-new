# Adaptive Sniper Bot — Technical Architecture Summary
Generated: April 14, 2026 | File: bot.py (7,697 lines)

---

## 1. Core Strategy Logic

### Three Parallel Entry Tracks

---

### Track A — Swing (Main Strategy)

- Timeframes: 4H primary, 1H fallback
- Candle history: 250 bars (ensures accurate EMA200)
- Minimum score: 88/100
- Max concurrent trades: 3
- Leverage: 3x (Isolated)
- Margin per trade: $5–$50 (dynamic, clamped)

**Scoring System (100 points total):**

| Category | Max | Conditions |
|---|---|---|
| Trend | 30 | Price above EMA200, Bollinger Upper, positive 24h change |
| MACD | 25 | Signal crossover, positive histogram, momentum |
| RSI | 20 | RSI(14) in range 40–65 (LONG), divergence bonus |
| BB + Volume | 15 | BB Squeeze detected + breakout, Volume ≥ ×1.5 avg |
| Candle Patterns | 10 | Engulfing, Hammer, Rejection wick |

---

### Track B — Scalp (Mean Reversion)

- Trigger LONG: coin dropped >20% in 2h + RSI 15m < 18 + bounce ≥1% from low
- Trigger SHORT: coin up >30% in 24h + RSI 15m > 82
- Leverage: 5x | Margin: $15 | Position: $75 controlled
- TP: 3% | SL: 1.5% | Max duration: 60 minutes
- Max concurrent: 2 scalp trades

---

### Track C — High-Velocity / Cliff (5m Explosive Candle)

- Trigger: single 5m candle moves >2.5% AND volume >3× 10-bar average
- RSI Divergence: RSI(14) on 15m > 70 → HIGH-CONVICTION SHORT signal
- Leverage: 10x | Margin: $50 | Position: $500 controlled
- TP: 3% | SL: 2.5% | BE trigger: 1.5% | Trailing: 1.5% from peak
- Max duration: 30 minutes | Max concurrent: 2

---

### BTC Universal Compass

Before every entry, BTC/USDT is checked against its EMA20 on 15m:
- BTC above EMA20 → approves LONG, blocks counter-trend SHORT
- BTC below EMA20 → approves SHORT, blocks counter-trend LONG

The bot never fights the BTC macro trend.

---

## 2. Current Ruleset (All Active Filters)

### Kill-Switch (Fear & Greed Index)

| FNG Range | Label | Effect |
|---|---|---|
| 0–24 | Extreme Fear | All new Swing entries BLOCKED |
| 25–29 | Fear (adjusted) | Entries allowed with conservative parameters |
| 70+ | Greed | Position size reduced to 60% |
| Any | Kill-Switch active | Scalp and High-Velocity still allowed |

**Sniper Exception (bypasses Kill-Switch):**
- Score ≥ 92
- Volume ≥ 2.5× average
- Price within 5% of EMA200
- Claude AI (claude-3-haiku) returns "STRONG BUY"
- Entry at 50% margin (Half-Size)

---

### RSI Veto

- LONG blocked if RSI(14) > 65 (chasing pumps)
- SHORT blocked if RSI(14) < 28 (shorting oversold)

---

### EMA200 Anti-Chase

- If price is >2.5% away from EMA200 → entry rejected
- Exception: Volume ≥ 1.5× average (genuine breakout)

---

### Volume Minimums

- Swing: $10M 24h volume minimum
- Scalp: $50M 24h volume minimum
- Breakout Top10: coin-specific (filtered per scan)

---

### Blacklist (Slow Movers)

Always skipped: `TRX/USDT`, `ADA/USDT`

---

### Hunter Mode

Activated when: FNG ≥ 70 OR any coin up >15% in 24h

- Minimum RR raised to 1:3
- TP1 = 1× SL distance, TP = 3× SL distance
- Margin automatically reduced
- Telegram alert sent on activation

---

### Precision Filters (Swing)

- BB Squeeze Ratio: width < 50% of 20-bar historical average
- BB Squeeze Breakout threshold: price within 0.5% of band
- Timeframe adaptive: if 4H trend weak → falls back to 1H

---

### Stagnation Exit

- After 4 hours in a trade, if price is within ±0.5% of entry → force close
- Only applies to `phase = initial` (before TP1 is hit)
- Does NOT apply to Scalp or Cliff tracks (they have their own time limits)

---

## 3. Risk Management

### Position Sizing Formula

```
max_risk_usd = equity × 1.5%
pos_size     = max_risk_usd / (sl_pct / 100)
margin       = pos_size / leverage
margin       = clamp(min=$5, max=$50)
pos_size     = margin × leverage  (recalculated after clamp)
```

Example: Equity $135 → max_risk=$2.02 → SL=5% → pos_size=$40.4 → margin=$13.47 @ 3x

---

### Dynamic Stop-Loss (3 Layers)

1. **Asset class base:**
   - BTC, ETH, SOL (Major) → 3.0%
   - All other altcoins → 5.0%

2. **Fear buffer:**
   - FNG < 25 → add +1.0%

3. **ATR floor:**
   - Final SL = max(base + buffer, 1.5 × ATR%)
   - Ensures trade has room to breathe based on actual volatility

TP and TP1 scale proportionally to maintain RR when SL changes.

---

### FNG Mode (Swing Only)

| FNG | Mode | SL | TP1 | TP | BE trigger | Min RR |
|---|---|---|---|---|---|---|
| 0–25 | Conservative 🛡️ | 3% | 2% | 4% | 1% | 1.3 |
| 26–45 | Careful ⚠️ | 4% | 3.5% | 7% | 1.5% | 1.5 |
| 46–55 | Standard ⚖️ | 5% | 5% | 10% | 2.5% | 2.0 |
| 56–75 | Aggressive 🚀 | 6% | 7% | 15% | 3.5% | 2.0 |
| 76+ | Moon 🌕 | 8% | 10% | 25% | 5% | 2.5 |

---

### Exit Logic (evaluated every 60 seconds per trade)

Priority order:

1. **TP Hit** → close 100%, log win
2. **SL Hit** → close 100%, log loss
3. **BE Trigger** (profit ≥ be_pct%) → move SL to entry + 0.1% (risk-free)
4. **TP1 Hit** → close 50% of position, activate Trailing Stop on remainder
5. **Trailing Stop** → 1.5% from peak price (ATR-based: 1.5 × ATR)
6. **Partial25** → close 25% when profit >5% then drops >1% from peak
7. **Stagnation** → force close after 4h with <0.5% movement
8. **Time Limit** → Scalp: 60min, Cliff: 30min

---

### Daily Circuit Breaker

If total daily P&L drops below a configured threshold → all new entries blocked for the rest of the day. Resets at midnight Israel time.

---

## 4. Architecture & State

### Demo Mode — No Real Orders

The Bitget API is used READ-ONLY:
- `exchange.fetch_ticker(symbol)` — current price
- `exchange.fetch_ohlcv(symbol, timeframe, limit)` — candle data
- `exchange.fetch_tickers()` — all pairs (for scanning)

`create_order()`, `cancel_order()`, `fetch_balance()` are NEVER called.
All P&L is virtual. The bot tracks a simulated wallet starting at $200.

---

### State Variables (In-Memory + Disk)

```python
active_trades      []    # RLock-protected list of open trade dicts
wallet             {}    # balance, total_pnl, starting, equity_history
closed_trades_log  []    # rolling 48h window of closed trades
trade_audit_log    []    # persistent 100-trade audit log
daily_stats        {}    # wins, losses, pnl, close_reasons per day
```

Every mutation triggers a disk save:
- `save_active_trades()` → `active_trades.json`
- `save_wallet()` → `wallet.json`
- `_save_audit_log()` → `trade_audit.json`

On startup, all three are loaded back from disk automatically.

---

### Thread Architecture (9 concurrent threads)

| Thread | Function | Interval |
|---|---|---|
| Flask HTTP | REST API on port 8091 | always-on |
| Telegram Polling | Command handler (infinity_polling) | realtime |
| `track_trades` | Monitor open trades for SL/TP/BE/Trailing | 60s |
| `auto_scan_loop` | Swing scan (production only) | 3600s |
| `scalp_scanner` | Mean Reversion scanner | 300s (600s if Kill-Switch) |
| `top10_breakout` | Top 20 coins breakout scan | 300s |
| `high_velocity` | Cliff/Rocket 5m detector | 120s |
| `major_watch` | BTC/ETH/SOL/BNB dedicated monitor | 300s |
| `sol_watch` | SOL momentum (BTC EMA20 + 1H>4H high) | 900s |

All threads run in `daemon=True` mode and are protected by try/except at the loop level.

---

### Internal Flask API (port 8091)

Used by the Express API server (port 8080) to serve the dashboard:

| Endpoint | Method | Description |
|---|---|---|
| `/api/trades` | GET | Active trades list |
| `/api/wallet` | GET | Wallet state |
| `/api/hot` | GET | Hot candidates (Top 15 gainers) |
| `/api/trade_audit` | GET | Last 100 closed trades |
| `/api/fng_settings` | GET/POST | Read/update FNG thresholds |
| `/api/make` | POST | Receive commands from Make.com agent |
| `/api/last_scan` | GET | Last scan results |
| `/api/debug` | GET | System diagnostics |

---

### External Integrations

| Service | Purpose | Auth |
|---|---|---|
| Bitget (ccxt) | Market data (read-only) | API Key + Secret + Password |
| Telegram Bot API | Alerts + commands | Bot Token + Chat ID |
| Anthropic Claude | Sniper Kill-Switch bypass decision | ANTHROPIC_API_KEY |
| Google Gemini | Daily audit reports + /news analysis | AI_INTEGRATIONS_GEMINI_* |
| Make.com Webhook | Outbound trade signals + inbound commands | Secret token: sniper2026 |
| Alternative.me | Fear & Greed Index (cached 1h) | Public API |

---

## 5. Error Handling

### API Timeouts / Rate Limits

- `ccxt` is initialized with `enableRateLimit: True` → automatic backoff between calls
- All `exchange.*` calls are inside try/except blocks
- On exception: prints error, skips current iteration, sleeps, retries next cycle
- No crash propagation from a single failed API call

### Thread Resilience

Every scanner thread uses this pattern:
```python
while True:
    try:
        # ... scan logic ...
    except Exception as e:
        print(f"[Thread] Error: {e}")
    time.sleep(INTERVAL)
```
A single exception never kills the thread.

### Telegram Errors

`send_msg()` is wrapped independently — a Telegram 400/429 error does not affect trade logic.

### Bot Crash Recovery

- SIGTERM handler saves state to disk before shutdown
- On restart: `active_trades`, `wallet`, `audit_log` all reload from JSON files
- No trade data is lost across restarts

### Gemini / Claude Errors

- Claude error → conservative fallback: REJECT (does not block trade, Kill-Switch stays active)
- Gemini error → audit skipped silently, no crash

---

## 6. Key Configuration Constants (Latest Values)

```python
# Entry
MIN_SCORE              = 88       # Minimum score to open a trade
MAX_TRADES             = 3        # Max concurrent trades
RSI_VETO_LONG          = 65       # Block LONG if RSI > 65
RSI_VETO_SHORT         = 28       # Block SHORT if RSI < 28
EMA_PROXIMITY_PCT      = 2.5      # Max distance from EMA200 (%)
VOL_EMA_BYPASS_MULT    = 1.5      # Volume multiplier to bypass EMA filter

# SL / TP
SL_BASE_MAJOR          = 3.0      # BTC/ETH/SOL base SL (%)
SL_BASE_ALTCOIN        = 5.0      # Altcoin base SL (%)
SL_FEAR_BUFFER         = 1.0      # Extra SL when FNG < 25 (%)
SL_ATR_MULT            = 1.5      # ATR multiplier for SL floor
TP1_PCT_FIXED          = 5.0      # TP1 — partial close 50%
TP_PCT_FIXED           = 15.0     # TP full target

# Exits
BE_BUFFER_PCT          = 2.0      # Break-Even trigger (%)
BE_LOCK_BUFFER_PCT     = 0.1      # SL locks at entry + 0.1%
TRAIL_PCT              = 1.5      # Trailing stop (% from peak)
ATR_TRAIL_MULT         = 1.5      # ATR multiplier for trailing
PARTIAL_25_TRIGGER     = 5.0      # Partial close at 5% profit
STAGNATION_MIN_HOURS   = 4.0      # Hours before stagnation check
STAGNATION_RANGE_PCT   = 0.5      # Movement threshold for stagnation (%)

# Sniper Mode
SNIPER_MIN_SCORE       = 92       # Score to attempt Kill-Switch bypass
SNIPER_EMA_PCT         = 5.0      # Max EMA200 distance for Sniper
SNIPER_VOL_MIN         = 2.5      # Volume multiplier for Sniper
SNIPER_MARGIN_MULT     = 0.5      # Half-size entry

# Scalp
SCALP_LEVERAGE         = 5
SCALP_MARGIN           = 15.0
SCALP_TP_PCT           = 3.0
SCALP_SL_PCT           = 1.5
SCALP_MAX_DURATION_MIN = 60

# High-Velocity
CLIFF_DROP_PCT         = 2.5      # Min candle move for velocity event
CLIFF_VOL_MULT         = 3.0      # Volume multiplier for velocity
CLIFF_LEVERAGE         = 10
CLIFF_MARGIN           = 50.0
CLIFF_SL_PCT           = 2.5
CLIFF_TP_PCT           = 3.0
CLIFF_MAX_DURATION_MIN = 30

# Risk
MAX_EQUITY_RISK_PCT    = 1.5      # Max risk per trade (% of equity)
SWING_TRACK_LEVERAGE   = 3        # Swing leverage
MAJOR_COINS            = {BTC, ETH, SOL}
SLOW_MOVERS            = {TRX/USDT, ADA/USDT}
```

---

## 7. Telegram Commands Reference

| Command | Description |
|---|---|
| `/status` | Active trades with SL/TP/floating P&L |
| `/report` | Full daily statistics report |
| `/audit` | Gemini AI deep analysis of last 24h |
| `/news <text>` | Paste news → Gemini analyzes sentiment + impact on open trades |
| `/scan` | Manual scan now (dev mode) |
| `/close BTC` | Manually close a position |
| `/addtrade SOL LONG 83.66` | Manually register an active trade |
| `/update BTC 84000 95000` | Update SL/TP for a trade |
| `/watch SOL LONG` | Watch a coin every 15 minutes |
| `/unwatch SOL` | Stop watching |
| `/fng` | Show current FNG thresholds |
| `/setfng extreme 15` | Adjust Kill-Switch threshold |
| `/top10` | Manual breakout scan on 20 coins |
| `/fillslots` | Fill empty trade slots from top 20 |
| `/sol` | Live SOL analysis |
| `/ping` | Bot health check |
| `/dashboard` | Link to web dashboard |
| `/test` | Open a test BTC trade |
| `/home` | Full command menu |

---

## 8. Make.com Integration

**Outbound (Bot → Make):**
Every `place_order()` call fires a POST to:
`https://hook.eu1.make.com/14l99wxa67quwoh9q9n042238mjsuars`

Payload: `{symbol, direction, entry_price, timestamp, strategy, score, sl, tp}`

**Inbound (Make → Bot):**
POST to `/api/make` with `secret: sniper2026`

| Command | Fields | Action |
|---|---|---|
| `news_alert` | symbol, headline, sentiment | Telegram alert |
| `whale_alert` | symbol, direction, amount_usd | Whale alert |
| `analysis` | text | Free-text to Telegram |
| `close_trade` | symbol | Force close active trade |
| `tighten_sl` | symbol, new_sl_pct | Update SL on active trade |

---

*End of Technical Summary — Adaptive Sniper Bot v2026*
