import { useState } from "react";
import { ScanResult } from "@/hooks/useBotData";

interface ScanStatusProps {
  results: ScanResult[];
  updatedAt: string | null;
  loading: boolean;
}

type Filter = "ALL" | "BUY" | "SELL" | "HOLD";

const FILTER_STYLES: Record<Filter, { active: string; border: string; color: string }> = {
  ALL:  { active: "rgba(59,130,246,0.2)",  border: "#3b82f6", color: "#93c5fd" },
  BUY:  { active: "rgba(34,197,94,0.2)",   border: "#22c55e", color: "#4ade80" },
  SELL: { active: "rgba(239,68,68,0.2)",   border: "#ef4444", color: "#f87171" },
  HOLD: { active: "rgba(250,204,21,0.15)", border: "#facc15", color: "#facc15" },
};

function signalColor(sig: string) {
  if (sig === "BUY")  return { color: "#4ade80", bg: "rgba(34,197,94,0.12)" };
  if (sig === "SELL") return { color: "#f87171", bg: "rgba(239,68,68,0.12)" };
  return { color: "#facc15", bg: "rgba(250,204,21,0.12)" };
}

function confColor(conf?: string) {
  if (!conf) return "#94a3b8";
  const c = conf.toLowerCase();
  if (c === "high")   return "#4ade80";
  if (c === "medium") return "#facc15";
  if (c === "low")    return "#f87171";
  return "#94a3b8";
}

function relTime(raw?: string | null): string {
  if (!raw) return "—";
  try {
    const ms = Date.now() - new Date(raw).getTime();
    if (ms < 0) return "just now";
    const s = Math.floor(ms / 1000);
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    return `${Math.floor(m / 60)}h ago`;
  } catch {
    return raw;
  }
}

export function ScanStatus({ results, updatedAt, loading }: ScanStatusProps) {
  const [filter, setFilter] = useState<Filter>("ALL");

  const filtered = filter === "ALL"
    ? results
    : results.filter(r => r.signal === filter);

  return (
    <div className="rounded-xl overflow-hidden"
      style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
      <div className="px-4 py-3 flex items-center justify-between"
        style={{ borderBottom: "1px solid #1e3a5f" }}>
        <div className="flex items-center gap-2">
          <h2 className="text-xs font-bold uppercase tracking-widest" style={{ color: "#94a3b8" }}>
            Scan Status
          </h2>
          {updatedAt && (
            <span className="text-xs" style={{ color: "#475569" }}>
              · {relTime(updatedAt)}
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {(["ALL", "BUY", "SELL", "HOLD"] as Filter[]).map(f => {
            const s = FILTER_STYLES[f];
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

      <div className="overflow-x-auto" style={{ maxHeight: 280, overflowY: "auto" }}>
        <table className="w-full text-xs">
          <thead className="sticky top-0" style={{ background: "#0d1f3c" }}>
            <tr style={{ borderBottom: "1px solid #1e3a5f" }}>
              {["COIN", "SIGNAL", "CONFIDENCE", "LAST SCANNED"].map(h => (
                <th key={h} className="px-4 py-2 text-left font-semibold uppercase tracking-wide"
                  style={{ color: "#64748b" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && results.length === 0 ? (
              [...Array(5)].map((_, i) => (
                <tr key={i} style={{ borderBottom: "1px solid #0d1f3c" }}>
                  {[...Array(4)].map((_, j) => (
                    <td key={j} className="px-4 py-2.5">
                      <div className="h-3 rounded animate-pulse"
                        style={{ background: "#0d1f3c", width: j === 0 ? "80%" : "50%" }} />
                    </td>
                  ))}
                </tr>
              ))
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-xs"
                  style={{ color: "#475569" }}>
                  No results{filter !== "ALL" ? ` for ${filter}` : ""}
                </td>
              </tr>
            ) : (
              filtered.map((c, i) => {
                const sc = signalColor(c.signal);
                const cc = confColor(c.confidence);
                return (
                  <tr key={i}
                    style={{ borderBottom: "1px solid #0d1f3c", transition: "background 0.1s" }}
                    onMouseEnter={e => (e.currentTarget.style.background = "#0d1f3c")}
                    onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                    <td className="px-4 py-2.5 font-mono font-semibold"
                      style={{ color: "#cbd5e1" }}>
                      {c.symbol}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="px-2 py-0.5 rounded text-xs font-bold"
                        style={{ color: sc.color, background: sc.bg }}>
                        {c.signal}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 font-medium" style={{ color: cc }}>
                      {c.confidence ?? "—"}
                    </td>
                    <td className="px-4 py-2.5" style={{ color: "#64748b" }}>
                      {relTime(c.last_checked)}
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
