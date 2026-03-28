"""
Trading Bot — Audit Reporter
Builds a structured JSON audit report, saves locally, sends via Telegram,
and (optionally) uploads to Google Shared Drive.
"""

import os
import json
import io
import requests
from datetime import datetime


AUDIT_FILE     = 'audit_report.json'
FOLDER_ID      = os.environ.get('GDRIVE_FOLDER_ID', '')
SA_JSON_STR    = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON', '')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
CHAT_ID        = os.environ.get('CHAT_ID', '')
GEMINI_URL     = os.environ.get('AI_INTEGRATIONS_GEMINI_BASE_URL', '')
GEMINI_KEY     = os.environ.get('AI_INTEGRATIONS_GEMINI_API_KEY', '')


# ─────────────────────────────────────────────────────────────
# 1. REPORT BUILDER
# ─────────────────────────────────────────────────────────────

def build_audit_report(active_trades, wallet, closed_trades_log, starting=200.0):
    """Builds a structured JSON audit report from current bot state."""

    now    = datetime.now()
    cutoff = now.timestamp() - 24 * 3600   # last 24h for closed trades

    balance     = wallet.get('balance', starting)
    total_pnl   = wallet.get('total_pnl', 0.0)
    n_active    = len(active_trades)
    margin_used = n_active * 50.0

    floating_pnl = 0.0
    for t in active_trades:
        cp  = t.get('current_price', t['entry'])
        ep  = t['entry']
        pct = (cp - ep) / ep * 100 if ep else 0
        if t.get('direction') == 'SHORT':
            pct = -pct
        floating_pnl += round(500 * pct / 100, 2)

    free_cash     = max(0.0, balance - margin_used)
    total_balance = round(starting + total_pnl + floating_pnl, 2)

    recent_closed = [
        t for t in closed_trades_log
        if datetime.fromisoformat(t['closed_at']).timestamp() >= cutoff
    ]
    wins_24h   = sum(1 for t in recent_closed if t['pnl_usd'] > 0)
    losses_24h = sum(1 for t in recent_closed if t['pnl_usd'] <= 0)
    pnl_24h    = round(sum(t['pnl_usd'] for t in recent_closed), 2)

    POSITION_SIZE = 500.0   # $50 margin × 10x leverage

    active_detail = []
    for t in active_trades:
        cp  = t.get('current_price', t['entry'])
        ep  = t['entry']
        sl  = t.get('sl')
        tp  = t.get('tp')
        pct = (cp - ep) / ep * 100 if ep else 0
        if t.get('direction') == 'SHORT':
            pct = -pct

        # Expected P&L at TP / SL
        # Formula: abs(target - entry) / entry * position_size
        # Works for both LONG and SHORT (direction doesn't matter — abs handles it)
        exp_profit_usd = round(abs(tp  - ep) / ep * POSITION_SIZE, 2) if (tp  and ep) else None
        exp_loss_usd   = round(abs(sl  - ep) / ep * POSITION_SIZE, 2) if (sl  and ep) else None
        rr_ratio       = round(exp_profit_usd / exp_loss_usd, 2) if (exp_profit_usd and exp_loss_usd and exp_loss_usd > 0) else None

        active_detail.append({
            'symbol':              t['symbol'],
            'direction':           t.get('direction', 'LONG'),
            'timeframe':           t.get('timeframe', '4H'),
            'entry_price':         ep,
            'current_price':       round(cp, 8),
            'unrealized_pct':      round(pct, 2),
            'unrealized_usd':      round(POSITION_SIZE * pct / 100, 2),
            'expected_profit_usd': exp_profit_usd,
            'expected_loss_usd':   exp_loss_usd,
            'risk_reward_ratio':   rr_ratio,
            'score':               t.get('score', 0),
            'rsi':                 t.get('rsi'),
            'ema200':              t.get('ema200'),
            'score_breakdown':     t.get('score_breakdown', ''),
            'phase':               t.get('phase', 'initial'),
            'be_triggered':        t.get('be_triggered', False),
            'tp1_triggered':       t.get('tp1_triggered', False),
            'sl':                  sl,
            'tp':                  tp,
            'opened_at':           t.get('opened_at', ''),
        })

    return {
        'generated_at': now.isoformat(timespec='seconds'),
        'wallet': {
            'starting_balance': starting,
            'realized_pnl':     round(total_pnl, 2),
            'floating_pnl':     round(floating_pnl, 2),
            'free_cash':        round(free_cash, 2),
            'margin_used':      round(margin_used, 2),
            'total_balance':    total_balance,
        },
        'summary': {
            'active_count': n_active,
            'closed_count': len(recent_closed),
            'wins_24h':     wins_24h,
            'losses_24h':   losses_24h,
            'pnl_24h':      pnl_24h,
        },
        'active_trades':  active_detail,
        'closed_trades':  recent_closed,
    }


# ─────────────────────────────────────────────────────────────
# 2. LOCAL SAVE
# ─────────────────────────────────────────────────────────────

def save_report_locally(report_data):
    try:
        with open(AUDIT_FILE, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        return True, None
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# 3. GEMINI AI ANALYSIS
# ─────────────────────────────────────────────────────────────

def analyze_with_gemini(report_data):
    """
    Sends the trading report to Gemini for AI analysis.
    Returns analysis string (Hebrew) or None on failure.
    """
    if not GEMINI_URL or not GEMINI_KEY:
        return None
    try:
        w = report_data['wallet']
        s = report_data['summary']
        closed = report_data['closed_trades']
        active = report_data['active_trades']

        # סיכום עסקאות לפרומפט
        closed_lines = '\n'.join(
            f"  - {t['symbol']} {t.get('direction','LONG')} | "
            f"P&L: ${t['pnl_usd']:+.2f} | סיבת סגירה: {t.get('close_reason','')}"
            for t in closed[:15]
        ) or "  אין עסקאות סגורות"

        active_lines = '\n'.join(
            f"  - {t['symbol']} {t.get('direction','LONG')} | "
            f"Floating: ${t['unrealized_usd']:+.2f} | ציון: {t.get('score',0)}"
            for t in active
        ) or "  אין עסקאות פעילות"

        wr = (s['wins_24h'] / s['closed_count'] * 100) if s['closed_count'] > 0 else 0

        prompt = f"""אתה אנליסט מסחר מקצועי. נתח את דוח המסחר הבא ותן תובנות קצרות ועשירות בעברית.

📊 **נתוני הדוח — {report_data['generated_at']}:**
• יתרה כוללת: ${w['total_balance']:.2f} (התחלה: ${w['starting_balance']:.2f})
• Realized P&L: ${w['realized_pnl']:+.2f} | Floating: ${w['floating_pnl']:+.2f}
• עסקאות 24h: {s['closed_count']} נסגרו (✅ {s['wins_24h']} / ❌ {s['losses_24h']}) | Win Rate: {wr:.0f}%
• P&L 24h: ${s['pnl_24h']:+.2f}

📂 **עסקאות שנסגרו:**
{closed_lines}

🔓 **עסקאות פעילות:**
{active_lines}

תן ניתוח קצר (4-6 שורות) הכולל:
1. הערכת ביצועי הבוט היום
2. דפוסים שבולטים (סוגי סגירות, כיוונים)
3. המלצה אחת קצרה לשיפור או אישור שהאסטרטגיה עובדת
ענה בעברית בלבד, ללא כותרות markdown מורכבות."""

        url  = f'{GEMINI_URL}/models/gemini-2.5-flash:generateContent'
        hdrs = {'x-goog-api-key': GEMINI_KEY, 'Content-Type': 'application/json'}
        body = {
            'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
            'generationConfig': {'maxOutputTokens': 8192, 'temperature': 0.4}
        }
        resp = requests.post(url, headers=hdrs, json=body, timeout=25)
        if resp.ok:
            data = resp.json()
            candidate = data.get('candidates', [{}])[0]
            parts = candidate.get('content', {}).get('parts', [])
            # איחוד כל החלקים
            analysis = ''.join(p.get('text', '') for p in parts)
            # הסר markdown חזק שעלול לשבור Telegram
            analysis = analysis.replace('**', '').replace('__', '')
            print(f"✅ Gemini analysis complete ({len(analysis)} chars) | finish: {candidate.get('finishReason','?')}")
            return analysis.strip()
        else:
            print(f"Gemini error: {resp.status_code} {resp.text[:150]}")
            return None
    except Exception as e:
        print(f"Gemini analysis error: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# 4. TELEGRAM FILE SENDER
# ─────────────────────────────────────────────────────────────

def send_report_via_telegram(report_data):
    """
    Sends the audit report as a downloadable JSON file via Telegram.
    Returns (True, 'sent') or (False, error_str).
    """
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return False, "Missing TELEGRAM_TOKEN or CHAT_ID"
    try:
        now       = datetime.now()
        file_name = f"trading_report_{now.strftime('%Y-%m-%d')}.json"
        content   = json.dumps(report_data, indent=2, ensure_ascii=False).encode('utf-8')
        url       = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"

        w = report_data['wallet']
        s = report_data['summary']
        wr = (s['wins_24h'] / s['closed_count'] * 100) if s['closed_count'] > 0 else 0

        caption = (
            f"📊 *דוח יומי — {now.strftime('%d/%m/%Y')}*\n"
            f"💰 יתרה: `${w['total_balance']:.2f}` | "
            f"P&L 24h: `{'+' if s['pnl_24h']>=0 else ''}${s['pnl_24h']:.2f}`\n"
            f"✅ {s['wins_24h']} רווח / ❌ {s['losses_24h']} הפסד "
            f"({wr:.0f}% הצלחה)\n"
            f"📂 {s['closed_count']} עסקאות סגורות | "
            f"🔓 {s['active_count']} פעילות"
        )

        resp = requests.post(url, data={
            'chat_id':    CHAT_ID,
            'caption':    caption,
            'parse_mode': 'Markdown',
        }, files={
            'document': (file_name, io.BytesIO(content), 'application/json')
        }, timeout=15)

        if resp.ok:
            print(f"✅ Report sent via Telegram as {file_name}")
            return True, 'sent'
        else:
            return False, resp.text
    except Exception as e:
        print(f"Telegram file send error: {e}")
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# 3. GOOGLE DRIVE UPLOAD
# ─────────────────────────────────────────────────────────────

def _get_drive_service():
    """Returns an authenticated Google Drive service using the service account."""
    if not SA_JSON_STR or not FOLDER_ID:
        return None
    try:
        import google.oauth2.service_account as sa
        from googleapiclient.discovery import build

        info  = json.loads(SA_JSON_STR)
        creds = sa.Credentials.from_service_account_info(
            info,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        return build('drive', 'v3', credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"Drive auth error: {e}")
        return None


def upload_to_gdrive(report_data):
    """
    Uploads audit_report.json to TradingBot_Reports folder in Google Drive.
    File name: audit_YYYY-MM-DD_HH-MM.json
    If a file with the same name exists, it is updated (not duplicated).
    Returns (True, file_url) on success, (False, error_str) on failure.
    """
    service = _get_drive_service()
    if not service:
        return False, "Drive service unavailable (missing credentials or folder ID)"

    try:
        from googleapiclient.http import MediaIoBaseUpload

        now       = datetime.now()
        file_name = f"audit_{now.strftime('%Y-%m-%d_%H-%M')}.json"
        content   = json.dumps(report_data, indent=2, ensure_ascii=False).encode('utf-8')
        media     = MediaIoBaseUpload(io.BytesIO(content), mimetype='application/json')

        # Check if file already exists in folder
        q = (f"name='{file_name}' and '{FOLDER_ID}' in parents "
             f"and trashed=false")
        existing = service.files().list(q=q, fields='files(id)').execute()
        files    = existing.get('files', [])

        if files:
            # Update existing file
            file_id = files[0]['id']
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            # Create new file
            meta = {'name': file_name, 'parents': [FOLDER_ID]}
            f    = service.files().create(
                body=meta, media_body=media, fields='id'
            ).execute()
            file_id = f['id']

        url = f"https://drive.google.com/file/d/{file_id}/view"
        print(f"✅ Uploaded to Drive: {file_name} → {url}")
        return True, url

    except Exception as e:
        print(f"Drive upload error: {e}")
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# 5. TOP-LEVEL RUNNER (called from bot.py every 12h)
# ─────────────────────────────────────────────────────────────

def run_audit_upload(active_trades, wallet, closed_trades_log,
                     starting=200.0, send_telegram=None):
    """
    Builds the audit report, saves locally, sends via Telegram (file),
    and attempts Google Shared Drive upload if configured.
    Returns True on success.
    """
    report     = build_audit_report(active_trades, wallet, closed_trades_log, starting)
    ok_local, err_local = save_report_locally(report)

    # ניתוח AI מ-Gemini
    gemini_analysis = analyze_with_gemini(report)

    # שלח קובץ לטלגרם (ראשי — עובד תמיד)
    tg_ok, tg_info = send_report_via_telegram(report)

    # נסה העלאה לדרייב (משני — עובד רק עם Shared Drive)
    drive_ok, drive_info = upload_to_gdrive(report)

    if send_telegram:
        if ok_local:
            drive_line = (f"☁️ Drive: [פתח קובץ]({drive_info})"
                          if drive_ok else "📎 _הקובץ נשלח לטלגרם להורדה_")

            w   = report['wallet']
            s   = report['summary']
            closed = report['closed_trades']

            # פירוט עסקאות סגורות (מקסימום 10)
            trades_lines = ""
            for t in closed[:10]:
                sym   = t['symbol'].replace('USDT', '')
                direc = '🟢 L' if t.get('direction','LONG')=='LONG' else '🔴 S'
                pnl   = t['pnl_usd']
                sign  = '+' if pnl >= 0 else ''
                emoji = '✅' if pnl > 0 else '❌'
                reason = t.get('close_reason', '')
                trades_lines += (f"  {emoji} `{sym}` {direc} | "
                                 f"`{sign}${pnl:.2f}` | {reason}\n")
            if not trades_lines:
                trades_lines = "  _אין עסקאות סגורות ב-24 שעות האחרונות_\n"
            elif len(closed) > 10:
                trades_lines += f"  _...ועוד {len(closed)-10} עסקאות_\n"

            # עסקאות פעילות
            active_lines = ""
            for t in report['active_trades']:
                sym   = t['symbol'].replace('USDT', '')
                direc = '🟢 L' if t.get('direction','LONG')=='LONG' else '🔴 S'
                upnl  = t['unrealized_usd']
                sign  = '+' if upnl >= 0 else ''
                active_lines += f"  `{sym}` {direc} | `{sign}${upnl:.2f}` (floating)\n"
            if not active_lines:
                active_lines = "  _אין עסקאות פעילות_\n"

            wr = (s['wins_24h'] / s['closed_count'] * 100) if s['closed_count'] > 0 else 0

            send_telegram(
                f"📊 *דוח יומי — 24 שעות אחרונות*\n"
                f"🕛 {report['generated_at']}\n"
                f"{'─'*28}\n\n"

                f"*💼 מצב ארנק:*\n"
                f"  💰 יתרה כוללת: `${w['total_balance']:.2f}`\n"
                f"  📈 Realized P&L: `{'+' if w['realized_pnl']>=0 else ''}${w['realized_pnl']:.2f}`\n"
                f"  💹 Floating P&L: `{'+' if w['floating_pnl']>=0 else ''}${w['floating_pnl']:.2f}`\n"
                f"  💵 מזומן חופשי:  `${w['free_cash']:.2f}`\n\n"

                f"*📋 סיכום 24 שעות:*\n"
                f"  עסקאות שנסגרו: `{s['closed_count']}` "
                f"(✅ {s['wins_24h']} / ❌ {s['losses_24h']})\n"
                f"  🎯 אחוז הצלחה: `{wr:.0f}%`\n"
                f"  💵 P&L 24h: `{'+' if s['pnl_24h']>=0 else ''}${s['pnl_24h']:.2f}`\n\n"

                f"*📂 עסקאות שנסגרו ({min(len(closed),10)}/{len(closed)}):*\n"
                f"{trades_lines}\n"

                f"*🔓 עסקאות פעילות ({s['active_count']}):*\n"
                f"{active_lines}\n"

                f"{drive_line}\n"
                f"{'─'*28}"
            )

            # שלח ניתוח Gemini כהודעה נפרדת
            if gemini_analysis:
                send_telegram(
                    f"🤖 *ניתוח AI | Gemini*\n\n"
                    f"{gemini_analysis}"
                )
        else:
            send_telegram(f"⚠️ *Daily Report — שגיאה בשמירה*\n`{err_local}`")

    print(f"Audit local: {'✅ ' + AUDIT_FILE if ok_local else '❌ ' + str(err_local)}")
    return ok_local
