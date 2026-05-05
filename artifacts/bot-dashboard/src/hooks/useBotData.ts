import { useEffect, useState, useCallback, useRef } from "react";

// ── API types ──────────────────────────────────────────────────
export interface StatusData {
  connected: boolean;
  exchange: string;
  mode: string;
  equity: number;
  available: number;
  starting: number;
  unrealized: number;
  realized: number;
  locked: number;
  fng_value: number;
  fng_label: string;
  active_trades: number;
  btc_price: number;
  ts: number;
}

export interface BotTrade {
  symbol: string;
  entry: number;
  current_price?: number;
  sl: number;
  tp: number;
  tp1: number;
  be_lvl: number;
  sl_pct: number;
  tp_pct: number;
  direction: "LONG" | "SHORT";
  phase: "initial" | "trailing";
  be_triggered: boolean;
  tp1_triggered: boolean;
  tp1_pnl?: number;
  partial_25_triggered?: boolean;
  pos_size?: number;
  score: number;
  atr: number;
  peak_price: number;
  trailing_sl: number | null;
  timeframe?: string;
  leverage?: number;
}

export interface TradesData {
  updated: string | null;
  count: number;
  trades: BotTrade[];
}

export interface ScanResult {
  symbol: string;
  signal: "BUY" | "SELL" | "HOLD";
  score?: number;
  confidence?: string;
  last_checked?: string;
  timeframe?: string;
}

export interface ScanData {
  updated: string | null;
  count: number;
  results: ScanResult[];
}

export interface SlotsData {
  max_trades: number;
  active_trades: number;
  open_slots: number;
  min: number;
  max: number;
  amount_per_trade?: number;
  default_leverage?: number;
}

export interface AuditTrade {
  id?: string;
  symbol: string;
  direction: "LONG" | "SHORT";
  open_time?: string;
  entry_price?: number;
  close_time?: string;
  close_price?: number;
  leverage?: number;
  pos_size?: number;
  pnl?: number;
  pnl_pct?: number;
  close_reason?: string;
}

export interface AuditData {
  updated: string | null;
  count: number;
  trades: AuditTrade[];
}

export interface WalletData {
  balance: number;
  starting: number;
  total_pnl: number;
  trades_opened: number;
  equity_history: Array<{ t: string; eq: number }>;
  available_balance?: number;
  unrealized_pnl?: number;
  equity?: number;
}

export interface FngSettings {
  extreme_fear: number;
  fear: number;
  greed: number;
  amount_per_trade?: number;
  default_leverage?: number;
}

export interface BotLogData {
  stdout: string;
  stderr: string;
  crash: string | null;
}

// ── Generic polling hook ────────────────────────────────────────
function usePoll<T>(url: string, interval: number, enabled = true) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const lastFetch = useRef(0);

  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch(`${url}?_t=${Date.now()}`);
      if (!r.ok) throw new Error(`${r.status}`);
      const json = await r.json();
      setData(json);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
    lastFetch.current = Date.now();
  }, [url]);

  const refetch = useCallback(() => { fetch_(); }, [fetch_]);

  useEffect(() => {
    if (!enabled) return;
    fetch_();
    const id = setInterval(fetch_, interval);
    return () => clearInterval(id);
  }, [fetch_, interval, enabled]);

  return { data, loading, error, refetch };
}

// ── Public hooks ───────────────────────────────────────────────
export function useStatus() {
  return usePoll<StatusData>("/api/status", 10_000);
}

export function useTrades() {
  return usePoll<TradesData>("/api/trades", 10_000);
}

export function useScan() {
  return usePoll<ScanData>("/api/last_scan", 30_000);
}

export function useSlots() {
  return usePoll<SlotsData>("/api/slots", 30_000);
}

export function useWallet() {
  return usePoll<WalletData>("/api/wallet", 30_000);
}

export function useFngSettings() {
  return usePoll<FngSettings>("/api/fng_settings", 60_000);
}

export function useAudit(enabled: boolean) {
  return usePoll<AuditData>("/api/trade_audit", 60_000, enabled);
}

export function useBotLog(enabled: boolean) {
  return usePoll<BotLogData>("/api/bot_log", 5_000, enabled);
}

// ── Sync with Telegram ─────────────────────────────────────────
export async function syncTelegram(): Promise<boolean> {
  try {
    const r = await fetch("/api/make", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "sync_telegram" }),
    });
    return r.ok;
  } catch {
    return false;
  }
}

// ── Save slots ─────────────────────────────────────────────────
export async function saveSlots(max_trades: number): Promise<boolean> {
  try {
    const r = await fetch("/api/slots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_trades }),
    });
    return r.ok;
  } catch {
    return false;
  }
}

// ── Save FNG settings (+ extra fields for amount/leverage) ─────
export async function saveFngSettings(payload: Partial<FngSettings>): Promise<boolean> {
  try {
    const r = await fetch("/api/fng_settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return r.ok;
  } catch {
    return false;
  }
}
