import { useRef, useEffect } from "react";
import { BotLogData, RestartEvent } from "@/hooks/useBotData";

interface LogsViewProps {
  logData: BotLogData | null;
  loading: boolean;
}

function LogBlock({ title, content, color }: { title: string; content: string; color: string }) {
  const ref = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [content]);

  const handleCopy = () => {
    navigator.clipboard.writeText(content).catch(() => {});
  };

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex items-center justify-between px-3 py-2"
        style={{ background: "#0d1f3c", borderBottom: "1px solid #1e3a5f" }}>
        <span className="text-xs font-semibold uppercase tracking-wide" style={{ color }}>
          {title}
        </span>
        <button
          onClick={handleCopy}
          className="text-xs px-2 py-0.5 rounded transition-all"
          style={{
            background: "rgba(59,130,246,0.1)",
            border: "1px solid rgba(59,130,246,0.3)",
            color: "#93c5fd",
          }}
          onMouseEnter={e => (e.currentTarget.style.background = "rgba(59,130,246,0.2)")}
          onMouseLeave={e => (e.currentTarget.style.background = "rgba(59,130,246,0.1)")}>
          Copy
        </button>
      </div>
      <pre
        ref={ref}
        className="flex-1 overflow-y-auto text-xs font-mono leading-relaxed p-3"
        style={{
          background: "#050b14",
          color: color,
          maxHeight: 300,
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
        }}>
        {content || "(empty)"}
      </pre>
    </div>
  );
}

function RestartBanner({ info }: { info: BotLogData["restart_info"] }) {
  if (!info || info.restart_count === 0) return null;

  const count = info.restart_count;
  const lastAt = info.last_restart_at ?? "unknown";
  const lastReason = info.last_reason ?? "";
  const history: RestartEvent[] = info.history ?? [];

  return (
    <div className="rounded-xl p-4 space-y-3"
      style={{ background: "rgba(251,191,36,0.08)", border: "1px solid #f59e0b" }}>

      {/* Header row */}
      <div className="flex items-center gap-2">
        <span style={{ color: "#fbbf24", fontSize: 16 }}>⚠</span>
        <span className="font-bold text-sm" style={{ color: "#fbbf24" }}>
          Bot restarted {count} {count === 1 ? "time" : "times"}
        </span>
        <span className="text-xs ml-auto" style={{ color: "#94a3b8" }}>
          Watchdog is active — bot will auto-recover from crashes
        </span>
      </div>

      {/* Last restart detail */}
      <div className="text-xs space-y-1" style={{ color: "#cbd5e1" }}>
        <div>
          <span style={{ color: "#94a3b8" }}>Last restart: </span>
          <span className="font-mono">{lastAt}</span>
        </div>
        {lastReason && (
          <div>
            <span style={{ color: "#94a3b8" }}>Reason: </span>
            <span className="font-mono" style={{ color: "#fca5a5" }}>{lastReason}</span>
          </div>
        )}
      </div>

      {/* History table — last 5 events */}
      {history.length > 1 && (
        <div>
          <div className="text-xs font-semibold mb-1 uppercase tracking-wide" style={{ color: "#64748b" }}>
            Recent restarts
          </div>
          <div className="space-y-1">
            {[...history].reverse().slice(0, 5).map((ev, i) => (
              <div key={i}
                className="flex items-center gap-3 text-xs font-mono px-2 py-1 rounded"
                style={{ background: "rgba(255,255,255,0.03)", color: "#94a3b8" }}>
                <span className="flex-shrink-0" style={{ color: "#64748b" }}>{ev.ts}</span>
                <span className="flex-1 truncate" style={{ color: "#fca5a5" }}>{ev.reason}</span>
                {ev.exit_code !== null && (
                  <span className="flex-shrink-0 px-1.5 py-0.5 rounded"
                    style={{
                      background: ev.exit_code === 0 ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)",
                      color: ev.exit_code === 0 ? "#4ade80" : "#f87171",
                    }}>
                    exit {ev.exit_code}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function LogsView({ logData, loading }: LogsViewProps) {
  if (loading && !logData) {
    return (
      <div className="flex items-center justify-center h-64"
        style={{ color: "#475569" }}>
        <div className="text-center">
          <div className="text-2xl mb-2">⏳</div>
          <div className="text-sm">Loading logs...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Watchdog restart banner — shown whenever the bot has been restarted */}
      <RestartBanner info={logData?.restart_info ?? null} />

      {logData?.crash && (
        <div className="rounded-xl p-4"
          style={{ background: "rgba(239,68,68,0.1)", border: "1px solid #ef4444" }}>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-red-400 font-bold text-sm">💥 CRASH DETECTED</span>
          </div>
          <pre className="text-xs font-mono text-red-300 whitespace-pre-wrap"
            style={{ maxHeight: 120, overflow: "auto" }}>
            {logData.crash}
          </pre>
        </div>
      )}

      <div className="rounded-xl overflow-hidden"
        style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
        <LogBlock
          title="📤 Stdout"
          content={logData?.stdout ?? ""}
          color="#4ade80"
        />
      </div>

      <div className="rounded-xl overflow-hidden"
        style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
        <LogBlock
          title="⚠️ Stderr"
          content={logData?.stderr ?? ""}
          color="#facc15"
        />
      </div>
    </div>
  );
}
