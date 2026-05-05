import { useState } from "react";
import { ScanData, RejectedCoin } from "@/hooks/useBotData";

interface ScanStatusProps {
  scan: ScanData | null;
  loading: boolean;
}

type Filter = "ALL" | "LONG" | "SHORT";

function scoreColor(score: number) {
  if (score >= 85) return { color: "#4ade80", label: "High" };
  if (score >= 78) return { color: "#facc15", label: "Med" };
  return { color: "#f87171", label: "Low" };
}

function relTime(raw: string | null): string {
  if (!raw) return "—";
  try {
    const ms = Date.now() - new Date(raw).getTime();
    if (ms < 0) return "just now";
    const s = Math.floor(ms / 1000);
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  } catch { return raw; }
}

function ScanSummary({ scan }: { scan: ScanData }) {
  return (
    <div className="grid grid-cols-2 gap-2 px-4 py-3" style={{ borderBottom: "1px solid #1e3a5f" }}>
      {[
        { label: "Scanned",   value: scan.total_scanned ?? 0,    color: "#93c5fd" },
        { label: "Signals",   value: scan.signals_found ?? 0,    color: "#4ade80" },
        { label: "BTC Regime",value: scan.btc_regime ?? "—",     color: scan.btc_regime === "BULL" ? "#4ade80" : scan.btc_regime === "BEAR" ? "#f87171" : "#facc15" },
        { label: "FNG",       value: `${scan.fng_value} (${scan.fng_label ?? "—"})`, color: "#94a3b8" },
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
  );
}

export function ScanStatus({ scan, loading }: ScanStatusProps) {
  const [filter, setFilter] = useState<Filter>("ALL");

  const coins: RejectedCoin[] = scan?.rejected_coins ?? [];
  const filtered = filter === "ALL" ? coins : coins.filter(c => c.direction === filter);

  const FILTERS: Filter[] = ["ALL", "LONG", "SHORT"];
  const filterColor: Record<Filter, { active: string; border: string; color: string }> = {
    ALL:   { active: "rgba(59,130,246,0.2)",  border: "#3b82f6", color: "#93c5fd" },
    LONG:  { active: "rgba(34,197,94,0.2)",   border: "#22c55e", color: "#4ade80" },
    SHORT: { active: "rgba(239,68,68,0.2)",   border: "#ef4444", color: "#f87171" },
  };

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
          {scan?.scan_time && (
            <span className="text-xs" style={{ color: "#475569" }}>
              · {relTime(scan.scan_time)}
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {FILTERS.map(f => {
            const s = filterColor[f];
            const isActive = filter === f;
            return (
              <button key={f} onClick={() => setFilter(f)}
                className="px-2.5 py-1 rounded text-xs font-semibold transition-all"
                style={{
                  background: isActive ? s.active : "rgba(30,58,95,0.5)",
                  color: isActive ? s.color : "#64748b",
                  border: `1px solid ${isActive ? s.border : "#1e3a5f"}`,
                }}>
                {f}
              </button>
            );
          })}
        </div>
      </div>

      {/* Summary stats */}
      {scan && <ScanSummary scan={scan} />}

      {/* Candidates table */}
      <div className="overflow-x-auto" style={{ maxHeight: 260, overflowY: "auto" }}>
        <table className="w-full text-xs">
          <thead className="sticky top-0" style={{ background: "#0d1f3c" }}>
            <tr style={{ borderBottom: "1px solid #1e3a5f" }}>
              {["COIN", "DIR", "SCORE", "TIMEFRAMES (4H/1H)", "REASON"].map(h => (
                <th key={h} className="px-3 py-2 text-left font-semibold uppercase tracking-wide"
                  style={{ color: "#64748b" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && coins.length === 0 ? (
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
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-xs"
                  style={{ color: "#475569" }}>
                  {coins.length === 0 ? "No scan data yet" : `No ${filter} candidates`}
                </td>
              </tr>
            ) : (
              filtered.map((c, i) => {
                const sc = scoreColor(c.best_score);
                const isLong = c.direction === "LONG";
                const h4 = c.scores?.["4H"];
                const h1 = c.scores?.["1H"];
                const frames = [
                  h4 != null ? `${h4}` : "—",
                  h1 != null ? `${h1}` : "—",
                ].join(" / ");
                return (
                  <tr key={i}
                    style={{ borderBottom: "1px solid #0d1f3c", transition: "background 0.1s" }}
                    onMouseEnter={e => (e.currentTarget.style.background = "#0d1f3c")}
                    onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                    <td className="px-3 py-2.5 font-mono font-semibold"
                      style={{ color: "#cbd5e1" }}>
                      {c.symbol}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="text-xs font-bold px-1.5 py-0.5 rounded"
                        style={{
                          color: isLong ? "#4ade80" : "#f87171",
                          background: isLong ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
                        }}>
                        {isLong ? "▲ L" : "▼ S"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 font-mono font-bold"
                      style={{ color: sc.color }}>
                      {c.best_score}
                      <span className="text-xs font-normal ml-1" style={{ color: sc.color, opacity: 0.7 }}>
                        {sc.label}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 font-mono"
                      style={{ color: "#64748b" }}>
                      {frames}
                    </td>
                    <td className="px-3 py-2.5 text-xs"
                      style={{ color: "#475569", maxWidth: 260 }}>
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
