# 🤖 Adaptive Sniper Bot — פרמטרים ואסטרטגיה

> **עדכון אחרון:** אפריל 2026 | **פלטפורמה:** Bitget Demo | **ארנק:** ~$182

---

## 🧭 אסטרטגיה כללית

| פרמטר | ערך | הסבר |
|---|---|---|
| אסטרטגיה | **Adaptive Sniper** | מבוסס על תצורת 26-27 מרץ הרווחית |
| ניקוד מינימום כניסה | **88 / 100** | סף כניסה גבוה — איכות על כמות |
| מקסימום עסקאות במקביל | **3** | פוקוס על עסקאות מנצחות |
| סיכון מקסימלי לעסקה | **1.5% מהון** | חוק הסיכון הבסיסי |
| RSI וטו LONG | **> 65** | לא רודפים פאמפים |
| RSI וטו SHORT | **< 28** | לא מורטים מטבעות Oversold |
| Anti-Chase EMA200 | **2.5%** | לא נכנסים רחוק מ-EMA200 |
| Volume Bypass | **× 1.5** | נפח גבוה מאפשר כניסה גם ליד EMA |

---

## 💹 מסלול Swing 🌊

> מסלול ראשי — סבלני, מינוף נמוך, RR גבוה

| פרמטר | ערך |
|---|---|
| מינוף | 3× |
| נפח מינימום | $10M / 24h |
| Trailing Stop | 1.5% מהשיא |
| Trailing Activation | 2% רווח |
| Partial Close (25%) | בעלייה של 5% |
| Stagnation Exit | 4 שעות ללא תזוזה ±0.5% |

### 🎯 יעדים דינמיים לפי Fear & Greed Index

| FNG | מצב שוק | Mode | SL | TP1 | TP | BE | RR-min |
|---|---|---|---|---|---|---|---|
| 0–25 | Extreme Fear | 🛡️ Conservative | 3% | 2% | 4% | 1.0% | 1.3 |
| 26–45 | Fear | ⚠️ Careful | 4% | 3.5% | 7% | 1.5% | 1.5 |
| 46–55 | Neutral | ⚖️ Standard | 5% | 5% | 10% | 2.5% | 2.0 |
| 56–75 | Greed | 🚀 Aggressive | 6% | 7% | 15% | 3.5% | 2.0 |
| 76–100 | Extreme Greed | 🌕 Moon | 8% | 10% | 25% | 5.0% | 2.5 |

> **BE** = Break-Even — מהרגע שהמחיר מגיע לאחוז זה, ה-SL עובר לנקודת הכניסה.  
> **TP1** = סגירת 50% מהפוזיציה + מעבר ל-Trailing.

---

## ⚡ מסלול Scalp

> כניסות מהירות — SL צמוד, יציאה תוך שעה

| פרמטר | ערך |
|---|---|
| מינוף | 10× |
| נפח מינימום | $50M / 24h |
| SL | 2% |
| TP1 | 4% |
| TP | 8% |
| BE Trigger | 50% מהדרך ל-TP1 |
| זמן מקסימום | 60 דקות (אחר כך כפור) |
| מקסימום עסקאות | 2 במקביל |

---

## 💥 High-Velocity (Rocket / Cliff)

> נר 5 דקות פיצוצי — כניסה מהירה מאוד

| פרמטר | ערך |
|---|---|
| תנועה מינימום בנר | 2.5% |
| Volume | × 3 ממוצע |
| RSI Overbought | > 70 (לSHORT) |
| SL | 2.5% |
| TP | 3% |
| BE Trigger | 1.5% |
| Trailing | 1.5% מהשיא |
| מינוף | 10× |
| מרג'ין | $50 |
| זמן מקסימום | 30 דקות |
| מקסימום עסקאות | 2 במקביל |

---

## 🎯 Precision Hunter Mode

> מופעל כשהשוק לחוץ — FNG ≥ 70 או קפיצה > 15% ב-24h

| פרמטר | ערך |
|---|---|
| FNG Trigger | ≥ 70 (Greed) |
| 24h Pump Trigger | > 15% |
| מינימום RR | 1:3 |
| TP1 | שווה ל-SL (1:1) |
| TP | שלוש פעמים SL (1:3) |
| TP1 מפעיל BE | ✅ אוטומטי |

---

## 🧨 Sniper Exception (Kill-Switch Override)

> מאפשר כניסה גם כשFNG נמוך מאוד אם הסיגנל חזק מספיק

| פרמטר | ערך |
|---|---|
| ניקוד מינימום | 92 / 100 |
| מרחק מ-EMA200 | < 5% |
| Volume | ≥ 2.5× ממוצע |
| גודל פוזיציה | 50% מהרגיל (Half-Size) |

---

## 🛑 מנגנוני הגנה

### Kill-Switch
- **מופעל** כש-FNG < 25 (Extreme Fear) **ו-**BTC BEAR
- **מבוטל** כש-FNG עולה מעל 25 **או** BTC BULL (Hunter Mode)
- בזמן Kill-Switch: רק עסקאות Sniper Exception מותרות

### BTC Parabolic Bull Filter
- BTC 4H > EMA200 **ו-** RSI(1H) > 60 → **חסימת כל SHORTs**
- Cache: 15 דקות

### Circuit Breaker יומי
- הפסד > $30 ביום → עצירת כל הסריקות עד למחרת

### Slow-Movers Blacklist
- `TRX/USDT`, `ADA/USDT` — אסורים לסחר בכל תנאי

---

## 📊 מערכת הניקוד (Scoring v3 + Pre-Breakout)

| מרכיב | ניקוד מקסימום |
|---|---|
| Trend (EMA200) | 25 |
| MACD | 15 |
| RSI | 10 |
| Bollinger Bands | 20 |
| Volume | 30 |
| Flag Pattern | +15 |
| Fair Value Gap (FVG) | +10 |
| BB Squeeze | +12 |
| Volume Buildup | +8 |
| RSI Divergence | +10 |
| Fear & Greed | ±5 |
| **סה"כ (capped)** | **100** |

> ניקוד גולמי מקסימלי: 155 — מוגבל ל-100 כדי שלא לעוות את הסף

---

## 🔐 Stagnation Exit

> מגן מפני עסקאות שנתקעות ולא זזות

| פרמטר | ערך |
|---|---|
| זמן מינימום | 4 שעות מהכניסה |
| טווח "קפוא" | ±0.5% מהכניסה |
| Phase | Initial בלבד (לא Trailing) |
| חל על | Swing / Breakout — לא Scalp / Cliff |

---

## 💰 ניהול כסף

| פרמטר | ערך |
|---|---|
| ארנק | ~$182 |
| מרג'ין לעסקת Swing | $15 (מוגבל $5–$50) |
| סיכון לעסקה | 1.5% מהון |
| Greed Filter | פוזיציה × 60% כש-FNG > 70 |
| Weekly Profit Target | $50 |

---

## ⏰ מחזורי סריקה

| סורק | תדירות | הערות |
|---|---|---|
| Major Watch (8 מטבעות) | כל 5 דקות | BTC/ETH/SOL/XRP/BNB/LINK/NEAR/FET |
| Top 10 Breakout | כל 10 דקות | 20 מטבעות מובילים |
| High-Velocity (Rocket/Cliff) | כל 2 דקות | נרות 5m פיצוציים |
| Scalp Scanner | כל 5–10 דקות | רק כשFNG < 25 |
| Fear & Greed | כל שעה | Alternative.me API |
| BTC Parabolic Cache | כל 15 דקות | לבדיקת SHORT filter |
| Heartbeat | כל 30 דקות | עדכון עסקאות פעילות |

---

## 🔧 ערכי FNG Settings (ניתנים לשינוי)

> קובץ: `fng_settings.json`

| פרמטר | ערך נוכחי | הסבר |
|---|---|---|
| `extreme_fear` | 25 | מתחת לזה = Kill-Switch |
| `fear` | 30 | מתחת לזה = Hunter Mode |
| `greed` | 70 | מעל לזה = Greed Filter + Hunter |

---

## 📈 גרף בכל כניסה

כל עסקה שנפתחת שולחת גרף ל-Telegram עם:
- **72 נרות** של 1H (3 ימים)
- קווי **Entry / TP / SL**
- **EMA200** (כתום)
- **Bollinger Bands** (אפור)
- **FVG Zone** (כתום) אם קיים
- **Volume + RSI** בתחתית
- כפתור **❌ Close Position** לסגירה מיידית
