import { Sparkline } from "./Sparkline";

interface FinancialOverviewProps {
  starting: number | null;
  equity: number | null;
  realized: number;
  unrealized: number;
  equityHistory: number[];
  loading: boolean;
  onShowHistory: () => void;
  isMobile?: boolean;
}

function safe(n: number | null | undefined): number {
  return n != null && isFinite(n) ? n : 0;
}

function fmtUsd(n: number | null, signed = false): string {
  if (n == null || !isFinite(n)) return "—";
  const abs = Math.abs(n);
  const sign = n >= 0 ? (signed ? "+" : "") : "-";
  return `${sign}$${abs.toFixed(2)}`;
}

function fmtPct(n: number | null): string {
  if (n == null || !isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

export function FinancialOverview({
  starting, equity, realized, unrealized, equityHistory, loading, onShowHistory, isMobile,
}: FinancialOverviewProps) {
  const eq  = equity;
  const st  = starting;
  const rl  = safe(realized);
  const unr = safe(unrealized);

  const totalPnl = eq != null && st != null ? eq - st : null;
  const totalPct = totalPnl != null && st != null && st > 0 ? (totalPnl / st) * 100 : null;

  const isProfit     = (totalPnl ?? 0) >= 0;
  const equityColor  = eq == null ? "#94a3b8" : eq >= (st ?? eq) ? "#4ade80" : "#f87171";

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
          📜 {isMobile ? "History" : "Trade History"}
        </button>
      </div>

      {loading ? (
        <div className="animate-pulse space-y-2">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-6 rounded-lg" style={{ background: "#0d1f3c" }} />
          ))}
        </div>
      ) : (
        <div className={isMobile ? "space-y-2.5" : "grid gap-3"}
          style={isMobile ? {} : { gridTemplateColumns: "1fr auto" }}>

          {/* Numbers */}
          <div className="space-y-2">
            <div className="flex justify-between items-baseline">
              <span className="text-xs uppercase tracking-wide" style={{ color: "#64748b" }}>
                Starting Balance
              </span>
              <span className="font-mono font-bold" style={{ color: "#e2e8f0" }}>
                {fmtUsd(st)}
              </span>
            </div>

            <div className="flex justify-between items-center">
              <span className="text-xs uppercase tracking-wide" style={{ color: "#64748b" }}>
                Current Equity
              </span>
              <span
                className={`font-mono font-bold ${isMobile ? "text-xl" : "text-2xl"}`}
                style={{ color: equityColor }}>
                {fmtUsd(eq)}
              </span>
            </div>

            <div className="flex justify-between items-baseline">
              <span className="text-xs uppercase tracking-wide" style={{ color: "#64748b" }}>
                Total P&L
              </span>
              <span className="font-mono font-bold"
                style={{ color: isProfit ? "#4ade80" : "#f87171" }}>
                {fmtUsd(totalPnl, true)}
                {totalPct != null && ` (${fmtPct(totalPct)})`}
              </span>
            </div>

            <div className="grid grid-cols-2 gap-2 pt-2" style={{ borderTop: "1px solid #1e3a5f" }}>
              <div className="rounded-lg p-2 text-center"
                style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
                <div className="text-sm font-bold font-mono"
                  style={{ color: unr >= 0 ? "#4ade80" : "#f87171" }}>
                  {fmtUsd(unr, true)}
                </div>
                <div className="text-xs mt-0.5" style={{ color: "#64748b" }}>Floating P&L</div>
              </div>
              <div className="rounded-lg p-2 text-center"
                style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
                <div className="text-sm font-bold font-mono"
                  style={{ color: rl >= 0 ? "#4ade80" : "#f87171" }}>
                  {fmtUsd(rl, true)}
                </div>
                <div className="text-xs mt-0.5" style={{ color: "#64748b" }}>Realized P&L</div>
              </div>
            </div>
          </div>

          {/* Sparkline — desktop only */}
          {!isMobile && (
            <div className="flex flex-col items-center justify-end pb-1 pl-3">
              <div className="text-xs mb-1 text-center" style={{ color: "#334155" }}>Equity Curve</div>
              <Sparkline
                points={equityHistory.length >= 2 ? equityHistory : [safe(st), safe(eq)]}
                color={equityColor}
                width={130}
                height={44}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
