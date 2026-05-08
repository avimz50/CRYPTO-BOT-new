import { useState } from "react";
import { ScanData, RejectedCoin, BubbleWatch } from "@/hooks/useBotData";
import { StaleBadge } from "./StaleBadge";

interface ScanStatusProps {
  scan: ScanData | null;
  loading: boolean;
  stale?: boolean;
  lastSuccessAt?: number | null;
}

type Filter = "ALL" | "BUY" | "SELL" | "HOLD";

function scoreToConfidence(score: number): { label: string; color: string } {
  if (score >= 85) return { label: "High",   color: "#4ade80" };
  if (score >= 78) return { label: "Medium", color: "#facc15" };
  return                    { label: "Low",    color: "#f87171" };
}

function relTime(raw: string | null): string {
  if (!raw) return "—";
  try {
    const ms = Date.now() - new Date(raw).getTime();
    if (ms < 0) return "just now";
    const s = Math.floor(ms / 1000);
    if (s < 60)  return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60)  return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24)  return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  } catch { return raw; }
}

// Bubble-watch entries become HOLD rows in the table
function bubbleToRow(b: BubbleWatch): { symbol: string; signal: "HOLD"; confidence: string; confColor: string; reason: string } {
  const pct = b.change_pct != null ? `${b.change_pct > 0 ? "+" : ""}${b.change_pct.toFixed(1)}%` : "";
  const vol  = b.volume_usd != null
    ? `Vol $${(b.volume_usd / 1_000).toFixed(0)}k`
    : "";
  const reason = [pct && `Price ${pct}`, vol].filter(Boolean).join(" · ");
  return { symbol: b.symbol, signal: "HOLD", confidence: "Watch", confColor: "#a78bfa", reason };
}

export function ScanStatus({ scan, loading, stale, lastSuccessAt }: ScanStatusProps) {
  const [filter, setFilter] = useState<Filter>("ALL");

  const rejected: RejectedCoin[]  = scan?.rejected_coins ?? [];
  const bubbles:  BubbleWatch[]   = scan?.bubble_watch   ?? [];
  const scanTime = scan?.scan_time ?? null;

  const FILTERS: { id: Filter; label: string; color: string; border: string; active: string }[] = [
    { id: "ALL",  label: "ALL",  color: "#93c5fd", border: "#3b82f6", active: "rgba(59,130,246,0.2)"  },
    { id: "BUY",  label: "BUY",  color: "#4ade80", border: "#22c55e", active: "rgba(34,197,94,0.2)"   },
    { id: "SELL", label: "SELL", color: "#f87171", border: "#ef4444", active: "rgba(239,68,68,0.2)"   },
    { id: "HOLD", label: "HOLD", color: "#a78bfa", border: "#7c3aed", active: "rgba(124,58,237,0.2)"  },
  ];

  type Row =
    | { type: "candidate"; coin: RejectedCoin }
    | { type: "bubble";    item: BubbleWatch };

  const allRows: Row[] = [
    ...rejected.map(c => ({ type: "candidate" as const, coin: c })),
    ...bubbles.map(b  => ({ type: "bubble"    as const, item: b })),
  ];

  const filtered: Row[] = filter === "ALL"
    ? allRows
    : filter === "BUY"
    ? rejected.filter(c => c.direction === "LONG").map(c  => ({ type: "candidate" as const, coin: c }))
    : filter === "SELL"
    ? rejected.filter(c => c.direction === "SHORT").map(c => ({ type: "candidate" as const, coin: c }))
    : bubbles.map(b => ({ type: "bubble" as const, item: b }));

  const isEmpty = filtered.length === 0;

  return (
    <div className="rounded-xl overflow-hidden"
      style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>

      {/* Header */}
      <div className="px-4 py-3 flex items-center justify-between"
        style={{ borderBottom: "1px solid #1e3a5f" }}>
        <div className="flex items-center gap-2">
          <h2 className="text-xs font-bold uppercase tracking-widest" style={{ color: "#94a3b8" }}>
            Last Scan
          </h2>
          {scanTime && (
            <span className="text-xs" style={{ color: "#475569" }}>
              · {relTime(scanTime)}
            </span>
          )}
          {stale && <StaleBadge lastSuccessAt={lastSuccessAt ?? null} />}
        </div>
        <div className="flex gap-1">
          {FILTERS.map(f => (
            <button key={f.id} onClick={() => setFilter(f.id)}
              className="px-2.5 py-1 rounded text-xs font-semibold transition-all"
              style={{
                background: filter === f.id ? f.active : "rgba(30,58,95,0.5)",
                color:      filter === f.id ? f.color  : "#64748b",
                border:     `1px solid ${filter === f.id ? f.border : "#1e3a5f"}`,
              }}>
              {f.label}
              {f.id === "HOLD" && bubbles.length > 0 && (
                <span className="ml-1 px-1 rounded" style={{ background: "rgba(124,58,237,0.25)", color: "#a78bfa", fontSize: 9 }}>
                  {bubbles.length}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Summary stats */}
      {scan && (
        <div className="grid grid-cols-2 gap-2 px-4 py-3"
          style={{ borderBottom: "1px solid #1e3a5f" }}>
          {[
            { label: "Scanned",    value: scan.total_scanned ?? 0, color: "#93c5fd" },
            { label: "Signals",    value: scan.signals_found  ?? 0, color: "#4ade80" },
            { label: "BTC Regime", value: scan.btc_regime ?? "—",
              color: scan.btc_regime === "BULL" ? "#4ade80" : scan.btc_regime === "BEAR" ? "#f87171" : "#facc15" },
            { label: "F&G",
              value: `${scan.fng_value} (${scan.fng_label ?? "—"})`,
              color: "#94a3b8" },
          ].map(({ label, value, color }) => (
            <div key={label} className="flex justify-between items-center text-xs">
              <span style={{ color: "#64748b" }}>{label}</span>
              <span style={{ color, fontFamily: "monospace", fontWeight: 600 }}>{value}</span>
            </div>
          ))}
          {scan.system_message && (
            <div className="col-span-2 text-xs" style={{ color: "#475569" }}>
              {scan.system_message}
            </div>
          )}
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto" style={{ maxHeight: 260, overflowY: "auto" }}>
        <table className="w-full text-xs">
          <thead className="sticky top-0" style={{ background: "#0d1f3c" }}>
            <tr style={{ borderBottom: "1px solid #1e3a5f" }}>
              {["COIN", "SIGNAL", "CONFIDENCE", "LAST SCANNED", "REASON"].map(h => (
                <th key={h} className="px-3 py-2 text-left font-semibold uppercase tracking-wide whitespace-nowrap"
                  style={{ color: "#64748b" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && allRows.length === 0 ? (
              [...Array(4)].map((_, i) => (
                <tr key={i} style={{ borderBottom: "1px solid #0d1f3c" }}>
                  {[...Array(5)].map((_, j) => (
                    <td key={j} className="px-3 py-2.5">
                      <div className="h-3 rounded animate-pulse"
                        style={{ background: "#0d1f3c", width: j === 4 ? "90%" : "55%" }} />
                    </td>
                  ))}
                </tr>
              ))
            ) : isEmpty ? (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-xs"
                  style={{ color: "#475569" }}>
                  {allRows.length === 0 ? "No scan data yet"
                    : filter === "HOLD" ? "No bubble-watch coins this scan"
                    : `No ${filter === "BUY" ? "long" : "short"} candidates`}
                </td>
              </tr>
            ) : (
              filtered.map((row, i) => {
                if (row.type === "bubble") {
                  const b    = row.item;
                  const bRow = bubbleToRow(b);
                  return (
                    <tr key={`b${i}`}
                      style={{ borderBottom: "1px solid #0d1f3c", transition: "background 0.1s" }}
                      onMouseEnter={e => (e.currentTarget.style.background = "#0d1f3c")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                      <td className="px-3 py-2.5 font-mono font-semibold" style={{ color: "#cbd5e1" }}>
                        {bRow.symbol}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="text-xs font-bold px-1.5 py-0.5 rounded"
                          style={{ color: "#a78bfa", background: "rgba(124,58,237,0.12)" }}>
                          ◈ HOLD
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="font-mono font-bold text-xs" style={{ color: bRow.confColor }}>
                          {bRow.confidence}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 font-mono whitespace-nowrap" style={{ color: "#64748b" }}>
                        {relTime(scanTime)}
                      </td>
                      <td className="px-3 py-2.5 text-xs" style={{ color: "#475569", maxWidth: 260 }}>
                        {bRow.reason}
                      </td>
                    </tr>
                  );
                }

                const c    = row.coin;
                const isBuy = c.direction === "LONG";
                const conf  = scoreToConfidence(c.best_score);

                return (
                  <tr key={`c${i}`}
                    style={{ borderBottom: "1px solid #0d1f3c", transition: "background 0.1s" }}
                    onMouseEnter={e => (e.currentTarget.style.background = "#0d1f3c")}
                    onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                    <td className="px-3 py-2.5 font-mono font-semibold" style={{ color: "#cbd5e1" }}>
                      {c.symbol}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="text-xs font-bold px-1.5 py-0.5 rounded"
                        style={{
                          color: isBuy ? "#4ade80" : "#f87171",
                          background: isBuy ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
                        }}>
                        {isBuy ? "▲ BUY" : "▼ SELL"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="font-mono font-bold text-xs" style={{ color: conf.color }}>
                        {c.best_score}
                      </span>
                      <span className="text-xs ml-1" style={{ color: conf.color, opacity: 0.7 }}>
                        {conf.label}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 font-mono whitespace-nowrap" style={{ color: "#64748b" }}>
                      {relTime(scanTime)}
                    </td>
                    <td className="px-3 py-2.5 text-xs" style={{ color: "#475569", maxWidth: 260 }}>
                      {c.reason ?? "—"}
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
