import { Sparkline } from "./Sparkline";

interface FinancialOverviewProps {
  starting: number;
  equity: number;
  realized: number;
  unrealized: number;
  equityHistory: number[];
  loading: boolean;
  onShowHistory: () => void;
}

export function FinancialOverview({
  starting, equity, realized, unrealized, equityHistory, loading, onShowHistory
}: FinancialOverviewProps) {
  const totalPnl = equity - starting;
  const totalPct = starting > 0 ? (totalPnl / starting) * 100 : 0;
  const isProfit = totalPnl >= 0;
  const equityColor = equity >= starting ? "#4ade80" : "#f87171";

  function fmt(n: number, prefix = true) {
    const sign = n >= 0 ? (prefix ? "+" : "") : "-";
    return `${sign}$${Math.abs(n).toFixed(2)}`;
  }

  return (
    <div className="rounded-xl p-4" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-bold uppercase tracking-widest" style={{ color: "#94a3b8" }}>
          Financial Overview
        </h2>
        <button onClick={onShowHistory}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs transition-all"
          style={{
            background: "rgba(59,130,246,0.1)",
            border: "1px solid rgba(59,130,246,0.3)",
            color: "#93c5fd",
          }}
          onMouseEnter={e => (e.currentTarget.style.background = "rgba(59,130,246,0.2)")}
          onMouseLeave={e => (e.currentTarget.style.background = "rgba(59,130,246,0.1)")}>
          📜 Trade History
        </button>
      </div>

      {loading ? (
        <div className="grid gap-3 animate-pulse" style={{ gridTemplateColumns: "1fr 1fr" }}>
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-8 rounded-lg" style={{ background: "#0d1f3c" }} />
          ))}
        </div>
      ) : (
        <div className="grid gap-3" style={{ gridTemplateColumns: "1fr auto" }}>
          <div className="space-y-2.5">
            <div className="flex justify-between items-baseline">
              <span className="text-xs uppercase tracking-wide" style={{ color: "#64748b" }}>
                Starting Balance
              </span>
              <span className="font-mono font-bold" style={{ color: "#e2e8f0" }}>
                ${starting.toFixed(2)}
              </span>
            </div>

            <div className="flex justify-between items-center">
              <span className="text-xs uppercase tracking-wide" style={{ color: "#64748b" }}>
                Current Equity
              </span>
              <span className="font-mono font-bold text-2xl" style={{ color: equityColor }}>
                ${equity.toFixed(2)}
              </span>
            </div>

            <div className="flex justify-between items-baseline">
              <span className="text-xs uppercase tracking-wide" style={{ color: "#64748b" }}>
                Total P&L
              </span>
              <span className="font-mono font-bold" style={{ color: isProfit ? "#4ade80" : "#f87171" }}>
                {fmt(totalPnl)} ({totalPct >= 0 ? "+" : ""}{totalPct.toFixed(1)}%)
              </span>
            </div>

            <div className="grid grid-cols-2 gap-2 mt-1 pt-2"
              style={{ borderTop: "1px solid #1e3a5f" }}>
              <div className="rounded-lg p-2 text-center"
                style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
                <div className="text-sm font-bold font-mono"
                  style={{ color: unrealized >= 0 ? "#4ade80" : "#f87171" }}>
                  {fmt(unrealized)}
                </div>
                <div className="text-xs mt-0.5" style={{ color: "#64748b" }}>Floating P&L</div>
              </div>
              <div className="rounded-lg p-2 text-center"
                style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
                <div className="text-sm font-bold font-mono"
                  style={{ color: realized >= 0 ? "#4ade80" : "#f87171" }}>
                  {fmt(realized)}
                </div>
                <div className="text-xs mt-0.5" style={{ color: "#64748b" }}>Realized P&L</div>
              </div>
            </div>
          </div>

          <div className="flex flex-col items-center justify-end pb-1 pl-3">
            <div className="text-xs mb-1 text-center" style={{ color: "#334155" }}>Equity Curve</div>
            <Sparkline
              points={equityHistory.length >= 2 ? equityHistory : [starting, equity]}
              color={equityColor}
              width={130}
              height={44}
            />
          </div>
        </div>
      )}
    </div>
  );
}
