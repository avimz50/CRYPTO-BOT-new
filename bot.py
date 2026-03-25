import os
import io
import json
import ccxt
import telebot
import time
import threading
import pandas as pd
import pandas_ta as ta
from datetime import datetime, date, timedelta

# ── אזור זמן ישראל (UTC+2/+3 לפי שעון קיץ) ──
os.environ['TZ'] = 'Asia/Jerusalem'
time.tzset()

# ספריות גרף — fallback אם לא קיימות
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import mplfinance as mpf
    CHARTS_ENABLED = True
except ImportError:
    CHARTS_ENABLED = False
    print("mplfinance not available — charts disabled")

# --- הגדרות וחיבורים ---
exchange = ccxt.bitget({
    'apiKey': os.environ['BITGET_KEY'],
    'secret': os.environ['BITGET_SECRET'],
    'password': os.environ['BITGET_PW'],
    'enableRateLimit': True,
})

bot = telebot.TeleBot(os.environ['TELEGRAM_TOKEN'])
CHAT_ID = os.environ['CHAT_ID']

# רשימה קבועה לאסטרטגיית EMA בלבד
ENERGY_GEO = ['PAXG/USDT', 'POWR/USDT', 'HNT/USDT']

# נתיב לקובץ המועמדים החמים (לדאשבורד)
HOT_CANDIDATES_FILE = 'artifacts/bot-dashboard/public/hot_candidates.json'
DASHBOARD_URL       = 'https://95de2b83-78fc-4e84-b223-d602409dd064-00-ri9mebduqgwx.kirk.replit.dev/bot-dashboard'

# --- פרמטרי מינוף (דמו) ---
LEVERAGE       = 10          # מינוף 10x
MARGIN         = 50          # בטחון ($) לכל עסקה
POSITION_SIZE  = MARGIN * LEVERAGE   # $500 נשלט

# רשימה למעקב אחרי עסקאות דמו פתוחות
active_trades = []

# מעקב אחרי עסקאות שנסגרו היום
daily_stats = {
    'wins': 0,
    'losses': 0,
    'total_pnl': 0.0,   # רווח/הפסד כולל ($) עם מינוף
    'date': date.today()
}

# שמירת תאריך הדוח האחרון שנשלח
last_daily_report_date = None

def send_msg(text):
    try:
        bot.send_message(CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_data(symbol, timeframe='1h', limit=250):
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    return df

def generate_chart(df, symbol, entry, sl, tp, direction='LONG'):
    """מייצר גרף נרות עם EMA200, RSI, ווליום וקווי SL/Entry/TP.
       direction='LONG' → ירוק | 'SHORT' → אדום.
       מחזיר BytesIO או None אם נכשל."""
    if not CHARTS_ENABLED:
        return None
    try:
        # ── צבעי נרות לפי כיוון ──
        if direction == 'SHORT':
            candle_up   = '#ef5350'   # אדום — SHORT
            candle_down = '#b71c1c'
            accent      = '#ff5252'   # accent לקו TP/SL
            tp_color    = '#e74c3c'   # TP — מטרה לירידה
            sl_color    = '#2ecc71'   # SL — עצירה מעל
            dir_label   = '🐻 BEARISH SHORT'
        else:
            candle_up   = '#26a69a'   # ירוק — LONG
            candle_down = '#ef5350'
            accent      = '#00e676'
            tp_color    = '#2ecc71'   # TP — מטרה לעלייה
            sl_color    = '#e74c3c'   # SL — עצירה מתחת
            dir_label   = '🐂 BULLISH LONG'

        # ── נתונים: 72 נרות אחרונים (3 ימים ב-1H) ──
        plot_df = df.tail(72).copy()
        plot_df.index = pd.to_datetime(plot_df['timestamp'], unit='ms')
        plot_df = plot_df[['open', 'high', 'low', 'close', 'volume']].rename(
            columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                     'close': 'Close', 'volume': 'Volume'}
        )

        # ── אינדיקטורים ──
        ema200_vals = ta.ema(df['close'], length=200).tail(72).values
        rsi_vals    = ta.rsi(df['close'], length=14).tail(72).values

        rsi_30 = [30] * 72
        rsi_70 = [70] * 72

        apds = [
            mpf.make_addplot(ema200_vals, color='#f5a623', width=1.8,
                             label='EMA 200'),
            mpf.make_addplot(rsi_vals, panel=2, color='#9b59b6',
                             ylabel='RSI', ylim=(0, 100)),
            mpf.make_addplot(rsi_30, panel=2, color='#27ae60',
                             linestyle='--', width=0.8),
            mpf.make_addplot(rsi_70, panel=2, color='#e74c3c',
                             linestyle='--', width=0.8),
        ]

        # ── עיצוב כהה עם צבע לפי כיוון ──
        BG = '#0d1117'
        mc = mpf.make_marketcolors(
            up=candle_up, down=candle_down,
            wick={'up': candle_up, 'down': candle_down},
            volume={'up': candle_up, 'down': candle_down},
            edge='inherit'
        )
        style = mpf.make_mpf_style(
            marketcolors=mc,
            facecolor=BG, figcolor=BG,
            gridcolor='#21262d', gridstyle='-',
            y_on_right=True,
            rc={'axes.labelcolor': '#c9d1d9',
                'xtick.color': '#8b949e',
                'ytick.color': '#8b949e',
                'text.color': '#c9d1d9'}
        )

        buf = io.BytesIO()
        fig, axes = mpf.plot(
            plot_df,
            type='candle',
            style=style,
            addplot=apds,
            volume=True,
            panel_ratios=(4, 1, 2),
            figsize=(13, 9),
            title=f'\n  {symbol}  ·  1H  ·  {dir_label}',
            returnfig=True,
            tight_layout=True
        )

        # ── אזורי רווח / הפסד לפי כיוון ──
        ax = axes[0]

        if direction == 'SHORT':
            # SHORT: רווח (ירוק) מתחת לכניסה → TP | הפסד (אדום) מעל כניסה → SL
            ax.axhspan(tp,    entry, alpha=0.10, color='forestgreen', zorder=0)
            ax.axhspan(entry, sl,    alpha=0.10, color='crimson',     zorder=0)
            # קווי גבול האזורים
            ax.axhline(tp,    color='forestgreen', linewidth=0.6, linestyle=':')
            ax.axhline(sl,    color='crimson',     linewidth=0.6, linestyle=':')
        else:
            # LONG: רווח (ירוק) מעל כניסה → TP | הפסד (אדום) מתחת כניסה → SL
            ax.axhspan(entry, tp,    alpha=0.10, color='forestgreen', zorder=0)
            ax.axhspan(sl,    entry, alpha=0.10, color='crimson',     zorder=0)
            ax.axhline(tp,    color='forestgreen', linewidth=0.6, linestyle=':')
            ax.axhline(sl,    color='crimson',     linewidth=0.6, linestyle=':')

        # ── קווים ראשיים: Entry / TP / SL ──
        ax.axhline(entry, color='#3498db',    linewidth=1.8,
                   linestyle='--', label=f'🔵 Entry  {entry:.4f}')
        ax.axhline(tp,    color='forestgreen', linewidth=1.8,
                   linestyle='--', label=f'✅ TP     {tp:.4f}')
        ax.axhline(sl,    color='crimson',     linewidth=1.8,
                   linestyle='--', label=f'🛑 SL     {sl:.4f}')

        # ── תוויות טקסט על ציר Y ──
        y_min, y_max = ax.get_ylim()
        x_pos = ax.get_xlim()[1] * 0.98
        for price_lvl, label_txt, col in [
            (tp,    'TP',    'forestgreen'),
            (entry, 'ENTRY', '#3498db'),
            (sl,    'SL',    'crimson'),
        ]:
            if y_min < price_lvl < y_max:
                ax.text(x_pos, price_lvl, f' {label_txt}', color=col,
                        fontsize=7.5, fontweight='bold', va='center',
                        bbox=dict(facecolor=BG, edgecolor=col,
                                  boxstyle='round,pad=0.2', alpha=0.8))

        ax.legend(loc='upper left', fontsize=8,
                  facecolor='#161b22', labelcolor='#c9d1d9',
                  edgecolor='#30363d')

        fig.savefig(buf, format='png', dpi=120,
                    bbox_inches='tight', facecolor=BG)
        plt.close(fig)
        buf.seek(0)
        return buf

    except Exception as e:
        print(f"Chart error: {e}")
        return None

def send_chart_alert(chart_buf, symbol, caption):
    """שולח גרף עם כיתוב קצר, ואז את ההודעה המלאה בנפרד."""
    try:
        if chart_buf:
            short = f"📊 *{symbol}* — גרף 1H עם SL/Entry/TP"
            bot.send_photo(CHAT_ID, chart_buf, caption=short,
                           parse_mode='Markdown')
        send_msg(caption)
    except Exception as e:
        print(f"Send chart error: {e}")
        send_msg(caption)

# --- שלב 1+2: משפך — מועמדים חמים ---

def get_hot_candidates():
    """
    שלב 1: שליפת כל זוגות USDT מ-Bitget (ווליום $1M+)
    שלב 2: Top 15 Gainers (LONG) + Top 15 Losers (SHORT)
    מחזיר: (gainers_list, losers_list)
    """
    try:
        print("Fetching all tickers for hot candidates...")
        tickers = exchange.fetch_tickers()

        gainers, losers = [], []
        for symbol, ticker in tickers.items():
            if not symbol.endswith('/USDT'):
                continue
            change_pct = ticker.get('percentage', None)
            volume_usd = ticker.get('quoteVolume', 0) or 0
            last_price = ticker.get('last', 0) or 0

            if change_pct is None or volume_usd < 1_000_000 or last_price <= 0:
                continue

            row = {
                'symbol':     symbol,
                'change_pct': round(change_pct, 2),
                'volume_usd': round(volume_usd),
                'price':      last_price
            }
            if change_pct > 0:
                gainers.append(row)
            elif change_pct < 0:
                losers.append(row)

        gainers.sort(key=lambda x: x['change_pct'], reverse=True)
        losers.sort(key=lambda x: x['change_pct'])   # שלילי ביותר קודם

        top_gainers = gainers[:15]
        top_losers  = losers[:15]

        # שמירה לדאשבורד (גיינרים)
        data = {
            'updated':    datetime.now().strftime('%H:%M:%S'),
            'count':      len(top_gainers),
            'candidates': top_gainers
        }
        os.makedirs(os.path.dirname(HOT_CANDIDATES_FILE), exist_ok=True)
        with open(HOT_CANDIDATES_FILE, 'w') as f:
            json.dump(data, f)

        print(f"Gainers: {[c['symbol'] for c in top_gainers]}")
        print(f"Losers:  {[c['symbol'] for c in top_losers]}")
        return top_gainers, top_losers

    except Exception as e:
        print(f"Hot candidates error: {e}")
        return [], []

# ═══════════════════════════════════════════════════════════════
# מנוע ניקוד מקצועי — Professional Scoring System
# ═══════════════════════════════════════════════════════════════

MIN_SCORE = 75   # סף מינימום לפתיחת עסקה

def score_symbol(df_1h, df_15m, symbol, direction='LONG'):
    """
    מערכת ניקוד מקצועית 0–100 נקודות.

    direction='LONG'  → גיינרים, מחפש עלייה
    direction='SHORT' → לוזרים,  מחפש ירידה

    ניקוד:
      Trend     (30): EMA200 ב-1H (+20) + ב-15m (+10)
      Momentum  (25): MACD מעל/מתחת Signal (+15) + Histogram מתחזק (+10)
      RSI       (20): Sweet-spot (+20), Acceptable (+10)
      BB+Volume (25): מחיר מעל/מתחת MidBB (+15) + Volume ×1.2 (+10)

    מחזיר: (score: int, breakdown: str, atr: float)
    """
    score = 0
    parts = []

    try:
        close_1h = df_1h['close']

        # ── אינדיקטורים 1H ──
        ema200_1h = ta.ema(close_1h, length=200)
        macd_df   = ta.macd(close_1h, fast=12, slow=26, signal=9)
        rsi_s     = ta.rsi(close_1h, length=14)
        bb_df     = ta.bbands(close_1h, length=20, std=2)
        atr_s     = ta.atr(df_1h['high'], df_1h['low'], close_1h, length=14)

        # ── EMA200 על 15m ──
        ema200_15 = ta.ema(df_15m['close'], length=200)

        if any(v is None for v in [ema200_1h, macd_df, rsi_s, bb_df, atr_s, ema200_15]):
            print(f"  [{symbol}] indicator calc failed")
            return 0, "indicator error", 0

        price      = close_1h.iloc[-1]
        ema200_v   = ema200_1h.iloc[-1]
        ema200_15v = ema200_15.iloc[-1]

        # MACD — איתור עמודות (pandas_ta משתנה בשמות לפי פרמטרים)
        macd_col = next(c for c in macd_df.columns if c.startswith('MACD_'))
        sig_col  = next(c for c in macd_df.columns if c.startswith('MACDs_'))
        hist_col = next(c for c in macd_df.columns if c.startswith('MACDh_'))
        macd_v   = macd_df[macd_col].iloc[-1]
        sig_v    = macd_df[sig_col].iloc[-1]
        hist_v   = macd_df[hist_col].iloc[-1]
        hist_p   = macd_df[hist_col].iloc[-2]

        rsi_v    = rsi_s.iloc[-1]

        # Bollinger Middle
        bb_mid_col = next(c for c in bb_df.columns if 'BBM_' in c)
        bb_mid     = bb_df[bb_mid_col].iloc[-1]

        atr_v    = atr_s.iloc[-1]

        vol_curr = df_1h['volume'].iloc[-1]
        vol_avg  = df_1h['volume'].iloc[-11:-1].mean()
        vol_rat  = vol_curr / vol_avg if vol_avg > 0 else 0

        if any(pd.isna(v) for v in [ema200_v, ema200_15v, macd_v, sig_v,
                                      hist_v, hist_p, rsi_v, bb_mid, atr_v]):
            print(f"  [{symbol}] NaN in indicators")
            return 0, "NaN values", 0

        # ════════════════════════════════
        # 1. TREND — 30 נקודות
        # ════════════════════════════════
        if direction == 'LONG':
            t1h  = price > ema200_v
            t15m = price > ema200_15v
        else:
            t1h  = price < ema200_v
            t15m = price < ema200_15v

        t_pts = 0
        if t1h:
            t_pts += 20
        if t1h and t15m:
            t_pts += 10
        score += t_pts
        parts.append(f"Trend={t_pts}/30")
        print(f"  [{symbol}] {direction} | Trend={t_pts} "
              f"(1H={'✓' if t1h else '✗'} 15m={'✓' if t15m else '✗'})")

        # ════════════════════════════════
        # 2. MOMENTUM (MACD) — 25 נקודות
        # ════════════════════════════════
        if direction == 'LONG':
            macd_ok = macd_v > sig_v        # MACD מעל Signal
            hist_ok = hist_v > hist_p       # Histogram מתחזק (פחות שלילי / יותר חיובי)
        else:
            macd_ok = macd_v < sig_v
            hist_ok = hist_v < hist_p       # Histogram מתחזק לכיוון שלילי

        m_pts = 0
        if macd_ok:
            m_pts += 15
        if hist_ok:
            m_pts += 10
        score += m_pts
        parts.append(f"MACD={m_pts}/25")
        print(f"  [{symbol}] {direction} | MACD={m_pts} "
              f"(aligned={'✓' if macd_ok else '✗'} hist={'✓' if hist_ok else '✗'})")

        # ════════════════════════════════
        # 3. RSI STRENGTH — 20 נקודות
        # ════════════════════════════════
        if direction == 'LONG':
            rsi_ideal = 50 <= rsi_v <= 65   # Sweet spot: מומנטום בלי overbought
            rsi_ok    = 45 <= rsi_v <= 70   # Acceptable
        else:
            rsi_ideal = 35 <= rsi_v <= 50   # Momentum short: לא oversold עדיין
            rsi_ok    = 30 <= rsi_v <= 55

        r_pts = 0
        if rsi_ideal:
            r_pts = 20
        elif rsi_ok:
            r_pts = 10
        score += r_pts
        parts.append(f"RSI={r_pts}/20(={rsi_v:.0f})")
        print(f"  [{symbol}] {direction} | RSI={r_pts} (rsi={rsi_v:.1f})")

        # ════════════════════════════════
        # 4. BOLLINGER + VOLUME — 25 נקודות
        # ════════════════════════════════
        if direction == 'LONG':
            bb_ok = price > bb_mid
        else:
            bb_ok = price < bb_mid

        vol_ok = vol_rat >= 1.2

        b_pts = 0
        if bb_ok:
            b_pts += 15
        if vol_ok:
            b_pts += 10
        score += b_pts
        parts.append(f"BB+Vol={b_pts}/25")
        print(f"  [{symbol}] {direction} | BB={b_pts} "
              f"(bb={'✓' if bb_ok else '✗'} vol×{vol_rat:.1f}={'✓' if vol_ok else '✗'})")

        breakdown = " | ".join(parts) + f"  →  TOTAL={score}/100"
        print(f"  [{symbol}] {direction} SCORE={score}/100 {'🟢 SIGNAL!' if score >= MIN_SCORE else '🔴 skip'}")
        return score, breakdown, atr_v

    except Exception as e:
        print(f"  [{symbol}] score error: {e}")
        return 0, str(e), 0

# --- ניהול עסקאות דמו ---

def get_direction_header(direction):
    if direction == 'SHORT':
        return "🐻 *BEARISH SHORT (מכירה)*\n🔴🔴 _הדוב מוחץ למטה!_ 🔴🔴"
    return "🐂 *BULLISH LONG (קנייה)*\n🟢🟢 _השור נוגח למעלה!_ 🟢🟢"

def get_momentum_tip(direction):
    if direction == 'SHORT':
        return "📉 _השוק נחלש — מנצלים את הירידה._"
    return "🌊 _המומנטום חיובי — רוכבים על הגל._"

def _pnl_on_half(dist_pct):
    """P&L ($) על חצי פוזיציה ($250) לפי % מרחק מהכניסה."""
    return round(POSITION_SIZE / 2 * dist_pct / 100, 2)

def open_demo_trade(symbol, price, reason, df_1h=None,
                    direction='LONG', score=0, atr=0):
    """
    פותח עסקת דמו עם ניהול סיכון ATR דינמי (1:3 RR).
    אם ATR=0 → fallback לפרמטרים קבועים.
    """
    # ── SL/TP דינמי מבוסס ATR ──
    if atr and atr > 0:
        sl_dist = min(1.5 * atr, price * 0.04)   # מקסימום 4% מהמחיר
    else:
        sl_dist = price * 0.03                    # fallback: 3%

    tp_dist  = 3.0 * sl_dist    # RR 1:3
    be_dist  = 1.5 * sl_dist    # Break-Even: 1.5× SL dist
    tp1_dist = 2.0 * sl_dist    # TP1 (50%): 2× SL dist

    sl_pct  = round(sl_dist  / price * 100, 2)
    tp_pct  = round(tp_dist  / price * 100, 2)
    be_pct  = round(be_dist  / price * 100, 2)
    tp1_pct = round(tp1_dist / price * 100, 2)

    if direction == 'LONG':
        sl_price  = price - sl_dist
        tp_price  = price + tp_dist
        be_price  = price + be_dist
        tp1_price = price + tp1_dist
    else:   # SHORT
        sl_price  = price + sl_dist
        tp_price  = price - tp_dist
        be_price  = price - be_dist
        tp1_price = price - tp1_dist

    # ── P&L ──
    tp1_pnl    = _pnl_on_half(tp1_pct)
    tp2_pnl    = _pnl_on_half(tp_pct)
    max_profit = round(tp1_pnl + tp2_pnl, 2)
    sl_loss    = round(POSITION_SIZE * sl_pct / 100, 2)

    trade = {
        'symbol':        symbol,
        'entry':         price,
        'sl':            sl_price,
        'tp':            tp_price,
        'tp1':           tp1_price,
        'be_lvl':        be_price,
        'sl_pct':        sl_pct,
        'tp_pct':        tp_pct,
        'direction':     direction,
        'phase':         'initial',
        'be_triggered':  False,
        'tp1_triggered': False,
        'tp1_pnl':       0.0,
        'peak_price':    price,   # LONG: max; SHORT: min
        'trailing_sl':   None,
        'score':         score,
        'atr':           round(atr, 6),
    }
    active_trades.append(trade)

    dir_header = get_direction_header(direction)
    tip        = get_momentum_tip(direction)
    emoji      = "🟢" if direction == 'LONG' else "🔴"
    score_bar  = "█" * (score // 10) + "░" * (10 - score // 10)

    msg  = f"{dir_header}\n\n"
    msg += f"{'─' * 26}\n"
    msg += f"{emoji} *Professional Scoring System*\n"
    msg += f"מטבע: `{symbol}`\n"
    msg += f"פירוט: _{reason}_\n\n"
    msg += f"*ניקוד איתות: {score}/100*\n"
    msg += f"`{score_bar}` {'🟢 STRONG' if score>=85 else '🟡 GOOD'}\n\n"
    msg += f"מחיר כניסה: `{price:.6g}`\n"
    msg += f"🛑 SL ({'-' if direction=='LONG' else '+'}{sl_pct}%):  `{sl_price:.6g}` ← ATR×1.5\n"
    msg += f"🔒 BE ({'+' if direction=='LONG' else '-'}{be_pct}%):  `{be_price:.6g}` ← SL→כניסה\n"
    msg += f"🎯 TP1 ({'+' if direction=='LONG' else '-'}{tp1_pct}%): `{tp1_price:.6g}` ← סגירת 50%\n"
    msg += f"🎯 TP  ({'+' if direction=='LONG' else '-'}{tp_pct}%): `{tp_price:.6g}` ← RR 1:3\n\n"
    msg += f"{'─' * 26}\n"
    msg += f"💼 *Leverage: {LEVERAGE}x (Isolated)*\n"
    msg += f"💰 בטחון: ${MARGIN} · נשלט: ${POSITION_SIZE}\n"
    msg += f"📈 מקסימום רווח: *+${max_profit}*\n"
    msg += f"📉 מקסימום הפסד: *-${sl_loss}*\n\n"
    msg += tip

    chart_buf = generate_chart(df_1h, symbol, price, sl_price, tp_price, direction) \
                if df_1h is not None else None
    send_chart_alert(chart_buf, symbol, msg)
    print(f"Trade opened: {symbol} {direction} @ {price:.6g} | SL={sl_price:.6g} TP={tp_price:.6g} | Score={score}")


def track_trades():
    """
    בודק כל עסקה פעילה כל 60 שניות.
    תומך ב-LONG וב-SHORT.
    """
    global active_trades, daily_stats

    if daily_stats['date'] != date.today():
        daily_stats = {'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'date': date.today()}

    half = POSITION_SIZE / 2   # $250 — חצי פוזיציה לאחר TP1

    for trade in active_trades[:]:
        try:
            ticker        = exchange.fetch_ticker(trade['symbol'])
            current_price = ticker['last']
            entry         = trade['entry']
            sym           = trade['symbol']
            direction     = trade.get('direction', 'LONG')

            # helpers: "profit direction" — True כאשר המחיר זזה לכיוון הרצוי
            def profit_dir(p):
                return p >= entry if direction == 'LONG' else p <= entry

            def sl_hit(p):
                return p <= trade['sl'] if direction == 'LONG' else p >= trade['sl']

            def tp_full_hit(p):
                return p >= trade['tp'] if direction == 'LONG' else p <= trade['tp']

            def tp1_hit(p):
                return p >= trade['tp1'] if direction == 'LONG' else p <= trade['tp1']

            def be_hit(p):
                return p >= trade['be_lvl'] if direction == 'LONG' else p <= trade['be_lvl']

            # ════════════════════════════════════════════
            # שלב INITIAL — פוזיציה מלאה $500
            # ════════════════════════════════════════════
            if trade['phase'] == 'initial':

                # 1. Break Even
                if not trade['be_triggered'] and be_hit(current_price):
                    trade['sl']           = entry
                    trade['be_triggered'] = True
                    be_pct = trade['sl_pct']
                    send_msg(
                        f"🔒 *Break Even מופעל — {sym}*\n"
                        f"מחיר: `{current_price:.6g}` ({'+' if direction=='LONG' else '-'}{be_pct}%)\n"
                        f"SL הועבר לכניסה: `{entry:.6g}`\n"
                        f"💼 {LEVERAGE}x Isolated · ההון מוגן!"
                    )

                # 2. TP1 — סגור 50%, הפעל Trailing
                if tp1_hit(current_price):
                    dist_pct  = abs(current_price - entry) / entry * 100
                    tp1_pnl   = _pnl_on_half(dist_pct)
                    tp1_pct_r = round(tp1_pnl / MARGIN * 100, 1)
                    trade['tp1_triggered'] = True
                    trade['tp1_pnl']       = tp1_pnl
                    trade['phase']         = 'trailing'
                    trade['peak_price']    = current_price
                    if direction == 'LONG':
                        trade['trailing_sl'] = current_price * 0.98
                    else:
                        trade['trailing_sl'] = current_price * 1.02
                    daily_stats['total_pnl'] += tp1_pnl
                    send_msg(
                        f"🎯 *TP1 הושג — {sym}!*\n"
                        f"מחיר: `{current_price:.6g}` | {direction}\n"
                        f"50% נסגרו · 📈 רווח נעול: *+${tp1_pnl} (+{tp1_pct_r}%)*\n"
                        f"💼 {LEVERAGE}x · שאר 50% ($250) בטריילינג 2%\n"
                        f"📍 Trailing SL: `{trade['trailing_sl']:.6g}`\n"
                        f"📈 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    continue

                # 3. SL נגע
                if sl_hit(current_price):
                    if trade['be_triggered']:
                        daily_stats['losses'] += 1
                        send_msg(
                            f"🔒 *Break Even — יצאנו ב-{sym}*\n"
                            f"מחיר: `{current_price:.6g}` | כניסה: `{entry:.6g}`\n"
                            f"*ללא הפסד · ההון נשמר*\n"
                            f"💼 {LEVERAGE}x Isolated\n"
                            f"📊 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                        )
                    else:
                        loss     = round(POSITION_SIZE * trade['sl_pct'] / 100, 2)
                        loss_pct = round(loss / MARGIN * 100, 1)
                        daily_stats['losses']    += 1
                        daily_stats['total_pnl'] -= loss
                        send_msg(
                            f"🛑 *SL נגע — {sym}*\n"
                            f"כניסה: `{entry:.6g}` → SL: `{trade['sl']:.6g}`\n"
                            f"📉 *הפסד: -${loss} (-{loss_pct}% על מרג'ין)*\n"
                            f"💼 {LEVERAGE}x Isolated · בטחון: ${MARGIN}\n"
                            f"📉 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                        )
                    active_trades.remove(trade)

            # ════════════════════════════════════════════
            # שלב TRAILING — 50% פוזיציה נותרת ($250)
            # ════════════════════════════════════════════
            elif trade['phase'] == 'trailing':

                # עדכן שיא/שפל ו-Trailing SL
                if direction == 'LONG':
                    if current_price > trade['peak_price']:
                        trade['peak_price']  = current_price
                        trade['trailing_sl'] = round(current_price * 0.98, 8)
                else:
                    if current_price < trade['peak_price']:
                        trade['peak_price']  = current_price
                        trade['trailing_sl'] = round(current_price * 1.02, 8)

                # TP מלא — סגור שאר 50%
                if tp_full_hit(current_price):
                    dist_pct = abs(current_price - entry) / entry * 100
                    tp_pnl   = _pnl_on_half(dist_pct)
                    total    = round(trade['tp1_pnl'] + tp_pnl, 2)
                    daily_stats['wins']      += 1
                    daily_stats['total_pnl'] += tp_pnl
                    send_msg(
                        f"✅ *TP מלא הושג — {sym}!* 🎉\n"
                        f"מחיר: `{current_price:.6g}` | {direction}\n"
                        f"שאר 50% נסגרו: 📈 *+${tp_pnl}*\n"
                        f"TP1 + TP סה\"כ: 📈 *+${total}*\n"
                        f"💼 {LEVERAGE}x Isolated\n"
                        f"📈 סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    active_trades.remove(trade)

                # Trailing Stop נגע
                elif sl_hit(current_price) or \
                     (direction == 'LONG' and trade['trailing_sl'] and current_price <= trade['trailing_sl']) or \
                     (direction == 'SHORT' and trade['trailing_sl'] and current_price >= trade['trailing_sl']):
                    dist_pct = abs(current_price - entry) / entry * 100
                    sign     = 1 if profit_dir(current_price) else -1
                    half_pnl = round(sign * _pnl_on_half(dist_pct), 2)
                    total    = round(trade['tp1_pnl'] + half_pnl, 2)
                    icon     = "📈" if half_pnl >= 0 else "📉"
                    if half_pnl >= 0:
                        daily_stats['wins'] += 1
                    else:
                        daily_stats['losses'] += 1
                    daily_stats['total_pnl'] += half_pnl
                    ref_price = trade['peak_price']
                    send_msg(
                        f"📍 *Trailing Stop נגע — {sym}*\n"
                        f"{'שיא' if direction=='LONG' else 'שפל'}: `{ref_price:.6g}` → יציאה: `{current_price:.6g}`\n"
                        f"50% נסגרו: {icon} *{half_pnl:+}$*\n"
                        f"TP1 + Trailing סה\"כ: {icon} *{total:+}$*\n"
                        f"💼 {LEVERAGE}x Isolated\n"
                        f"{icon} סה\"כ היום: ${round(daily_stats['total_pnl'], 2):+}"
                    )
                    active_trades.remove(trade)

        except Exception as e:
            print(f"Track error {trade.get('symbol','?')}: {e}")

# --- דוח יומי ---

def check_api_connection():
    try:
        exchange.fetch_ticker('BTC/USDT')
        return True
    except Exception:
        return False

def send_daily_report():
    now = datetime.now().strftime('%d/%m/%Y %H:%M')
    total_closed = daily_stats['wins'] + daily_stats['losses']
    win_rate = (daily_stats['wins'] / total_closed * 100) if total_closed > 0 else 0
    api_ok = check_api_connection()
    api_status = "✅ פעיל" if api_ok else "❌ בעיה בחיבור!"

    if active_trades:
        trades_lines = ""
        for t in active_trades:
            trades_lines += f"  • `{t['symbol']}` — כניסה: {t['entry']:.4f}\n"
    else:
        trades_lines = "  _אין עסקאות פעילות כרגע_\n"

    total_pnl  = daily_stats.get('total_pnl', 0.0)
    pnl_sign   = "+" if total_pnl >= 0 else ""
    pnl_on_margin = round(total_pnl / MARGIN * 100, 1) if MARGIN else 0

    msg = f"📊 *דוח יומי — {now}*\n"
    msg += f"{'─' * 28}\n\n"
    msg += f"*📂 עסקאות פתוחות ({len(active_trades)}):*\n"
    msg += trades_lines + "\n"
    msg += f"*📈 תוצאות 24 שעות אחרונות:*\n"
    msg += f"  ✅ רווחים: {daily_stats['wins']}\n"
    msg += f"  ❌ הפסדים: {daily_stats['losses']}\n"
    msg += f"  🎯 אחוז הצלחה: {win_rate:.0f}%\n\n"
    msg += f"*💼 P&L כולל (10x Isolated):*\n"
    msg += f"  {pnl_sign}${total_pnl:.2f} ({pnl_sign}{pnl_on_margin}% על מרג'ין)\n\n"
    msg += f"*🔌 חיבור Bitget API:* {api_status}\n"
    msg += f"{'─' * 28}\n"
    msg += f"_הבוט פעיל ומסרוק כל שעה_ 🤖"

    send_msg(msg)
    print(f"Daily report sent at {now}")

def check_daily_report():
    global last_daily_report_date
    now = datetime.now()
    today = date.today()

    if now.hour == 9 and now.minute < 15:
        if last_daily_report_date != today:
            send_daily_report()
            last_daily_report_date = today

# --- פקודות טלגרם ---

@bot.message_handler(commands=['test'])
def handle_test(message):
    try:
        send_msg("🧪 *מריץ איתות טסט ל-BTC/USDT...*")
        df_1h  = get_data('BTC/USDT', timeframe='1h',  limit=250)
        df_15m = get_data('BTC/USDT', timeframe='15m', limit=250)
        price  = df_1h['close'].iloc[-1]
        atr    = ta.atr(df_1h['high'], df_1h['low'], df_1h['close'], length=14).iloc[-1]
        open_demo_trade(
            'BTC/USDT', price,
            'Trend=20/30 | MACD=25/25 | RSI=20/20 | BB+Vol=15/25 → TOTAL=80/100 [TEST]',
            df_1h, direction='LONG', score=80, atr=atr
        )
        print(f"Test signal sent for BTC/USDT at {price}")
    except Exception as e:
        send_msg(f"❌ שגיאה בטסט: {e}")

@bot.message_handler(commands=['status'])
def handle_status(message):
    if not active_trades:
        send_msg("📭 *אין עסקאות פעילות כרגע.*")
        return
    msg = f"📋 *עסקאות פעילות ({len(active_trades)}):*\n\n"
    for i, t in enumerate(active_trades, 1):
        phase_label = "🔄 Trailing" if t.get('phase') == 'trailing' else "📊 Initial"
        be_label    = " · 🔒 BE" if t.get('be_triggered') else ""
        tp1_label   = " · TP1✅" if t.get('tp1_triggered') else ""
        dirlab      = "🟢 LONG" if t.get('direction', 'LONG') == 'LONG' else "🔴 SHORT"
        score       = t.get('score', 0)
        msg += (
            f"*{i}. {t['symbol']}* {dirlab} · {phase_label}{be_label}{tp1_label}\n"
            f"   ניקוד: *{score}/100* | ATR: `{t.get('atr', 0):.6g}`\n"
            f"   כניסה: `{t['entry']:.6g}`\n"
            f"   🛑 SL: `{t['sl']:.6g}` | 🎯 TP: `{t['tp']:.6g}`\n"
            f"   🔒 BE: `{t['be_lvl']:.6g}` | 🎯 TP1: `{t['tp1']:.6g}`\n"
        )
        if t.get('phase') == 'trailing' and t.get('trailing_sl'):
            ref = "שיא" if t.get('direction', 'LONG') == 'LONG' else "שפל"
            msg += f"   📍 Trailing SL: `{t['trailing_sl']:.6g}` | {ref}: `{t['peak_price']:.6g}`\n"
        msg += "\n"
    msg += f"_לעדכון SL/TP: /update SYMBOL SL TP_\n"
    msg += f"_לסגירה ידנית: /close SYMBOL_"
    send_msg(msg)

@bot.message_handler(commands=['update'])
def handle_update(message):
    """
    שימוש: /update BTC 84000 95000
    או:    /update BTC sl=84000
    או:    /update BTC tp=95000
    """
    try:
        parts = message.text.strip().split()
        if len(parts) < 3:
            send_msg(
                "⚠️ *שימוש שגוי*\n\n"
                "פורמט: `/update SYMBOL SL TP`\n"
                "דוגמה: `/update BTC 84000 95000`\n\n"
                "לעדכון SL בלבד: `/update BTC sl=84000`\n"
                "לעדכון TP בלבד: `/update BTC tp=95000`"
            )
            return

        raw_sym = parts[1].upper()
        symbol  = raw_sym if '/' in raw_sym else f"{raw_sym}/USDT"

        # מציאת העסקה
        trade = next((t for t in active_trades if t['symbol'] == symbol), None)
        if not trade:
            symbols_list = ', '.join(f"`{t['symbol']}`" for t in active_trades) or "_אין_"
            send_msg(f"❌ לא נמצאה עסקה פתוחה עבור `{symbol}`\n\nפתוחות: {symbols_list}")
            return

        new_sl = trade['sl']
        new_tp = trade['tp']
        changes = []

        # פענוח פרמטרים: /update BTC 84000 95000  או sl=84000 tp=95000
        for arg in parts[2:]:
            arg_l = arg.lower()
            if arg_l.startswith('sl='):
                new_sl = float(arg_l.replace('sl=', ''))
                changes.append(f"SL → `{new_sl:.4f}`")
            elif arg_l.startswith('tp='):
                new_tp = float(arg_l.replace('tp=', ''))
                changes.append(f"TP → `{new_tp:.4f}`")
            else:
                # פוזיציה: ארגומנט 2 = SL, ארגומנט 3 = TP
                try:
                    val = float(arg)
                    if len(changes) == 0:
                        new_sl = val
                        changes.append(f"SL → `{new_sl:.4f}`")
                    else:
                        new_tp = val
                        changes.append(f"TP → `{new_tp:.4f}`")
                except ValueError:
                    pass

        # ולידציה: SL < מחיר כניסה < TP
        entry = trade['entry']
        if new_sl >= entry:
            send_msg(f"⚠️ SL ({new_sl}) חייב להיות *מתחת* למחיר הכניסה ({entry:.4f})")
            return
        if new_tp <= entry:
            send_msg(f"⚠️ TP ({new_tp}) חייב להיות *מעל* למחיר הכניסה ({entry:.4f})")
            return

        # עדכון
        old_sl, old_tp = trade['sl'], trade['tp']
        trade['sl'] = new_sl
        trade['tp'] = new_tp
        trade['sl_pct'] = round((entry - new_sl) / entry * 100, 2)
        trade['tp_pct'] = round((new_tp - entry) / entry * 100, 2)

        sl_pct = trade['sl_pct']
        tp_pct = trade['tp_pct']
        sl_pnl = round(POSITION_SIZE * sl_pct / 100, 2)
        tp_pnl = round(POSITION_SIZE * tp_pct / 100, 2)

        send_msg(
            f"✏️ *עסקה עודכנה — {symbol}*\n\n"
            f"{''.join(chr(10) + '  ' + c for c in changes)}\n\n"
            f"כניסה: `{entry:.4f}`\n"
            f"🛑 SL חדש: `{new_sl:.4f}` (-{sl_pct}% · סיכון: -${sl_pnl})\n"
            f"🎯 TP חדש: `{new_tp:.4f}` (+{tp_pct}% · פוטנציאל: +${tp_pnl})\n\n"
            f"💼 {LEVERAGE}x Isolated · בטחון: ${MARGIN}"
        )
        print(f"Trade updated: {symbol} SL={new_sl} TP={new_tp}")

    except Exception as e:
        send_msg(f"❌ שגיאה בעדכון: {e}")
        print(f"Update error: {e}")

@bot.message_handler(commands=['close'])
def handle_close(message):
    """סגירה ידנית של עסקה: /close BTC"""
    try:
        parts = message.text.strip().split()
        if len(parts) < 2:
            send_msg("⚠️ שימוש: `/close BTC` או `/close BTC/USDT`")
            return

        raw_sym = parts[1].upper()
        symbol  = raw_sym if '/' in raw_sym else f"{raw_sym}/USDT"

        trade = next((t for t in active_trades if t['symbol'] == symbol), None)
        if not trade:
            send_msg(f"❌ לא נמצאה עסקה פתוחה עבור `{symbol}`")
            return

        ticker        = exchange.fetch_ticker(symbol)
        current_price = ticker['last']
        entry         = trade['entry']

        # חישוב P&L בפועל
        if trade.get('tp1_triggered'):
            # חצי פוזיציה נסגרת עכשיו, חצי כבר נסגר ב-TP1
            half    = POSITION_SIZE / 2
            half_pnl = round(half * (current_price - entry) / entry * 100 / 100, 2)
            total   = round(trade.get('tp1_pnl', 0) + half_pnl, 2)
            pnl_str = f"TP1 + יציאה: *{'+' if total>=0 else ''}${total}*"
        else:
            pct     = (current_price - entry) / entry * 100
            pnl     = round(POSITION_SIZE * pct / 100, 2)
            pnl_str = f"P&L: *{'+' if pnl>=0 else ''}${pnl}* ({pct:+.2f}%)"

        active_trades.remove(trade)
        if current_price >= entry:
            daily_stats['wins'] += 1
        else:
            daily_stats['losses'] += 1

        send_msg(
            f"🚪 *סגירה ידנית — {symbol}*\n\n"
            f"כניסה: `{entry:.4f}` → יציאה: `{current_price:.4f}`\n"
            f"{pnl_str}\n"
            f"💼 {LEVERAGE}x Isolated\n"
            f"סה\"כ היום: ${round(daily_stats.get('total_pnl', 0), 2):+}"
        )
        print(f"Manual close: {symbol} at {current_price}")

    except Exception as e:
        send_msg(f"❌ שגיאה בסגירה: {e}")

@bot.message_handler(commands=['report'])
def handle_report(message):
    send_daily_report()

@bot.message_handler(commands=['ping'])
def handle_ping(message):
    now        = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    uptime_msg = f"🟢 *הבוט פעיל!*\n\n"
    uptime_msg += f"🕐 שעה: `{now}`\n"
    uptime_msg += f"📊 עסקאות פעילות: *{len(active_trades)}*\n"
    if active_trades:
        for t in active_trades:
            phase = "🔄 Trailing" if t.get('phase') == 'trailing' else "📊 Initial"
            uptime_msg += f"   • `{t['symbol']}` — {phase}\n"
    uptime_msg += f"\n📈 P&L היום: *${round(daily_stats.get('total_pnl', 0), 2):+}*\n"
    uptime_msg += f"✅ ניצחונות: {daily_stats.get('wins', 0)} · ❌ הפסדים: {daily_stats.get('losses', 0)}\n"
    uptime_msg += f"\n🖥 [פתח דאשבורד]({DASHBOARD_URL})\n"
    uptime_msg += f"_הסריקה הבאה בעוד פחות משעה_"
    send_msg(uptime_msg)

@bot.message_handler(commands=['dashboard'])
def handle_dashboard(message):
    send_msg(
        f"🖥 *דאשבורד הבוט*\n\n"
        f"[👉 לחץ כאן לפתיחת הדאשבורד]({DASHBOARD_URL})\n\n"
        f"_תראה שם: Top 15 מועמדים חמים, גיינרים, ווליום ועוד_"
    )

def start_telegram_polling():
    print("Telegram polling started...")
    while True:
        try:
            bot.polling(non_stop=True, timeout=30, long_polling_timeout=30)
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)

# --- לולאת מעקב עסקאות — Thread נפרד ---

def trade_monitor_loop():
    """
    רץ בThread נפרד.
    בודק SL / TP / BE / Trailing כל 60 שניות — ללא תלות בסריקה.
    """
    print("Trade monitor started — checking every 60s")
    while True:
        try:
            if active_trades:
                track_trades()
                check_daily_report()
        except Exception as e:
            print(f"Trade monitor error: {e}")
        time.sleep(60)

# --- לולאת סריקת איתותים — Thread נפרד ---

def _scan_batch(candidates, direction):
    """
    עוזר לסריקה: מריץ score_symbol על רשימת מועמדים.
    direction: 'LONG' או 'SHORT'
    מחזיר מספר האיתותים שנמצאו.
    """
    found = 0
    for candidate in candidates:
        symbol = candidate['symbol']
        if any(t['symbol'] == symbol for t in active_trades):
            continue
        try:
            df_1h  = get_data(symbol, timeframe='1h',  limit=250)
            df_15m = get_data(symbol, timeframe='15m', limit=250)
            price  = df_1h['close'].iloc[-1]

            print(f"Scoring {symbol} [{direction}] @ {price:.6g}")
            score, breakdown, atr = score_symbol(df_1h, df_15m, symbol, direction)

            if score >= MIN_SCORE:
                open_demo_trade(
                    symbol, price, breakdown,
                    df_1h, direction=direction,
                    score=score, atr=atr
                )
                found += 1

        except Exception as e:
            print(f"Error scanning {symbol}: {e}")
    return found


def scan_loop():
    """
    רץ בThread נפרד.
    סורק Top 15 Gainers (LONG) + Top 15 Losers (SHORT) פעם בשעה.
    מפעיל Professional Scoring System — מינימום 75 נקודות לאיתות.
    """
    print("Scan loop started — scanning every 60 minutes")
    while True:
        try:
            check_daily_report()
            now_str = datetime.now().strftime('%H:%M:%S')

            # ── שלב 1: שלוף גיינרים ולוזרים ──
            gainers, losers = get_hot_candidates()
            total_scanned   = len(gainers) + len(losers) + len(ENERGY_GEO)

            send_msg(
                f"🔍 *Professional Scoring Scan* — {now_str}\n"
                f"🟢 Gainers (LONG): *{len(gainers)}*  🔴 Losers (SHORT): *{len(losers)}*\n"
                f"📐 סף מינימום: *{MIN_SCORE}/100 נקודות*"
            )

            signals_found = 0

            # ── שלב 2: סריקת גיינרים — LONG ──
            signals_found += _scan_batch(gainers, 'LONG')

            # ── שלב 3: סריקת לוזרים — SHORT ──
            signals_found += _scan_batch(losers, 'SHORT')

            # ── שלב 4: רשימה קבועה — ENERGY_GEO (LONG בלבד) ──
            energy_candidates = [{'symbol': s} for s in ENERGY_GEO]
            signals_found += _scan_batch(energy_candidates, 'LONG')

            print(f"Scan done — {signals_found} signal(s) / {total_scanned} scanned")

            # ── סיכום סריקה ──
            now       = datetime.now().strftime('%H:%M')
            next_scan = (datetime.now() + timedelta(hours=1)).strftime('%H:%M')
            pnl_today = round(daily_stats.get('total_pnl', 0), 2)
            pnl_icon  = "📈" if pnl_today >= 0 else "📉"

            summary  = f"✅ *סריקה הושלמה — {now}*\n\n"
            summary += f"🔍 נסרקו: *{total_scanned}* מטבעות\n"
            summary += f"📊 איתותים שנמצאו: *{signals_found}*\n"
            summary += f"📊 עסקאות פעילות: *{len(active_trades)}*\n"
            if active_trades:
                for t in active_trades:
                    phase  = "🔄 Trailing" if t.get('phase') == 'trailing' else "📊 Initial"
                    dirlab = "🟢" if t.get('direction') == 'LONG' else "🔴"
                    score  = t.get('score', 0)
                    summary += f"   {dirlab} `{t['symbol']}` {phase} · Score {score}/100\n"
            summary += f"\n{pnl_icon} P&L היום: *${pnl_today:+}*\n"
            summary += f"⏰ סריקה הבאה: `{next_scan}`\n"
            summary += f"_📍 מעקב עסקאות פעיל כל 60 שניות_"
            send_msg(summary)

            print(f"Scan complete at {now}. Next scan at {next_scan}.")

        except Exception as e:
            print(f"Scan loop error: {e}")

        time.sleep(3600)

# --- הלולאה הראשית ---

def main():
    # Thread 1 — Telegram polling
    polling_thread = threading.Thread(target=start_telegram_polling, daemon=True)
    polling_thread.start()

    # Thread 2 — מעקב עסקאות כל 60 שניות
    monitor_thread = threading.Thread(target=trade_monitor_loop, daemon=True)
    monitor_thread.start()

    # Thread 3 — סריקת איתותים כל שעה
    scan_thread = threading.Thread(target=scan_loop, daemon=True)
    scan_thread.start()

    send_msg(
        "🤖 *הבוט התחיל ב-Replit!*\n\n"
        "⚙️ *מצב הלולאות:*\n"
        "🔍 סריקת איתותים: כל *60 דקות*\n"
        "📍 מעקב SL/TP:    כל *60 שניות*\n\n"
        "*פקודות:*\n"
        "/ping — מצב הבוט\n"
        "/dashboard — קישור לדאשבורד\n"
        "/test — איתות BTC מזויף\n"
        "/status — עסקאות פעילות\n"
        "/update BTC 84000 95000 — עדכון SL/TP\n"
        "/close BTC — סגירה ידנית\n"
        "/report — דוח יומי\n\n"
        f"🖥 [פתח דאשבורד]({DASHBOARD_URL})"
    )

    # Thread הראשי נשאר ער
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
