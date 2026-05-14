import { useEffect, useState, useCallback } from "react";

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
}

export interface AuditTrade {
  symbol: string;
  direction: "LONG" | "SHORT";
  timeframe?: string;
  leverage?: number;
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

/** Flask /api/fng_settings threshold fields */
export interface FngSettings {
  extreme_fear: number;
  fear: number;
  greed: number;
  ranges?: {
    extreme_fear?: { min: number; max: number; desc: string };
    fear?:         { min: number; max: number; desc: string };
    greed?:        { min: number; max: number; desc: string };
  };
}

export interface BotLogData {
  stdout: string;
  stderr: string;
  crash: string | null;
}

// ── localStorage — client-side persistence for settings Flask doesn't expose ─
const LS_SETTINGS_KEY = "botDashboard_settings_v1";
export interface LocalSettingsCache {
  amount_per_trade: number;
  default_leverage: number;
  max_trades?: number;
}
const LS_DEFAULTS: LocalSettingsCache = { amount_per_trade: 50, default_leverage: 10, max_trades: 3 };

export function lsReadSettings(): LocalSettingsCache {
  try {
    const raw = localStorage.getItem(LS_SETTINGS_KEY);
    if (!raw) return { ...LS_DEFAULTS };
    const p = JSON.parse(raw) as Partial<LocalSettingsCache>;
    return {
      amount_per_trade: typeof p.amount_per_trade === "number" ? p.amount_per_trade : LS_DEFAULTS.amount_per_trade,
      default_leverage: typeof p.default_leverage === "number" ? p.default_leverage : LS_DEFAULTS.default_leverage,
      max_trades:       typeof p.max_trades       === "number" ? p.max_trades       : LS_DEFAULTS.max_trades,
    };
  } catch { return { ...LS_DEFAULTS }; }
}

export function lsWriteSettings(v: LocalSettingsCache): void {
  try { localStorage.setItem(LS_SETTINGS_KEY, JSON.stringify(v)); } catch { /* ignore */ }
}

// ── Poll result ────────────────────────────────────────────────
export interface PollResult<T> {
  data: T | null;
  loading: boolean;
  error: boolean;
  /** true when data exists but last successful fetch was > 1× the poll interval ago */
  stale: boolean;
  /** timestamp (ms) of the last successful fetch, or null if never fetched */
  lastSuccessAt: number | null;
  refetch: () => void;
}

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

  const stale = data != null && lastOk != null && (Date.now() - lastOk) > interval;
  return { data, loading, error, stale, lastSuccessAt: lastOk, refetch };
}

// ── Public hooks ───────────────────────────────────────────────
export function useStatus()      { return usePoll<StatusData>("/api/status",        10_000); }
export function useTrades()      { return usePoll<TradesData>("/api/trades",        10_000); }
export function useScan()        { return usePoll<ScanData>("/api/last_scan",       30_000); }
export function useSlots()       { return usePoll<SlotsData>("/api/slots",          30_000); }
export function useWallet()      { return usePoll<WalletData>("/api/wallet",        30_000); }
export function useFngSettings() { return usePoll<FngSettings>("/api/fng_settings", 60_000); }
export function useAudit(enabled: boolean)  { return usePoll<AuditData>("/api/trade_audit", 60_000, enabled); }
export function useBotLog(enabled: boolean) { return usePoll<BotLogData>("/api/bot_log",      5_000, enabled); }

// ── Actions ────────────────────────────────────────────────────

/** POST /api/sync — triggers /status message to Telegram from the bot */
export async function syncTelegram(): Promise<boolean> {
  try {
    const r = await fetch("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    return r.ok;
  } catch { return false; }
}

/** POST /api/slots — update max_trades in the Flask bot */
export async function saveSlots(max_trades: number): Promise<boolean> {
  // Persist locally first — this always succeeds and is the primary cache
  const cached = lsReadSettings();
  lsWriteSettings({ ...cached, max_trades });

  // Fire-and-forget to Flask (best-effort; production URL may not accept POSTs)
  fetch("/api/slots", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ max_trades }),
  }).catch(() => {/* ignore */});

  return true;
}

/**
 * POST /api/fng_settings — sends ALL bot settings in one call.
 * Persists everything to localStorage first (primary cache), then
 * fires a best-effort POST to Flask (production URL may not accept POSTs).
 */
export async function saveBotSettings(payload: {
  fng: Partial<FngSettings>;
  amount_per_trade: number;
  default_leverage: number;
}): Promise<boolean> {
  // Write to localStorage as primary persistence
  const cached = lsReadSettings();
  lsWriteSettings({ ...cached, amount_per_trade: payload.amount_per_trade, default_leverage: payload.default_leverage });

  // Fire-and-forget to Flask (best-effort)
  fetch("/api/fng_settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      extreme_fear:     payload.fng.extreme_fear,
      fear:             payload.fng.fear,
      greed:            payload.fng.greed,
      amount_per_trade: payload.amount_per_trade,
      default_leverage: payload.default_leverage,
    }),
  }).catch(() => {/* ignore */});

  return true;
}
