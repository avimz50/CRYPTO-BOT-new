import { BotTrade } from "@/hooks/useBotData";
import { StaleBadge } from "./StaleBadge";

interface ActiveTradesTableProps {
  trades: BotTrade[];
  maxTrades: number;
  loading: boolean;
  stale?: boolean;
  lastSuccessAt?: number | null;
}

function fmt(n: number) {
  if (n == null) return "—";
  if (Math.abs(n) < 0.001) return n.toExponential(2);
  if (Math.abs(n) < 1) return n.toFixed(4);
  if (Math.abs(n) < 10000) return n.toFixed(2);
  return n.toLocaleString();
}

function fmtUsd(n: number, signed = true): string {
  if (!isFinite(n)) return "—";
  const sign = n >= 0 ? (signed ? "+" : "") : "-";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

export function ActiveTradesTable({ trades, maxTrades, loading, stale, lastSuccessAt }: ActiveTradesTableProps) {
  // Compute totals for footer summary
  const hasFeeData = trades.some(t => t.estimated_fees_usd != null || t.net_pnl_usd != null);

  return (
    <div className="rounded-xl overflow-hidden"
      style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
      <div className="px-4 py-3 flex items-center justify-between"
        style={{ borderBottom: "1px solid #1e3a5f" }}>
        <div className="flex items-center gap-2">
          <h2 className="text-xs font-bold uppercase tracking-widest" style={{ color: "#94a3b8" }}>
            Active Trades
          </h2>
          {stale && <StaleBadge lastSuccessAt={lastSuccessAt ?? null} />}
        </div>
        <span className="text-xs px-2 py-0.5 rounded-full font-mono"
          style={{
            background: "rgba(59,130,246,0.15)",
            border: "1px solid #3b82f6",
            color: "#93c5fd",
          }}>
          {trades.length} / {maxTrades} · Max: {maxTrades}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr style={{ background: "#0d1f3c", borderBottom: "1px solid #1e3a5f" }}>
              {[
                "COIN ↗", "DIR", "TF", "LEVERAGE", "AMOUNT", "ENTRY", "CURR PRICE",
                "SL", "BE Target", "Final TP", "EST. PROFIT",
                "GROSS P&L", "EST. FEES", "NET P&L", "STATUS",
              ].map(h => (
                <th key={h} className="px-3 py-2 text-left font-semibold uppercase tracking-wide whitespace-nowrap"
                  style={{ color: h === "NET P&L" ? "#93c5fd" : "#64748b" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && trades.length === 0 ? (
              [...Array(2)].map((_, i) => (
                <tr key={i} style={{ borderBottom: "1px solid #0d1f3c" }}>
                  {[...Array(15)].map((_, j) => (
                    <td key={j} className="px-3 py-3">
                      <div className="h-3 rounded animate-pulse" style={{ background: "#0d1f3c", width: "60%" }} />
                    </td>
                  ))}
                </tr>
              ))
            ) : trades.length === 0 ? (
              <tr>
                <td colSpan={15} className="px-4 py-6 text-center text-xs"
                  style={{ color: "#475569" }}>
                  No active trades
                </td>
              </tr>
            ) : (
              trades.map((t, i) => {
                const isLong = t.direction === "LONG";
                const cp = t.current_price ?? t.entry;
                const rawPct = (cp - t.entry) / t.entry * 100;
                const pnlPct = isLong ? rawPct : -rawPct;
                const posSize = t.pos_size ?? 500;
                // After TP1: only 25% of position remains open; tp1_pnl is already realized in wallet
                const activeSize = t.tp1_triggered ? posSize * 0.25 : posSize;
                const computedPnl = t.tp1_triggered
                  ? (t.tp1_pnl ?? 0) + activeSize * pnlPct / 100
                  : activeSize * pnlPct / 100;

                // Prefer server-supplied gross/fee/net; fall back to computed
                const grossPnl = t.gross_pnl_usd ?? computedPnl;
                const estFees  = t.estimated_fees_usd ?? null;
                const netPnl   = t.net_pnl_usd ?? (estFees != null ? grossPnl - estFees : grossPnl);

                const tp1Price = ((t as unknown) as Record<string, unknown>).tp1 as number ?? t.tp;
                const estProfit = tp1Price && t.entry
                  ? Math.abs(tp1Price - t.entry) / t.entry * posSize
                  : null;
                const leverage = (t.leverage ?? Math.round(posSize / 50)) || 10;
                const amount = Math.round(posSize / leverage);
                const bgSymbol = t.symbol.replace("/", "");
                const bitgetUrl = `https://www.bitget.com/futures/usdt/${bgSymbol}`;

                return (
                  <tr key={i}
                    style={{ borderBottom: "1px solid #0d1f3c", transition: "background 0.1s" }}
                    onMouseEnter={e => (e.currentTarget.style.background = "#0d1f3c")}
                    onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                    <td className="px-3 py-2.5">
                      <a href={bitgetUrl} target="_blank" rel="noopener noreferrer"
                        className="font-mono font-semibold transition-colors"
                        style={{ color: "#60a5fa" }}
                        onMouseEnter={e => (e.currentTarget.style.color = "#93c5fd")}
                        onMouseLeave={e => (e.currentTarget.style.color = "#60a5fa")}>
                        {t.symbol}
                      </a>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="px-1.5 py-0.5 rounded text-xs font-bold"
                        style={{
                          color: isLong ? "#4ade80" : "#f87171",
                          background: isLong ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
                        }}>
                        {isLong ? "▲ LONG" : "▼ SHORT"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 font-mono font-semibold" style={{ color: "#93c5fd" }}>
                      {t.timeframe ?? "—"}
                    </td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#cbd5e1" }}>{leverage}x</td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#cbd5e1" }}>${amount}</td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#cbd5e1" }}>{fmt(t.entry)}</td>
                    <td className="px-3 py-2.5 font-mono font-semibold"
                      style={{ color: netPnl >= 0 ? "#4ade80" : "#f87171" }}>
                      {fmt(cp)}
                      <span className="ml-1 text-xs font-normal opacity-60">
                        {netPnl >= 0 ? "▲" : "▼"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#f87171" }}>
                      <span className="text-xs opacity-60 mr-0.5">🛑</span>
                      {fmt(t.sl)}
                    </td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#fbbf24" }}>
                      <span className="text-xs opacity-60 mr-0.5">🎯</span>
                      {fmt(tp1Price)}
                    </td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#4ade80" }}>
                      <span className="text-xs opacity-60 mr-0.5">🏆</span>
                      {fmt(t.tp)}
                    </td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#93c5fd" }}>
                      {estProfit != null ? `+$${estProfit.toFixed(2)}` : "—"}
                    </td>
                    {/* Gross P&L */}
                    <td className="px-3 py-2.5 font-mono"
                      style={{ color: grossPnl >= 0 ? "#86efac" : "#fca5a5" }}>
                      {fmtUsd(grossPnl)}
                      <span className="text-xs font-normal ml-1 opacity-60">
                        ({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%)
                      </span>
                    </td>
                    {/* Est. Fees */}
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#94a3b8" }}>
                      {estFees != null ? `-$${estFees.toFixed(2)}` : "—"}
                    </td>
                    {/* Net P&L — prominent */}
                    <td className="px-3 py-2.5 font-mono font-bold"
                      style={{ color: netPnl >= 0 ? "#4ade80" : "#f87171" }}>
                      {fmtUsd(netPnl)}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="flex items-center gap-1.5" style={{ color: "#4ade80" }}>
                        <span className="w-1.5 h-1.5 rounded-full"
                          style={{ background: "#4ade80", animation: "pulse 2s infinite" }} />
                        Running
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
          {/* Summary footer — only shown when fee data is available */}
          {hasFeeData && trades.length > 0 && (() => {
            const totalGross = trades.reduce((s, t) => {
              const cp = t.current_price ?? t.entry;
              const rawPct = (cp - t.entry) / t.entry * 100;
              const pnlPct = t.direction === "LONG" ? rawPct : -rawPct;
              const posSize = t.pos_size ?? 500;
              const activeSize = t.tp1_triggered ? posSize * 0.25 : posSize;
              const computed = t.tp1_triggered
                ? (t.tp1_pnl ?? 0) + activeSize * pnlPct / 100
                : activeSize * pnlPct / 100;
              return s + (t.gross_pnl_usd ?? computed);
            }, 0);
            const totalFees = trades.reduce((s, t) => s + (t.estimated_fees_usd ?? 0), 0);
            const totalNet  = trades.reduce((s, t) => {
              const cp = t.current_price ?? t.entry;
              const rawPct = (cp - t.entry) / t.entry * 100;
              const pnlPct = t.direction === "LONG" ? rawPct : -rawPct;
              const posSize = t.pos_size ?? 500;
              const activeSize = t.tp1_triggered ? posSize * 0.25 : posSize;
              const computed = t.tp1_triggered
                ? (t.tp1_pnl ?? 0) + activeSize * pnlPct / 100
                : activeSize * pnlPct / 100;
              const gross = t.gross_pnl_usd ?? computed;
              const fees  = t.estimated_fees_usd ?? 0;
              return s + (t.net_pnl_usd ?? gross - fees);
            }, 0);
            return (
              <tfoot>
                <tr style={{ background: "#071222", borderTop: "1px solid #1e3a5f" }}>
                  <td colSpan={11} className="px-3 py-2 text-xs font-semibold"
                    style={{ color: "#64748b" }}>
                    TOTAL (floating)
                  </td>
                  <td className="px-3 py-2 font-mono text-xs"
                    style={{ color: totalGross >= 0 ? "#86efac" : "#fca5a5" }}>
                    {fmtUsd(totalGross)}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs" style={{ color: "#94a3b8" }}>
                    {totalFees > 0 ? `-$${totalFees.toFixed(2)}` : "—"}
                  </td>
                  <td className="px-3 py-2 font-mono font-bold text-xs"
                    style={{ color: totalNet >= 0 ? "#4ade80" : "#f87171" }}>
                    {fmtUsd(totalNet)}
                  </td>
                  <td />
                </tr>
              </tfoot>
            );
          })()}
        </table>
      </div>
    </div>
  );
}
