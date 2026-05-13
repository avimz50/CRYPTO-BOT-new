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

export function ActiveTradesTable({ trades, maxTrades, loading, stale, lastSuccessAt }: ActiveTradesTableProps) {
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
              {["COIN ↗", "DIR", "LEVERAGE", "AMOUNT", "ENTRY", "SL", "TP", "EST. PROFIT", "LIVE P&L", "STATUS"].map(h => (
                <th key={h} className="px-3 py-2 text-left font-semibold uppercase tracking-wide whitespace-nowrap"
                  style={{ color: "#64748b" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && trades.length === 0 ? (
              [...Array(2)].map((_, i) => (
                <tr key={i} style={{ borderBottom: "1px solid #0d1f3c" }}>
                  {[...Array(10)].map((_, j) => (
                    <td key={j} className="px-3 py-3">
                      <div className="h-3 rounded animate-pulse" style={{ background: "#0d1f3c", width: "60%" }} />
                    </td>
                  ))}
                </tr>
              ))
            ) : trades.length === 0 ? (
              <tr>
                <td colSpan={10} className="px-4 py-6 text-center text-xs"
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
                const pnlUsd = t.tp1_triggered
                  ? (t.tp1_pnl ?? 0) + posSize * pnlPct / 100
                  : posSize * pnlPct / 100;
                const tp1Price = (t as any).tp1 ?? t.tp;
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
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#cbd5e1" }}>{leverage}x</td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#cbd5e1" }}>${amount}</td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#cbd5e1" }}>{fmt(t.entry)}</td>
                    <td className="px-3 py-2.5 font-mono font-semibold" style={{ color: "#f87171" }}>
                      {fmt(t.sl)}
                    </td>
                    <td className="px-3 py-2.5 font-mono font-semibold" style={{ color: "#4ade80" }}>
                      {fmt(t.tp)}
                    </td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: "#93c5fd" }}>
                      {estProfit != null ? `+$${estProfit.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-3 py-2.5 font-mono font-bold"
                      style={{ color: pnlUsd >= 0 ? "#4ade80" : "#f87171" }}>
                      {pnlUsd >= 0 ? "+" : ""}{pnlUsd.toFixed(2)}$
                      <span className="text-xs font-normal ml-1"
                        style={{ color: pnlUsd >= 0 ? "#86efac" : "#fca5a5", opacity: 0.7 }}>
                        ({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%)
                      </span>
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
        </table>
      </div>
    </div>
  );
}
