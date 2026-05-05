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

// ── Scan data — actual shape from /api/last_scan ──────────────
export interface RejectedCoin {
  symbol: string;
  direction: "LONG" | "SHORT";
  best_score: number;
  reason: string;
  scores: { "4H"?: number; "1H"?: number; "15m"?: number };
}

export interface BubbleWatch {
  symbol: string;
  change_pct: number;
  direction: string;
  price: number;
  volume_usd: number;
}

export interface ScanData {
  scan_time: string | null;
  total_scanned: number;
  signals_found: number;
  active_trades_count: number;
  max_trades: number;
  min_score: number;
  btc_regime: string;
  fng_value: number;
  fng_label: string;
  market_sentiment_factor: string;
  rejected_coins: RejectedCoin[];
  system_message: string;
  scan_duration_s: number;
  bubble_watch: BubbleWatch[];
  sandbox_analysis: unknown[];
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

// ── Audit data — actual shape from /api/trade_audit ──────────
export interface AuditTrade {
  symbol: string;
  direction: "LONG" | "SHORT";
  timeframe?: string;
  entry_price: number;
  close_price: number;
  sl_at_open?: number;
  tp_at_open?: number;
  dist_sl_pct?: number;
  dist_tp_pct?: number;
  rr_ratio?: number;
  rr_achieved?: number;
  score?: number;
  close_reason: string;
  pnl_usd: number;
  opened_at: string;
  closed_at: string;
  fng_at_entry?: number;
  duration_min?: number;
  strategy?: string;
  track?: string;
  lesson?: string;
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

// ── Poll result with stale detection ──────────────────────────
export interface PollResult<T> {
  data: T | null;
  loading: boolean;
  error: boolean;
  /** true when data exists but last successful fetch was more than 2× the poll interval ago */
  stale: boolean;
  refetch: () => void;
}

// ── Generic polling hook ────────────────────────────────────────
function usePoll<T>(url: string, interval: number, enabled = true): PollResult<T> {
  const [data, setData]       = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(false);
  const [lastOk, setLastOk]   = useState<number | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const r = await fetch(`${url}?_t=${Date.now()}`);
      if (!r.ok) throw new Error(`${r.status}`);
      const json = (await r.json()) as T;
      setData(json);
      setError(false);
      setLastOk(Date.now());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [url]);

  const refetch = useCallback(() => { fetch_(); }, [fetch_]);

  useEffect(() => {
    if (!enabled) return;
    fetch_();
    const id = setInterval(fetch_, interval);
    return () => clearInterval(id);
  }, [fetch_, interval, enabled]);

  const stale = data != null && lastOk != null && (Date.now() - lastOk) > interval * 2;

  return { data, loading, error, stale, refetch };
}

// ── Public hooks ───────────────────────────────────────────────
export function useStatus()    { return usePoll<StatusData>("/api/status",      10_000); }
export function useTrades()    { return usePoll<TradesData>("/api/trades",      10_000); }
export function useScan()      { return usePoll<ScanData>("/api/last_scan",     30_000); }
export function useSlots()     { return usePoll<SlotsData>("/api/slots",        30_000); }
export function useWallet()    { return usePoll<WalletData>("/api/wallet",      30_000); }
export function useFngSettings() { return usePoll<FngSettings>("/api/fng_settings", 60_000); }
export function useAudit(enabled: boolean) { return usePoll<AuditData>("/api/trade_audit", 60_000, enabled); }
export function useBotLog(enabled: boolean) { return usePoll<BotLogData>("/api/bot_log",     5_000,  enabled); }

// ── Actions ────────────────────────────────────────────────────
export interface LocalSettings {
  amount_per_trade: number;
  default_leverage: number;
}

export async function syncTelegram(): Promise<boolean> {
  try {
    const r = await fetch("/api/telegram_sync", { method: "POST" });
    if (!r.ok) return false;
    const j = await r.json() as { ok: boolean };
    return j.ok === true;
  } catch { return false; }
}

export async function fetchLocalSettings(): Promise<LocalSettings | null> {
  try {
    const r = await fetch("/api/local_settings");
    if (!r.ok) return null;
    return await r.json() as LocalSettings;
  } catch { return null; }
}

export async function saveLocalSettings(payload: Partial<LocalSettings>): Promise<boolean> {
  try {
    const r = await fetch("/api/local_settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) return false;
    const j = await r.json() as { ok: boolean };
    return j.ok === true;
  } catch { return false; }
}

export async function saveSlots(max_trades: number): Promise<boolean> {
  try {
    const r = await fetch("/api/slots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_trades }),
    });
    return r.ok;
  } catch { return false; }
}

export async function saveFngSettings(payload: Partial<FngSettings>): Promise<boolean> {
  try {
    const r = await fetch("/api/fng_settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return r.ok;
  } catch { return false; }
}
