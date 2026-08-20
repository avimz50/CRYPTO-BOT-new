import { AuditTrade } from "@/hooks/useBotData";
import { StaleBadge } from "./StaleBadge";

interface TradeHistoryModalProps {
  trades: AuditTrade[];
  loading: boolean;
  onClose: () => void;
  stale?: boolean;
  lastSuccessAt?: number | null;
}

const REASON_BADGE: Record<string, { color: string; bg: string }> = {
  TP:         { color: "#4ade80", bg: "rgba(34,197,94,0.12)" },
  SL:         { color: "#f87171", bg: "rgba(239,68,68,0.12)" },
  BE:         { color: "#facc15", bg: "rgba(250,204,21,0.12)" },
  MANUAL:     { color: "#93c5fd", bg: "rgba(59,130,246,0.12)" },
  STAGNATION: { color: "#94a3b8", bg: "rgba(148,163,184,0.1)" },
};

function reasonBadge(r: string) {
  const key = r.toUpperCase();
  return REASON_BADGE[key] ?? { color: "#94a3b8", bg: "rgba(148,163,184,0.08)" };
}

function fmtPrice(n: number | undefined) {
  if (n == null || !isFinite(n)) return "—";
  if (Math.abs(n) < 0.0001) return n.toExponential(2);
  if (Math.abs(n) < 1) return n.toFixed(5);
  if (Math.abs(n) < 10_000) return n.toFixed(2);
  return n.toLocaleString();
}

function fmtTime(raw: string | undefined) {
  if (!raw) return "—";
  try {
    const d = new Date(raw);
    if (isNaN(d.getTime())) return raw;
    const month = String(d.getMonth() + 1).padStart(2, "0");
    const day   = String(d.getDate()).padStart(2, "0");
    const hh    = String(d.getHours()).padStart(2, "0");
    const mm    = String(d.getMinutes()).padStart(2, "0");
    return `${month}/${day} ${hh}:${mm}`;
  } catch { return raw; }
}

function fmtDuration(minutes: number | undefined) {
  if (minutes == null || !isFinite(minutes)) return "—";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h === 0) return `${m}m`;
  if (h < 24)  return `${h}h ${m}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

function calcPnlPct(t: AuditTrade): number | null {
  if (!t.entry_price || !t.close_price) return null;
  const raw = (t.close_price - t.entry_price) / t.entry_price * 100;
  return t.direction === "LONG" ? raw : -raw;
}

function fmtLeverage(t: AuditTrade): string {
  return t.leverage != null && isFinite(t.leverage) ? `${t.leverage}x` : "—";
}

/** Resolve the best available net P&L value for a trade */
function resolveNetPnl(t: AuditTrade): number {
  if (t.net_pnl_usd != null && isFinite(t.net_pnl_usd)) return t.net_pnl_usd;
  if (isFinite(t.pnl_usd)) return t.pnl_usd;
  return 0;
}

/** Resolve gross P&L (before fees) */
function resolveGrossPnl(t: AuditTrade): number | null {
  if (t.gross_pnl_usd != null && isFinite(t.gross_pnl_usd)) return t.gross_pnl_usd;
  return null;
}

const STRATEGY_ORDER = ["Swing", "Scalp", "Breakout", "Velocity", "Research"];
const STRATEGY_ICON: Record<string, string> = {
  Swing: "🌊", Scalp: "⚡", Breakout: "🚀", Velocity: "💨", Research: "🔬",
};

interface StratStats { count: number; wins: number; pnl: number; }

function buildStrategyStats(trades: AuditTrade[]): Record<string, StratStats> {
  const out: Record<string, StratStats> = {};
  for (const t of trades) {
    const key = t.track || t.strategy || "Swing";
    if (!out[key]) out[key] = { count: 0, wins: 0, pnl: 0 };
    out[key].count++;
    const net = resolveNetPnl(t);
    if (net > 0) out[key].wins++;
    out[key].pnl += net;
  }
  return out;
}

export function TradeHistoryModal({ trades, loading, onClose, stale, lastSuccessAt }: TradeHistoryModalProps) {
  // Use net P&L for all aggregate stats
  const totalNetPnl   = trades.reduce((s, t) => s + resolveNetPnl(t), 0);
  const totalGrossPnl = trades.reduce((s, t) => {
    const g = resolveGrossPnl(t);
    return s + (g ?? resolveNetPnl(t));
  }, 0);
  const totalFees     = trades.reduce((s, t) => s + (t.fees_usd != null && isFinite(t.fees_usd) ? t.fees_usd : 0), 0);
  const hasFeeData    = trades.some(t => t.fees_usd != null || t.gross_pnl_usd != null);

  const wins    = trades.filter(t => resolveNetPnl(t) > 0).length;
  const losses  = trades.filter(t => resolveNetPnl(t) < 0).length;
  const winRate = trades.length > 0 ? (wins / trades.length * 100).toFixed(0) : "—";

  const stratStats = buildStrategyStats(trades);
  const stratKeys  = [
    ...STRATEGY_ORDER.filter(k => stratStats[k]),
    ...Object.keys(stratStats).filter(k => !STRATEGY_ORDER.includes(k)),
  ];

  const COLS = hasFeeData
    ? ["#", "PAIR", "DIR", "OPEN TIME", "ENTRY", "CLOSE TIME", "EXIT",
       "LEVERAGE", "VOLUME", "DURATION", "GROSS P&L", "FEES", "NET P&L", "P&L %", "REASON"]
    : ["#", "PAIR", "DIR", "OPEN TIME", "ENTRY", "CLOSE TIME", "EXIT",
       "LEVERAGE", "VOLUME", "DURATION", "NET P&L", "P&L %", "REASON"];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(7,13,26,0.92)", backdropFilter: "blur(6px)" }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>

      <div className="w-full max-w-6xl flex flex-col rounded-2xl overflow-hidden"
        style={{
          maxHeight: "90vh",
          background: "#0a1628",
          border: "1px solid #1e3a5f",
          boxShadow: "0 25px 60px rgba(0,0,0,0.6)",
        }}>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4"
          style={{ borderBottom: "1px solid #1e3a5f" }}>
          <div className="flex items-center gap-3">
            <span className="text-lg">📜</span>
            <h2 className="font-bold" style={{ color: "#e2e8f0" }}>Full Trade History</h2>
            <span className="text-xs px-2 py-0.5 rounded-full"
              style={{ background: "#0d1f3c", color: "#64748b" }}>
              {trades.length} trades
            </span>
            {stale && <StaleBadge lastSuccessAt={lastSuccessAt ?? null} />}
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-4 text-xs">
              {[
                { label: "Net P&L",  value: `${totalNetPnl >= 0 ? "+" : ""}${totalNetPnl.toFixed(2)}$`,   color: totalNetPnl >= 0 ? "#4ade80" : "#f87171" },
                ...(hasFeeData ? [
                  { label: "Est. Fees", value: totalFees > 0 ? `-$${totalFees.toFixed(2)}` : "$0.00",   color: "#94a3b8" },
                ] : []),
                { label: "Win Rate", value: `${winRate}%`,  color: "#93c5fd" },
                { label: "Wins",     value: String(wins),   color: "#4ade80" },
                { label: "Losses",   value: String(losses), color: "#f87171" },
              ].map(({ label, value, color }) => (
                <div key={label} className="text-center hidden sm:block">
                  <div className="font-mono font-bold" style={{ color }}>{value}</div>
                  <div style={{ color: "#64748b" }}>{label}</div>
                </div>
              ))}
            </div>
            <button onClick={onClose}
              className="w-7 h-7 rounded-full flex items-center justify-center text-sm transition-colors"
              style={{ background: "#0d1f3c", color: "#94a3b8" }}
              onMouseEnter={e => { e.currentTarget.style.background = "#1e3a5f"; e.currentTarget.style.color = "#e2e8f0"; }}
              onMouseLeave={e => { e.currentTarget.style.background = "#0d1f3c"; e.currentTarget.style.color = "#94a3b8"; }}>
              ✕
            </button>
          </div>
        </div>

        {/* Fee disclosure banner — shown when fee data is available */}
        {hasFeeData && (
          <div className="px-5 py-2 flex items-center gap-2 text-xs"
            style={{ background: "rgba(148,163,184,0.06)", borderBottom: "1px solid #1e3a5f" }}>
            <span style={{ color: "#94a3b8" }}>ℹ️</span>
            <span style={{ color: "#94a3b8" }}>
              P&amp;L shown as net (after fees). Gross P&amp;L and estimated exchange fees are disclosed per trade.
              Total estimated fees: <span className="font-mono font-semibold" style={{ color: "#fbbf24" }}>
                {totalFees > 0 ? `$${totalFees.toFixed(2)}` : "$0.00"}
              </span>
              {totalGrossPnl !== totalNetPnl && (
                <> · Gross: <span className="font-mono font-semibold"
                  style={{ color: totalGrossPnl >= 0 ? "#86efac" : "#fca5a5" }}>
                  {totalGrossPnl >= 0 ? "+" : ""}{totalGrossPnl.toFixed(2)}$
                </span></>
              )}
            </span>
          </div>
        )}

        {/* Strategy Breakdown */}
        {stratKeys.length > 0 && (
          <div className="px-5 py-3 flex flex-wrap gap-3"
            style={{ borderBottom: "1px solid #1e3a5f", background: "#071222" }}>
            <span className="text-xs font-semibold self-center mr-1" style={{ color: "#64748b" }}>
              BY STRATEGY
            </span>
            {stratKeys.map(key => {
              const s = stratStats[key];
              const wr = s.count > 0 ? Math.round(s.wins / s.count * 100) : 0;
              const pnlPos = s.pnl >= 0;
              const icon = STRATEGY_ICON[key] ?? "📊";
              return (
                <div key={key}
                  className="flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs"
                  style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                  <span>{icon}</span>
                  <span className="font-semibold" style={{ color: "#93c5fd" }}>{key}</span>
                  <span style={{ color: "#475569" }}>·</span>
                  <span style={{ color: "#94a3b8" }}>{s.count}T</span>
                  <span style={{ color: "#475569" }}>·</span>
                  <span style={{ color: wr >= 50 ? "#4ade80" : "#f87171" }}>{wr}%</span>
                  <span style={{ color: "#475569" }}>·</span>
                  <span className="font-mono font-bold"
                    style={{ color: pnlPos ? "#4ade80" : "#f87171" }}>
                    {pnlPos ? "+" : ""}{s.pnl.toFixed(2)}$
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {/* Table */}
        <div className="overflow-auto flex-1">
          <table className="w-full text-xs">
            <thead className="sticky top-0" style={{ background: "#0d1f3c" }}>
              <tr style={{ borderBottom: "1px solid #1e3a5f" }}>
                {COLS.map(h => (
                  <th key={h}
                    className="px-3 py-2.5 text-left font-semibold uppercase tracking-wide whitespace-nowrap"
                    style={{ color: h === "NET P&L" ? "#93c5fd" : "#64748b" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && trades.length === 0 ? (
                [...Array(5)].map((_, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #0d1f3c" }}>
                    {COLS.map((_, j) => (
                      <td key={j} className="px-3 py-3">
                        <div className="h-3 rounded animate-pulse"
                          style={{ background: "#0d1f3c", width: "65%" }} />
                      </td>
                    ))}
                  </tr>
                ))
              ) : trades.length === 0 ? (
                <tr>
                  <td colSpan={COLS.length} className="px-4 py-10 text-center"
                    style={{ color: "#475569" }}>
                    No closed trades yet
                  </td>
                </tr>
              ) : (
                [...trades].reverse().map((t, i) => {
                  const netPnl  = resolveNetPnl(t);
                  const grossPnl = resolveGrossPnl(t);
                  const fees    = t.fees_usd != null && isFinite(t.fees_usd) ? t.fees_usd : null;
                  const win     = netPnl > 0;
                  const pnlPct  = calcPnlPct(t);
                  const reason  = (t.close_reason ?? "—").toUpperCase();
                  const badge   = reasonBadge(reason);

                  return (
                    <tr key={i}
                      style={{ borderBottom: "1px solid #0d1f3c", transition: "background 0.1s" }}
                      onMouseEnter={e => (e.currentTarget.style.background = "#0d1f3c")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                      <td className="px-3 py-2.5 font-mono" style={{ color: "#475569" }}>
                        {trades.length - i}
                      </td>
                      <td className="px-3 py-2.5 font-mono font-semibold" style={{ color: "#60a5fa" }}>
                        {t.symbol}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="font-bold"
                          style={{ color: t.direction === "LONG" ? "#4ade80" : "#f87171" }}>
                          {t.direction === "LONG" ? "▲L" : "▼S"}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap" style={{ color: "#64748b" }}>
                        {fmtTime(t.opened_at)}
                      </td>
                      <td className="px-3 py-2.5 font-mono" style={{ color: "#cbd5e1" }}>
                        {fmtPrice(t.entry_price)}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap" style={{ color: "#64748b" }}>
                        {fmtTime(t.closed_at)}
                      </td>
                      <td className="px-3 py-2.5 font-mono" style={{ color: "#cbd5e1" }}>
                        {fmtPrice(t.close_price)}
                      </td>
                      <td className="px-3 py-2.5 font-mono" style={{ color: "#94a3b8" }}>
                        {fmtLeverage(t)}
                      </td>
                      {/* Volume = position size in USD; not in current /api/trade_audit payload */}
                      <td className="px-3 py-2.5 font-mono" style={{ color: "#475569" }}>—</td>
                      <td className="px-3 py-2.5 font-mono whitespace-nowrap" style={{ color: "#94a3b8" }}>
                        {fmtDuration(t.duration_min)}
                      </td>
                      {/* Gross P&L — only when fee data is present */}
                      {hasFeeData && (
                        <td className="px-3 py-2.5 font-mono whitespace-nowrap"
                          style={{ color: (grossPnl ?? netPnl) >= 0 ? "#86efac" : "#fca5a5" }}>
                          {grossPnl != null
                            ? `${grossPnl >= 0 ? "+" : ""}${grossPnl.toFixed(2)}$`
                            : `${netPnl >= 0 ? "+" : ""}${netPnl.toFixed(2)}$`}
                        </td>
                      )}
                      {/* Fees — only when fee data is present */}
                      {hasFeeData && (
                        <td className="px-3 py-2.5 font-mono whitespace-nowrap"
                          style={{ color: "#94a3b8" }}>
                          {fees != null ? `-$${fees.toFixed(2)}` : "—"}
                        </td>
                      )}
                      {/* Net P&L — primary, always shown, prominent */}
                      <td className="px-3 py-2.5 font-mono font-bold whitespace-nowrap"
                        style={{ color: win ? "#4ade80" : netPnl === 0 ? "#facc15" : "#f87171" }}>
                        {netPnl >= 0 ? "+" : ""}{netPnl.toFixed(2)}$
                      </td>
                      <td className="px-3 py-2.5 font-mono"
                        style={{ color: win ? "#86efac" : netPnl === 0 ? "#fde68a" : "#fca5a5" }}>
                        {pnlPct != null
                          ? `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%`
                          : "—"}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="px-1.5 py-0.5 rounded text-xs font-bold"
                          style={{ color: badge.color, background: badge.bg }}>
                          {reason}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
