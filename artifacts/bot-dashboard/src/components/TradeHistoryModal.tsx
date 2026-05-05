import { AuditTrade } from "@/hooks/useBotData";

interface TradeHistoryModalProps {
  trades: AuditTrade[];
  loading: boolean;
  onClose: () => void;
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

function calcPnlPct(t: AuditTrade): number | null {
  // If dist_tp_pct and rr_achieved exist, we can derive achieved % movement
  // But most reliable is: price change / entry × 100, direction-adjusted
  if (!t.entry_price || !t.close_price) return null;
  const raw = (t.close_price - t.entry_price) / t.entry_price * 100;
  return t.direction === "LONG" ? raw : -raw;
}

export function TradeHistoryModal({ trades, loading, onClose }: TradeHistoryModalProps) {
  const totalPnl = trades.reduce((s, t) => s + (isFinite(t.pnl_usd) ? t.pnl_usd : 0), 0);
  const wins     = trades.filter(t => t.pnl_usd > 0).length;
  const losses   = trades.filter(t => t.pnl_usd < 0).length;
  const winRate  = trades.length > 0 ? (wins / trades.length * 100).toFixed(0) : "—";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(7,13,26,0.92)", backdropFilter: "blur(6px)" }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>

      <div className="w-full max-w-5xl flex flex-col rounded-2xl overflow-hidden"
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
          </div>

          <div className="flex items-center gap-4">
            <div className="flex items-center gap-4 text-xs">
              {[
                { label: "Total P&L", value: `${totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}$`, color: totalPnl >= 0 ? "#4ade80" : "#f87171" },
                { label: "Win Rate",  value: `${winRate}%`,  color: "#93c5fd" },
                { label: "Wins",      value: String(wins),   color: "#4ade80" },
                { label: "Losses",    value: String(losses), color: "#f87171" },
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

        {/* Table */}
        <div className="overflow-auto flex-1">
          <table className="w-full text-xs">
            <thead className="sticky top-0" style={{ background: "#0d1f3c" }}>
              <tr style={{ borderBottom: "1px solid #1e3a5f" }}>
                {["#", "PAIR", "DIR", "OPEN TIME", "ENTRY", "CLOSE TIME", "EXIT", "SCORE", "P&L $", "P&L %", "REASON"].map(h => (
                  <th key={h} className="px-3 py-2.5 text-left font-semibold uppercase tracking-wide whitespace-nowrap"
                    style={{ color: "#64748b" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && trades.length === 0 ? (
                [...Array(5)].map((_, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #0d1f3c" }}>
                    {[...Array(11)].map((_, j) => (
                      <td key={j} className="px-3 py-3">
                        <div className="h-3 rounded animate-pulse"
                          style={{ background: "#0d1f3c", width: "65%" }} />
                      </td>
                    ))}
                  </tr>
                ))
              ) : trades.length === 0 ? (
                <tr>
                  <td colSpan={11} className="px-4 py-10 text-center"
                    style={{ color: "#475569" }}>
                    No closed trades yet
                  </td>
                </tr>
              ) : (
                [...trades].reverse().map((t, i) => {
                  const pnl    = isFinite(t.pnl_usd) ? t.pnl_usd : 0;
                  const win    = pnl > 0;
                  const pnlPct = calcPnlPct(t);
                  const reason = (t.close_reason ?? "—").toUpperCase();
                  const badge  = reasonBadge(reason);

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
                        {t.score ?? "—"}
                      </td>
                      <td className="px-3 py-2.5 font-mono font-bold whitespace-nowrap"
                        style={{ color: win ? "#4ade80" : pnl === 0 ? "#facc15" : "#f87171" }}>
                        {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}$
                      </td>
                      <td className="px-3 py-2.5 font-mono"
                        style={{ color: win ? "#86efac" : pnl === 0 ? "#fde68a" : "#fca5a5" }}>
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
