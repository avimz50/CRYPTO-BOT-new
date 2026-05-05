import { AuditTrade } from "@/hooks/useBotData";

interface TradeHistoryModalProps {
  trades: AuditTrade[];
  loading: boolean;
  onClose: () => void;
}

const REASON_COLOR: Record<string, string> = {
  TP: "#4ade80", SL: "#f87171", BE: "#facc15", MANUAL: "#93c5fd",
};

function fmt(n?: number) {
  if (n == null) return "—";
  if (Math.abs(n) < 0.001) return n.toExponential(2);
  if (Math.abs(n) < 1) return n.toFixed(5);
  if (Math.abs(n) < 10000) return n.toFixed(2);
  return n.toLocaleString();
}

function fmtTime(raw?: string) {
  if (!raw) return "—";
  try {
    const d = new Date(raw);
    if (isNaN(d.getTime())) return raw;
    return d.toLocaleDateString("en-GB", { month: "2-digit", day: "2-digit" })
      + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return raw;
  }
}

export function TradeHistoryModal({ trades, loading, onClose }: TradeHistoryModalProps) {
  const totalPnl = trades.reduce((s, t) => s + (t.pnl ?? 0), 0);
  const wins = trades.filter(t => (t.pnl ?? 0) > 0).length;
  const winRate = trades.length > 0 ? (wins / trades.length * 100).toFixed(0) : "—";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(7,13,26,0.92)", backdropFilter: "blur(6px)" }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}>

      <div className="w-full max-w-4xl mx-4 flex flex-col rounded-2xl overflow-hidden"
        style={{
          maxHeight: "85vh",
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
            <div className="flex items-center gap-3 text-xs">
              <div className="text-center">
                <div className="font-mono font-bold"
                  style={{ color: totalPnl >= 0 ? "#4ade80" : "#f87171" }}>
                  {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)}$
                </div>
                <div style={{ color: "#64748b" }}>Total P&L</div>
              </div>
              <div className="text-center">
                <div className="font-mono font-bold" style={{ color: "#93c5fd" }}>{winRate}%</div>
                <div style={{ color: "#64748b" }}>Win Rate</div>
              </div>
              <div className="text-center">
                <div className="font-mono font-bold" style={{ color: "#4ade80" }}>{wins}</div>
                <div style={{ color: "#64748b" }}>Wins</div>
              </div>
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
                {["#", "PAIR", "DIR", "BUY TIME", "BUY PRICE", "SELL TIME", "SELL PRICE", "LVRG", "VOL", "P&L", "P&L %", "REASON"].map(h => (
                  <th key={h} className="px-3 py-2.5 text-left font-semibold uppercase tracking-wide whitespace-nowrap"
                    style={{ color: "#64748b" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && trades.length === 0 ? (
                [...Array(5)].map((_, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #0d1f3c" }}>
                    {[...Array(12)].map((_, j) => (
                      <td key={j} className="px-3 py-3">
                        <div className="h-3 rounded animate-pulse"
                          style={{ background: "#0d1f3c", width: "70%" }} />
                      </td>
                    ))}
                  </tr>
                ))
              ) : trades.length === 0 ? (
                <tr>
                  <td colSpan={12} className="px-4 py-10 text-center"
                    style={{ color: "#475569" }}>
                    No closed trades yet
                  </td>
                </tr>
              ) : (
                [...trades].reverse().map((t, i) => {
                  const pnl = t.pnl ?? 0;
                  const pnlPct = t.pnl_pct ?? 0;
                  const win = pnl > 0;
                  const reason = (t.close_reason ?? "—").toUpperCase();
                  const reasonColor = REASON_COLOR[reason] ?? "#94a3b8";
                  return (
                    <tr key={i}
                      style={{ borderBottom: "1px solid #0d1f3c", transition: "background 0.1s" }}
                      onMouseEnter={e => (e.currentTarget.style.background = "#0d1f3c")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                      <td className="px-3 py-2.5 font-mono" style={{ color: "#475569" }}>
                        {t.id ?? (trades.length - i)}
                      </td>
                      <td className="px-3 py-2.5 font-mono font-semibold" style={{ color: "#60a5fa" }}>
                        {t.symbol}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="font-bold text-xs"
                          style={{ color: t.direction === "LONG" ? "#4ade80" : "#f87171" }}>
                          {t.direction === "LONG" ? "▲ L" : "▼ S"}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap" style={{ color: "#64748b" }}>
                        {fmtTime(t.open_time)}
                      </td>
                      <td className="px-3 py-2.5 font-mono" style={{ color: "#cbd5e1" }}>
                        {fmt(t.entry_price)}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap" style={{ color: "#64748b" }}>
                        {fmtTime(t.close_time)}
                      </td>
                      <td className="px-3 py-2.5 font-mono" style={{ color: "#cbd5e1" }}>
                        {fmt(t.close_price)}
                      </td>
                      <td className="px-3 py-2.5 font-mono" style={{ color: "#94a3b8" }}>
                        {t.leverage != null ? `${t.leverage}x` : "—"}
                      </td>
                      <td className="px-3 py-2.5 font-mono" style={{ color: "#94a3b8" }}>
                        {t.pos_size != null ? `$${fmt(t.pos_size)}` : "—"}
                      </td>
                      <td className="px-3 py-2.5 font-mono font-bold whitespace-nowrap"
                        style={{ color: win ? "#4ade80" : pnl === 0 ? "#facc15" : "#f87171" }}>
                        {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}$
                      </td>
                      <td className="px-3 py-2.5 font-mono"
                        style={{ color: win ? "#86efac" : pnl === 0 ? "#fde68a" : "#fca5a5" }}>
                        {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="px-1.5 py-0.5 rounded text-xs font-bold"
                          style={{ color: reasonColor, background: "rgba(255,255,255,0.05)" }}>
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
