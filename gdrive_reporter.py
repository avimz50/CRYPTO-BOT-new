"""
Trading Bot — Audit Reporter
Builds a structured JSON audit report and saves it locally to audit_report.json.
Exposed via /api/audit on the Flask server for download from the dashboard.
(Google Drive upload removed — Service Accounts lack personal Drive quota.)
"""

import os
import json
from datetime import datetime


AUDIT_FILE = 'audit_report.json'


# ─────────────────────────────────────────────────────────────
# 1. REPORT BUILDER
# ─────────────────────────────────────────────────────────────

def build_audit_report(active_trades, wallet, closed_trades_log, starting=200.0):
    """Builds a structured JSON audit report from current bot state."""

    now     = datetime.now()
    cutoff  = now.timestamp() - 12 * 3600   # last 12h for closed trades

    # ── Wallet metrics ────────────────────────────────────────
    balance     = wallet.get('balance', starting)
    total_pnl   = wallet.get('total_pnl', 0.0)
    n_active    = len(active_trades)
    margin_used = n_active * 50.0

    floating_pnl = 0.0
    for t in active_trades:
        cp = t.get('current_price', t['entry'])
        ep = t['entry']
        pct = (cp - ep) / ep * 100 if ep else 0
        if t.get('direction') == 'SHORT':
            pct = -pct
        floating_pnl += round(500 * pct / 100, 2)

    free_cash     = max(0.0, balance - margin_used)
    total_balance = round(starting + total_pnl + floating_pnl, 2)

    # ── Closed trades summary (last 12h) ──────────────────────
    recent_closed = [
        t for t in closed_trades_log
        if datetime.fromisoformat(t['closed_at']).timestamp() >= cutoff
    ]
    wins_12h   = sum(1 for t in recent_closed if t['pnl_usd'] > 0)
    losses_12h = sum(1 for t in recent_closed if t['pnl_usd'] <= 0)
    pnl_12h    = round(sum(t['pnl_usd'] for t in recent_closed), 2)

    # ── Active trade detail ───────────────────────────────────
    active_detail = []
    for t in active_trades:
        cp  = t.get('current_price', t['entry'])
        ep  = t['entry']
        pct = (cp - ep) / ep * 100 if ep else 0
        if t.get('direction') == 'SHORT':
            pct = -pct
        active_detail.append({
            'symbol':       t['symbol'],
            'direction':    t.get('direction', 'LONG'),
            'timeframe':    t.get('timeframe', '4H'),
            'entry_price':  ep,
            'current_price': round(cp, 8),
            'unrealized_pct': round(pct, 2),
            'unrealized_usd': round(500 * pct / 100, 2),
            'score':        t.get('score', 0),
            'rsi':          t.get('rsi'),
            'ema200':       t.get('ema200'),
            'score_breakdown': t.get('score_breakdown', ''),
            'phase':        t.get('phase', 'initial'),
            'be_triggered': t.get('be_triggered', False),
            'tp1_triggered': t.get('tp1_triggered', False),
            'sl':           t.get('sl'),
            'tp':           t.get('tp'),
            'opened_at':    t.get('opened_at', ''),
        })

    report = {
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
            'active_count':  n_active,
            'closed_count':  len(recent_closed),
            'wins_12h':      wins_12h,
            'losses_12h':    losses_12h,
            'pnl_12h':       pnl_12h,
        },
        'active_trades':  active_detail,
        'closed_trades':  recent_closed,
    }
    return report


# ─────────────────────────────────────────────────────────────
# 2. LOCAL SAVE
# ─────────────────────────────────────────────────────────────

def save_report_locally(report_data):
    """Saves the report as audit_report.json next to bot.py."""
    try:
        with open(AUDIT_FILE, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        return True, None
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# 3. TOP-LEVEL RUNNER (called from bot.py every 12h)
# ─────────────────────────────────────────────────────────────

def run_audit_upload(active_trades, wallet, closed_trades_log,
                     starting=200.0, send_telegram=None):
    """
    Builds the audit report and saves it locally.
    send_telegram: callable(text) to notify via Telegram, or None.
    Returns True on success.
    """
    report  = build_audit_report(active_trades, wallet, closed_trades_log, starting)
    ok, err = save_report_locally(report)

    if send_telegram:
        if ok:
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
                f"📥 הורד דוח: /api/audit"
            )
        else:
            send_telegram(f"⚠️ *Risk Audit — שגיאה בשמירה*\n`{err}`")

    print(f"Audit report: {'✅ saved to ' + AUDIT_FILE if ok else f'❌ failed: {err}'}")
    return ok
