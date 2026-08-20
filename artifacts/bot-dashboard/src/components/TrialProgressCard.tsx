import { useState } from "react";
import { ValidationTrialData, TrialTrade, resetValidationTrial } from "@/hooks/useBotData";
import { StaleBadge } from "./StaleBadge";

interface TrialProgressCardProps {
  data: ValidationTrialData | null;
  loading: boolean;
  stale?: boolean;
  lastSuccessAt?: number | null;
  onReset?: () => void;
}

function fmtUsd(n: number, signed = true): string {
  if (!isFinite(n)) return "—";
  const sign = n >= 0 ? (signed ? "+" : "") : "-";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

function resolveNetPnl(t: TrialTrade): number {
  if (isFinite(t.net_pnl_usd)) return t.net_pnl_usd;
  if (t.pnl_usd != null && isFinite(t.pnl_usd)) return t.pnl_usd;
  return 0;
}

function buildDirectionBreakdown(trades: TrialTrade[]) {
  const m: Record<string, { count: number; wins: number; net: number }> = {};
  for (const t of trades) {
    const k = t.direction ?? "UNKNOWN";
    if (!m[k]) m[k] = { count: 0, wins: 0, net: 0 };
    m[k].count++;
    const net = resolveNetPnl(t);
    if (net > 0) m[k].wins++;
    m[k].net += net;
  }
  return m;
}

function buildStrategyBreakdown(trades: TrialTrade[]) {
  const m: Record<string, { count: number; wins: number; net: number }> = {};
  for (const t of trades) {
    const k = t.track || t.strategy || "Unknown";
    if (!m[k]) m[k] = { count: 0, wins: 0, net: 0 };
    m[k].count++;
    const net = resolveNetPnl(t);
    if (net > 0) m[k].wins++;
    m[k].net += net;
  }
  return m;
}

function buildRegimeBreakdown(trades: TrialTrade[]) {
  const m: Record<string, { count: number; wins: number; net: number }> = {};
  for (const t of trades) {
    const k = t.market_regime ?? "Unknown";
    if (!m[k]) m[k] = { count: 0, wins: 0, net: 0 };
    m[k].count++;
    const net = resolveNetPnl(t);
    if (net > 0) m[k].wins++;
    m[k].net += net;
  }
  return m;
}

interface BreakdownRowProps {
  label: string;
  count: number;
  wins: number;
  net: number;
  color?: string;
}

function BreakdownRow({ label, count, wins, net }: BreakdownRowProps) {
  const wr = count > 0 ? Math.round(wins / count * 100) : 0;
  return (
    <div className="flex items-center justify-between text-xs py-0.5">
      <span className="font-semibold" style={{ color: "#93c5fd", minWidth: 80 }}>{label}</span>
      <span style={{ color: "#64748b" }}>{count}T · {wr}% WR</span>
      <span className="font-mono font-bold ml-2"
        style={{ color: net >= 0 ? "#4ade80" : "#f87171" }}>
        {fmtUsd(net)}
      </span>
    </div>
  );
}

type TabKey = "direction" | "strategy" | "regime";

export function TrialProgressCard({ data, loading, stale, lastSuccessAt, onReset }: TrialProgressCardProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("strategy");
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  if (loading && !data) {
    return (
      <div className="rounded-xl p-4 animate-pulse space-y-2"
        style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-4 rounded" style={{ background: "#0d1f3c", width: `${60 + i * 10}%` }} />
        ))}
      </div>
    );
  }

  if (!data) return null;

  const trades    = data.trades ?? [];
  const target    = data.target ?? 30;
  const count     = trades.length;
  const progress  = Math.min(count / target * 100, 100);

  // Aggregate: prefer summary fields if populated, fall back to computing from trades
  const s = data.summary ?? {};
  const totalNet   = typeof s.total_net_pnl_usd === "number" ? s.total_net_pnl_usd
                   : typeof s.total_pnl_usd === "number" ? s.total_pnl_usd
                   : trades.reduce((acc, t) => acc + resolveNetPnl(t), 0);
  const totalFees  = typeof s.total_fees_usd === "number" ? s.total_fees_usd
                   : trades.reduce((acc, t) => acc + (t.fees_usd ?? 0), 0);
  const wins       = typeof s.wins === "number" ? s.wins : trades.filter(t => resolveNetPnl(t) > 0).length;
  const netWinRate = typeof s.net_win_rate === "number" ? s.net_win_rate
                   : typeof s.win_rate === "number" ? s.win_rate
                   : count > 0 ? wins / count * 100 : 0;

  const dirBreak  = buildDirectionBreakdown(trades);
  const stratBreak = buildStrategyBreakdown(trades);
  const regBreak  = buildRegimeBreakdown(trades);

  const statusColor =
    data.status === "completed" ? "#4ade80" :
    data.status === "failed"    ? "#f87171" :
    "#facc15";

  const progressColor = progress >= 75 ? "#4ade80" : progress >= 40 ? "#facc15" : "#93c5fd";

  async function handleReset() {
    if (!window.confirm("Reset the validation trial? This cannot be undone.")) return;
    setResetting(true);
    setResetError(null);
    const result = await resetValidationTrial();
    setResetting(false);
    if (result.ok) {
      onReset?.();
    } else {
      setResetError(result.error ?? "Reset failed");
    }
  }

  const TABS: { key: TabKey; label: string }[] = [
    { key: "strategy", label: "Strategy" },
    { key: "direction", label: "Direction" },
    { key: "regime", label: "Regime" },
  ];

  const activeBreakdown =
    activeTab === "direction" ? dirBreak :
    activeTab === "regime"    ? regBreak :
    stratBreak;

  return (
    <div className="rounded-xl p-4 space-y-3"
      style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>

      {/* Title row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-xs font-bold uppercase tracking-widest" style={{ color: "#94a3b8" }}>
            Validation Trial
          </h2>
          {stale && <StaleBadge lastSuccessAt={lastSuccessAt ?? null} />}
          <span className="text-xs px-1.5 py-0.5 rounded font-semibold"
            style={{ color: statusColor, background: `${statusColor}18` }}>
            {data.status ?? "running"}
          </span>
        </div>
        {/* Reset — only shown when Flask supports it (i.e. we have data) */}
        <button
          onClick={handleReset}
          disabled={resetting}
          className="text-xs px-2 py-1 rounded transition-all"
          style={{
            background: "rgba(239,68,68,0.1)",
            border: "1px solid rgba(239,68,68,0.3)",
            color: resetting ? "#64748b" : "#f87171",
            cursor: resetting ? "not-allowed" : "pointer",
          }}
          onMouseEnter={e => !resetting && (e.currentTarget.style.background = "rgba(239,68,68,0.2)")}
          onMouseLeave={e => (e.currentTarget.style.background = "rgba(239,68,68,0.1)")}>
          {resetting ? "Resetting…" : "↺ Reset"}
        </button>
      </div>

      {resetError && (
        <div className="text-xs rounded px-2 py-1"
          style={{ background: "rgba(239,68,68,0.1)", color: "#f87171", border: "1px solid rgba(239,68,68,0.3)" }}>
          Reset failed: {resetError}
        </div>
      )}

      {/* Progress bar */}
      <div>
        <div className="flex justify-between items-baseline mb-1">
          <span className="text-xs" style={{ color: "#64748b" }}>
            Progress: {count} / {target} trades
          </span>
          <span className="text-xs font-mono font-bold" style={{ color: progressColor }}>
            {progress.toFixed(0)}%
          </span>
        </div>
        <div className="rounded-full overflow-hidden" style={{ background: "#0d1f3c", height: 8 }}>
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${progress}%`, background: progressColor }} />
        </div>
      </div>

      {/* Key metrics */}
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-lg p-2 text-center"
          style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
          <div className={`font-mono font-bold text-base`}
            style={{ color: totalNet >= 0 ? "#4ade80" : "#f87171" }}>
            {fmtUsd(totalNet)}
          </div>
          <div className="text-xs mt-0.5" style={{ color: "#64748b" }}>Net P&L</div>
        </div>
        <div className="rounded-lg p-2 text-center"
          style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
          <div className="font-mono font-bold text-base" style={{ color: "#93c5fd" }}>
            {netWinRate.toFixed(0)}%
          </div>
          <div className="text-xs mt-0.5" style={{ color: "#64748b" }}>Net Win Rate</div>
        </div>
        {totalFees > 0 && (
          <div className="rounded-lg p-2 text-center col-span-2"
            style={{ background: "#071222", border: "1px solid #1e3a5f" }}>
            <span className="text-xs" style={{ color: "#64748b" }}>
              Est. fees disclosed:&nbsp;
            </span>
            <span className="font-mono text-xs font-semibold" style={{ color: "#fbbf24" }}>
              ${totalFees.toFixed(2)}
            </span>
          </div>
        )}
      </div>

      {/* Breakdown tabs */}
      {trades.length > 0 && (
        <div>
          <div className="flex gap-1 mb-2">
            {TABS.map(({ key, label }) => (
              <button key={key}
                onClick={() => setActiveTab(key)}
                className="text-xs px-2 py-0.5 rounded transition-all"
                style={{
                  background: activeTab === key ? "rgba(59,130,246,0.2)" : "transparent",
                  color: activeTab === key ? "#93c5fd" : "#475569",
                  border: `1px solid ${activeTab === key ? "#3b82f6" : "transparent"}`,
                }}>
                {label}
              </button>
            ))}
          </div>
          <div className="space-y-0.5">
            {Object.entries(activeBreakdown).map(([k, v]) => (
              <BreakdownRow key={k} label={k} count={v.count} wins={v.wins} net={v.net} />
            ))}
            {Object.keys(activeBreakdown).length === 0 && (
              <div className="text-xs" style={{ color: "#475569" }}>No data yet</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
