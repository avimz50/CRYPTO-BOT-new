import { useRef, useEffect } from "react";
import { BotLogData } from "@/hooks/useBotData";

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
