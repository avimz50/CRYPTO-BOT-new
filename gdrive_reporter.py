"""
Google Drive Risk Audit Reporter
Generates and uploads trading_audit_report.json every 12h (08:00 & 20:00).
Requires env var: GDRIVE_SERVICE_ACCOUNT_JSON  (full JSON of a GCP service account key)
The service account must have Google Drive API enabled and access to the target folder.
"""

import os
import io
import json
from datetime import datetime


# ─────────────────────────────────────────────────────────────
# 1. REPORT BUILDER
# ─────────────────────────────────────────────────────────────

def build_audit_report(active_trades, wallet, closed_trades_log, starting=200.0):
    """Builds a structured JSON audit report from current bot state."""

    total_pnl  = wallet.get('total_pnl', 0.0)
    locked     = len(active_trades) * 50
    trades_cnt = wallet.get('trades_opened', 0)

    # Floating P&L from live current_price stored per trade
    floating = 0.0
    for t in active_trades:
        cp  = t.get('current_price', t['entry'])
        raw = (cp - t['entry']) / t['entry'] * 100
        pct = raw if t['direction'] == 'LONG' else -raw
        if t.get('tp1_triggered'):
            usd = (t.get('tp1_pnl') or 0) + 250 * pct / 100
        else:
            usd = 500 * pct / 100
        floating += usd

    total_balance = round(starting + total_pnl + floating, 2)
    free_cash     = round(max(0, starting - locked + total_pnl), 2)

    # Active trades detail
    active_list = []
    for t in active_trades:
        cp  = t.get('current_price', t['entry'])
        raw = (cp - t['entry']) / t['entry'] * 100
        pct = raw if t['direction'] == 'LONG' else -raw
        if t.get('tp1_triggered'):
            float_usd = (t.get('tp1_pnl') or 0) + 250 * pct / 100
        else:
            float_usd = 500 * pct / 100

        active_list.append({
            'symbol':          t['symbol'],
            'direction':       t['direction'],
            'timeframe':       t.get('timeframe', '4H'),
            'entry_price':     t['entry'],
            'current_price':   cp,
            'sl':              t['sl'],
            'tp':              t['tp'],
            'tp1':             t.get('tp1'),
            'score':           t.get('score', 0),
            'rsi_at_entry':    t.get('rsi'),
            'ema200_at_entry': t.get('ema200'),
            'breakdown':       t.get('score_breakdown', ''),
            'floating_pnl_usd': round(float_usd, 2),
            'floating_pct':    round(pct, 2),
            'phase':           t.get('phase', 'initial'),
            'tp1_triggered':   t.get('tp1_triggered', False),
            'be_triggered':    t.get('be_triggered', False),
        })

    # Closed trades in last 12 hours
    now = datetime.now()
    closed_12h = []
    for t in closed_trades_log:
        try:
            closed_dt   = datetime.fromisoformat(t.get('closed_at', '2000-01-01'))
            hours_ago   = (now - closed_dt).total_seconds() / 3600
            if hours_ago <= 12:
                closed_12h.append(t)
        except Exception:
            pass

    wins   = sum(1 for t in closed_12h if t.get('pnl_usd', 0) > 0)
    losses = sum(1 for t in closed_12h if t.get('pnl_usd', 0) <= 0)

    report = {
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
        'period':       '12-hour risk audit',
        'wallet': {
            'starting_balance': starting,
            'total_balance':    total_balance,
            'realized_pnl':     round(total_pnl, 2),
            'floating_pnl':     round(floating, 2),
            'free_cash':        free_cash,
            'locked_margin':    locked,
            'total_trades_ever': trades_cnt,
        },
        'active_trades':    active_list,
        'closed_last_12h':  closed_12h,
        'summary': {
            'active_count':  len(active_list),
            'closed_count':  len(closed_12h),
            'wins_12h':      wins,
            'losses_12h':    losses,
            'win_rate_12h':  f"{round(wins/(wins+losses)*100)}%" if (wins+losses) > 0 else 'N/A',
        }
    }
    return report


# ─────────────────────────────────────────────────────────────
# 2. GOOGLE DRIVE UPLOADER
# ─────────────────────────────────────────────────────────────

FOLDER_NAME = 'TradingBot_Reports'
FILE_NAME   = 'trading_audit_report.json'


def upload_to_drive(report_data):
    """
    Uploads the audit report JSON to Google Drive.
    Returns (success: bool, error: str|None).
    """
    sa_json = os.environ.get('GDRIVE_SERVICE_ACCOUNT_JSON', '')
    if not sa_json.strip():
        return False, 'GDRIVE_SERVICE_ACCOUNT_JSON secret not set'

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload

        creds_dict  = json.loads(sa_json)
        scopes      = ['https://www.googleapis.com/auth/drive']
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=scopes
        )
        service = build('drive', 'v3', credentials=credentials, cache_discovery=False)

        # ── Find or create folder ──────────────────────────────
        q_folder = (
            f"name='{FOLDER_NAME}' "
            f"and mimeType='application/vnd.google-apps.folder' "
            f"and trashed=false"
        )
        res     = service.files().list(q=q_folder, spaces='drive', fields='files(id)').execute()
        folders = res.get('files', [])
        if folders:
            folder_id = folders[0]['id']
        else:
            folder_meta = {'name': FOLDER_NAME,
                           'mimeType': 'application/vnd.google-apps.folder'}
            folder_id = service.files().create(
                body=folder_meta, fields='id'
            ).execute().get('id')

        # ── Check if file already exists (overwrite) ──────────
        q_file   = f"name='{FILE_NAME}' and '{folder_id}' in parents and trashed=false"
        res2     = service.files().list(q=q_file, spaces='drive', fields='files(id)').execute()
        existing = res2.get('files', [])

        content = json.dumps(report_data, indent=2, ensure_ascii=False)
        media   = MediaIoBaseUpload(
            io.BytesIO(content.encode('utf-8')),
            mimetype='application/json',
            resumable=False
        )

        if existing:
            service.files().update(
                fileId=existing[0]['id'],
                media_body=media
            ).execute()
        else:
            service.files().create(
                body={'name': FILE_NAME, 'parents': [folder_id]},
                media_body=media,
                fields='id'
            ).execute()

        return True, None

    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# 3. TOP-LEVEL RUNNER (called from bot.py)
# ─────────────────────────────────────────────────────────────

def run_audit_upload(active_trades, wallet, closed_trades_log,
                     starting=200.0, send_telegram=None):
    """
    Builds the report and uploads it.
    send_telegram: callable(text) to notify via Telegram, or None.
    Returns True on success.
    """
    report  = build_audit_report(active_trades, wallet, closed_trades_log, starting)
    ok, err = upload_to_drive(report)

    if send_telegram:
        if ok:
            send_telegram(
                "✅ *Risk Audit — Google Drive*\n"
                f"📁 `TradingBot_Reports/{FILE_NAME}` עודכן\n"
                f"🕐 {report['generated_at']}\n\n"
                f"*מצב ארנק:*\n"
                f"💰 Total Balance: `${report['wallet']['total_balance']:.2f}`\n"
                f"📈 Realized P&L: `${report['wallet']['realized_pnl']:+.2f}`\n"
                f"💹 Floating P&L: `${report['wallet']['floating_pnl']:+.2f}`\n"
                f"💵 Free Cash:    `${report['wallet']['free_cash']:.2f}`\n\n"
                f"📊 עסקאות פעילות: `{report['summary']['active_count']}`\n"
                f"📋 נסגרו ב-12h: `{report['summary']['closed_count']}` "
                f"(✅ {report['summary']['wins_12h']} / ❌ {report['summary']['losses_12h']})"
            )
        else:
            send_telegram(
                f"⚠️ *Risk Audit — שגיאה בהעלאה ל-Drive*\n`{err}`\n\n"
                "_ודא שה-Secret `GDRIVE_SERVICE_ACCOUNT_JSON` מוגדר נכון._"
            )

    print(f"Drive audit: {'✅ uploaded' if ok else f'❌ failed: {err}'}")
    return ok
