"""
database_manager.py — The Single Source of Truth

RULES (enforced by design):
  - This is the ONLY module that reads/writes to Replit Object Storage.
  - No other module may call state_store directly or modify wallet state.
  - Equity formula is immutable and lives only here:
      Equity = STARTING_BALANCE + realized_pnl + floating_pnl

All other modules (bot.py, strategy_engine.py, telegram UI) call this module.
"""

from __future__ import annotations
import state_store

# ── Immutable constants ────────────────────────────────────────────────────────
STARTING_BALANCE: float = 200.0

# Disk cache paths (write-through mirrors of Object Storage)
WALLET_FILE       = "artifacts/bot-dashboard/public/wallet.json"
TRADES_FILE       = "artifacts/bot-dashboard/public/active_trades.json"
AUDIT_FILE        = "artifacts/bot-dashboard/public/trade_audit.json"

# ── Equity formula (THE immutable definition) ─────────────────────────────────
def calc_equity(realized_pnl: float, floating_pnl: float = 0.0) -> float:
    """
    Equity = STARTING_BALANCE + realized_pnl + floating_pnl

    This is the single authoritative definition.
    'balance' (available cash) is NOT equity — it drifts with fees.
    Only this formula may be used for display or logging equity.
    """
    return round(STARTING_BALANCE + realized_pnl + floating_pnl, 2)


def reconcile_balance(balance: float, equity: float, locked: float) -> float:
    """
    Enforce: when no margin is locked (active_trades == 0),
    available balance MUST equal equity.

    Virtual-mode drift rule: the 'balance' field can drift due to
    incomplete margin returns. This function is the single place that
    corrects it. Called on every save and every API response.
    """
    if locked == 0.0:
        return round(equity, 2)
    return round(balance, 2)


# ── Wallet ─────────────────────────────────────────────────────────────────────
def get_wallet() -> dict:
    """Load wallet from Object Storage → disk → default. Never returns None."""
    default = {
        'balance':           STARTING_BALANCE,
        'starting':          STARTING_BALANCE,
        'total_pnl':         0.0,
        'trades_opened':     0,
        'total_wins':        0,
        'total_losses':      0,
        'locked_balance':    0.0,
        'available_balance': STARTING_BALANCE,
        'unrealized_pnl':    0.0,
        'equity_history':    [],
    }
    return state_store.load_state('wallet', WALLET_FILE, default)


def save_wallet(wallet: dict, active_trades: list | None = None) -> None:
    """
    Persist wallet to Object Storage + disk.
    Automatically recomputes equity using the immutable formula before saving.
    Pass active_trades to allow locked_balance + unrealized to be recalculated.
    """
    if active_trades is None:
        active_trades = []

    realized   = wallet.get('total_pnl', 0.0)
    # If no active trades, unrealized MUST be 0 — stale values cause equity drift
    unrealized = calc_floating_pnl(active_trades) if active_trades else 0.0
    locked     = round(sum(t.get('margin', 0.0) for t in active_trades), 2)
    equity     = calc_equity(realized, unrealized)
    balance    = reconcile_balance(wallet.get('balance', STARTING_BALANCE), equity, locked)

    snapshot = {
        **wallet,
        'starting':          STARTING_BALANCE,
        'balance':           balance,
        'locked_balance':    locked,
        'available_balance': balance,
        'unrealized_pnl':    round(unrealized, 2),
        # Equity is ALWAYS recomputed here — never trusted from callers
        'equity':            equity,
    }
    state_store.save_state('wallet', snapshot, WALLET_FILE)


def reset_wallet() -> dict:
    """Hard-reset wallet to $200 baseline. Returns the fresh wallet."""
    fresh = {
        'balance':           STARTING_BALANCE,
        'starting':          STARTING_BALANCE,
        'total_pnl':         0.0,
        'trades_opened':     0,
        'total_wins':        0,
        'total_losses':      0,
        'locked_balance':    0.0,
        'available_balance': STARTING_BALANCE,
        'unrealized_pnl':    0.0,
        'equity':            STARTING_BALANCE,
        'equity_history':    [],
    }
    state_store.reset_state('wallet', fresh, WALLET_FILE)
    return fresh


# ── Active Trades ──────────────────────────────────────────────────────────────
def get_active_trades() -> list:
    """Load active trades from Object Storage → disk → []."""
    data = state_store.load_state(
        'active_trades',
        TRADES_FILE,
        {'updated': None, 'count': 0, 'trades': []},
    )
    if isinstance(data, dict):
        return data.get('trades', [])
    return []


def save_active_trades(trades: list) -> None:
    """Persist active trades list to Object Storage + disk."""
    from datetime import datetime
    import pytz
    ts = datetime.now(pytz.timezone('Asia/Jerusalem')).strftime('%H:%M:%S')
    state_store.save_state(
        'active_trades',
        {'updated': ts, 'count': len(trades), 'trades': trades},
        TRADES_FILE,
    )


# ── Trade Audit Log ───────────────────────────────────────────────────────────
def get_audit_log() -> list:
    """Load audit log from Object Storage → disk → []."""
    data = state_store.load_state(
        'trade_audit',
        AUDIT_FILE,
        {'updated': None, 'count': 0, 'trades': []},
    )
    if isinstance(data, dict):
        return data.get('trades', [])
    return []


def save_audit_log(audit: list) -> None:
    """Persist audit log to Object Storage + disk."""
    from datetime import datetime
    import pytz
    ts = datetime.now(pytz.timezone('Asia/Jerusalem')).isoformat(timespec='seconds')
    state_store.save_state(
        'trade_audit',
        {'updated': ts, 'count': len(audit), 'trades': audit},
        AUDIT_FILE,
    )


# ── Floating P&L (read-only, computed from trade snapshots) ───────────────────
def calc_floating_pnl(trades: list) -> float:
    """
    Compute floating (unrealized) P&L from current_price snapshots stored
    inside each trade dict. Pure calculation — no API calls.
    """
    total = 0.0
    for t in trades:
        curr  = t.get('current_price', t.get('entry', 0))
        entry = t.get('entry', 0)
        pos   = t.get('pos_size', 0)
        if entry <= 0 or pos <= 0:
            continue
        if t.get('direction') == 'LONG':
            total += (curr - entry) / entry * pos
        else:
            total += (entry - curr) / entry * pos
    return round(total, 2)


# ── Convenience snapshot (for Flask routes / Telegram) ───────────────────────
def get_portfolio_snapshot(trades: list | None = None) -> dict:
    """
    Returns a single consistent dict with all financial fields,
    equity computed via the immutable formula.
    Safe to pass directly to Flask jsonify or Telegram messages.
    """
    wallet = get_wallet()
    if trades is None:
        trades = get_active_trades()

    realized   = wallet.get('total_pnl', 0.0)
    floating   = calc_floating_pnl(trades)
    locked     = round(sum(t.get('margin', 0.0) for t in trades), 2)
    equity     = calc_equity(realized, floating)
    available  = reconcile_balance(wallet.get('balance', STARTING_BALANCE), equity, locked)

    return {
        'starting':          STARTING_BALANCE,
        'realized_pnl':      realized,
        'floating_pnl':      floating,
        'equity':            equity,
        'locked_balance':    locked,
        'available_balance': available,
        'active_count':      len(trades),
        'total_pnl':         realized,          # alias used by dashboard
        'unrealized_pnl':    floating,          # alias used by dashboard
        'balance':           available,
        **{k: v for k, v in wallet.items()
           if k not in ('equity', 'total_pnl', 'unrealized_pnl',
                        'locked_balance', 'available_balance', 'balance', 'starting')},
    }
