"""
Trading Bot — Audit Reporter
Builds a structured JSON audit report, saves it locally to audit_report.json,
and uploads it to Google Drive (TradingBot_Reports folder).
"""

import os
import json
import io
from datetime import datetime


AUDIT_FILE    = 'audit_report.json'
FOLDER_ID     = os.environ.get('GDRIVE_FOLDER_ID', '')
SA_JSON_STR   = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON', '')


# ─────────────────────────────────────────────────────────────
# 1. REPORT BUILDER
# ─────────────────────────────────────────────────────────────

def build_audit_report(active_trades, wallet, closed_trades_log, starting=200.0):
    """Builds a structured JSON audit report from current bot state."""

    now    = datetime.now()
    cutoff = now.timestamp() - 12 * 3600   # last 12h for closed trades

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
    wins_12h   = sum(1 for t in recent_closed if t['pnl_usd'] > 0)
    losses_12h = sum(1 for t in recent_closed if t['pnl_usd'] <= 0)
    pnl_12h    = round(sum(t['pnl_usd'] for t in recent_closed), 2)

    active_detail = []
    for t in active_trades:
        cp  = t.get('current_price', t['entry'])
        ep  = t['entry']
        pct = (cp - ep) / ep * 100 if ep else 0
        if t.get('direction') == 'SHORT':
            pct = -pct
        active_detail.append({
            'symbol':          t['symbol'],
            'direction':       t.get('direction', 'LONG'),
            'timeframe':       t.get('timeframe', '4H'),
            'entry_price':     ep,
            'current_price':   round(cp, 8),
            'unrealized_pct':  round(pct, 2),
            'unrealized_usd':  round(500 * pct / 100, 2),
            'score':           t.get('score', 0),
            'rsi':             t.get('rsi'),
            'ema200':          t.get('ema200'),
            'score_breakdown': t.get('score_breakdown', ''),
            'phase':           t.get('phase', 'initial'),
            'be_triggered':    t.get('be_triggered', False),
            'tp1_triggered':   t.get('tp1_triggered', False),
            'sl':              t.get('sl'),
            'tp':              t.get('tp'),
            'opened_at':       t.get('opened_at', ''),
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
            'wins_12h':     wins_12h,
            'losses_12h':   losses_12h,
            'pnl_12h':      pnl_12h,
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
# 4. TOP-LEVEL RUNNER (called from bot.py every 12h)
# ─────────────────────────────────────────────────────────────

def run_audit_upload(active_trades, wallet, closed_trades_log,
                     starting=200.0, send_telegram=None):
    """
    Builds the audit report, saves locally, and uploads to Google Drive.
    Returns True on success.
    """
    report     = build_audit_report(active_trades, wallet, closed_trades_log, starting)
    ok_local, err_local = save_report_locally(report)

    drive_ok,  drive_info = upload_to_gdrive(report)

    if send_telegram:
        if ok_local:
            drive_line = (f"☁️ Drive: [פתח קובץ]({drive_info})"
                          if drive_ok else f"⚠️ Drive: {drive_info}")
            send_telegram(
                "📊 *Risk Audit Report — עודכן*\n"
                f"🕐 {report['generated_at']}\n\n"
                f"*מצב ארנק:*\n"
                f"💰 Total Balance: `${report['wallet']['total_balance']:.2f}`\n"
                f"📈 Realized P&L: `${report['wallet']['realized_pnl']:+.2f}`\n"
                f"💹 Floating P&L: `${report['wallet']['floating_pnl']:+.2f}`\n"
                f"💵 Free Cash:    `${report['wallet']['free_cash']:.2f}`\n\n"
                f"📊 עסקאות פעילות: `{report['summary']['active_count']}`\n"
                f"📋 נסגרו ב-12h: `{report['summary']['closed_count']}` "
                f"(✅ {report['summary']['wins_12h']} / ❌ {report['summary']['losses_12h']})\n"
                f"💵 P&L 12h: `${report['summary']['pnl_12h']:+.2f}`\n\n"
                f"{drive_line}\n"
                f"📥 הורד מהדשבורד: /api/audit"
            )
        else:
            send_telegram(f"⚠️ *Risk Audit — שגיאה בשמירה*\n`{err_local}`")

    print(f"Audit local: {'✅ ' + AUDIT_FILE if ok_local else '❌ ' + str(err_local)}")
    return ok_local
