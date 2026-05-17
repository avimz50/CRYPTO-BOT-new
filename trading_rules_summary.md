# Trading Bot — Current Operational Rules Summary
**Generated:** May 17, 2026

---

## 1. Entry Triggers

**Scan Cadence:** Every ~1 hour
- Top 15 Gainers → LONG candidates
- Top 15 Losers → SHORT candidates
- Fixed Energy/Geo watchlist → LONG only

---

### Step 1 — Hard Vetoes (before scoring)

| Veto | Condition |
|---|---|
| Low Volume | Volume < $1M USD/day → rejected |
| EMA-Chase | Price > EMA200 (4H) by more than 10% → rejected (unless volume ≥ ×1.5 avg) |
| Wick Rejection | Upper wick > candle body → LONG veto; Lower wick > candle body → SHORT veto |
| RSI Extreme | RSI > 65 → LONG hard veto; RSI < 15 → SHORT hard veto |

---

### Step 2 — Technical Score (0–100+ pts)

| Component | Max Pts | Condition |
|---|---|---|
| Trend / EMA200 | 25 | 4H alignment (+15) + 1H alignment (+10) |
| MACD | 15 | Signal cross (+10) + Histogram direction (+5) |
| RSI | 10 | Sweet-spot range (+10), acceptable range (+5) |
| Bollinger Bands | 20 | Above/below mid-BB (+12) + touching correct band (+8) |
| Volume | 30 | ×1.5 avg (+20), ×2.0 avg (+30) — **<×1.5 = VETO** |
| Bull/Bear Flag | 15 | Pattern detected on 1H |
| Fair Value Gap | 10 | In-zone or near on 1H |
| Order Block (ICT) | 3 | In-zone or near on 1H |
| BB Squeeze | 12 | Pre-breakout squeeze on 1H |
| Volume Build | 8 | Smart Money accumulation |
| RSI Divergence | 10 | Detected on 1H |
| FNG Adjustment | ±5 | Fear/Greed sentiment bonus/penalty |

**Minimum threshold to open a trade: Score ≥ 78 / 100**

---

### Step 3 — Timeframe Cascade

1. Try **4H** → if score ≥ 78, proceed
2. If <78, try **1H**
3. If still <78, try **15m**

---

### Step 4 — Macro Filters (applied after scoring)

| Filter | Rule |
|---|---|
| BTC Regime | BULL (BTC > EMA50) → LONGs only; BEAR → SHORTs only |
| BTC Parabolic | BTC price > EMA200 (4H) AND RSI 1H > 60 → ALL altcoin SHORTs blocked |
| BTC Strong Uptrend | BTC > EMA200 (1H) AND EMA200 (4H) → SHORTs require score > 90 |
| Kill-Switch | FNG < 25 (Extreme Fear) → ALL trades blocked except Sniper mode |
| Sniper Mode | Score ≥ 92 bypasses Kill-Switch at half position size |
| Extreme Fear | FNG < 25 → LONGs require score > 95 |
| Greed Filter | FNG ≥ 70 → Position size reduced to 60% |
| Hunter Mode | FNG ≥ 70 → Different TP structure (1:1 TP1, 1:3 TP2) |

---

## 2. Stop Loss (SL) Logic

### Primary Formula (ATR-based)
```
SL distance = 1.5 × ATR (1H timeframe)

LONG:  SL price = Entry − (1.5 × ATR_1H)
SHORT: SL price = Entry + (1.5 × ATR_1H)
```

### Dynamic SL Floor (minimum %)
| Asset Type | Base SL | FNG < 25 Buffer | ATR Floor |
|---|---|---|---|
| Major (BTC/ETH/SOL) | 3% | +1% | max(base%, 1.5 × ATR%) |
| Altcoins | 5% | +1% | max(base%, 1.5 × ATR%) |

The ATR-based price levels from the primary formula are used for actual SL placement.

---

## 3. Take Profit Logic

### TP1 — Fast Partial Exit
```
TP1 distance = 1.0 × ATR (15m timeframe)

LONG:  TP1 price = Entry + (1.0 × ATR_15m)
SHORT: TP1 price = Entry − (1.0 × ATR_15m)
```
- Designed to be **tight and fast** — triggers partial profit + Break-Even lock quickly

### TP2 — Final Target (1:2 Risk/Reward)
```
TP2 distance = SL distance × 2.0  (exactly 1:2 RR)

LONG:  TP2 price = Entry + (1.5 × ATR_1H × 2.0)
SHORT: TP2 price = Entry − (1.5 × ATR_1H × 2.0)
```

### What Happens at TP1 Hit
1. **50% of position** is closed (partial profit taken)
2. **SL moves to Break-Even** (entry price) — triggered at 50% of TP1 distance

### Hunter Mode Override (FNG ≥ 70)
- TP1 = 1:1 RR (same distance as SL)
- TP2 = 1:3 RR minimum

---

## 4. Position Sizing & Margin

### Primary Sizing Formula
```
Risk per trade = $2.00 (fixed — RISK_PER_TRADE_USD)
SL%      = |entry − SL_price| / entry × 100
pos_size = max($2.00 / SL%, $20 minimum)
leverage = clamp(round(pos_size / $50), min=2x, max=20x)
margin   = pos_size / leverage
```

### Hard Caps
| Parameter | Value |
|---|---|
| Max margin per trade | $50 |
| Minimum position size | $20 |
| Leverage range | 2x – 20x (auto-calculated) |
| Max equity risk per trade | 1.5% |

### Position Adjustments
| Condition | Adjustment |
|---|---|
| Sniper mode (Kill-Switch bypass) | Position × 0.5 (half-size) |
| Greed mode (FNG ≥ 70) | Position × 0.60 |
| Insufficient balance | Trade blocked with Telegram warning |

### Pre-Trade Viability Gate
Before any trade opens, both conditions must pass:

| Check | Minimum |
|---|---|
| Net profit at TP1 (after 0.12% round-trip fee) | ≥ $1.50 |
| Risk/Reward ratio at TP1 | ≥ 1.8 |

If either check fails → trade is **rejected**.

---

## Equity Formula (Immutable)
```
Equity = $200 (starting) + Realized PnL + Floating PnL
```

---

*Platform: Bitget Virtual/Paper Trading | Stack: Python bot → Node.js API → React Dashboard*
