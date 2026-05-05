import { useState, useEffect } from "react";
import { syncTelegram } from "@/hooks/useBotData";

interface HeaderProps {
  onToggleSidebar: () => void;
  isOnline: boolean;
  btcPrice?: number;
}

export function Header({ onToggleSidebar, isOnline, btcPrice }: HeaderProps) {
  const [clock, setClock] = useState(new Date());
  const [syncState, setSyncState] = useState<"idle" | "syncing" | "done" | "error">("idle");

  useEffect(() => {
    const id = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const handleSync = async () => {
    setSyncState("syncing");
    const ok = await syncTelegram();
    setSyncState(ok ? "done" : "error");
    setTimeout(() => setSyncState("idle"), 3000);
  };

  const syncLabel =
    syncState === "syncing" ? "Syncing..." :
    syncState === "done"    ? "Bot Synced!" :
    syncState === "error"   ? "Error" :
    "SYNC WITH TELEGRAM";

  const syncIcon =
    syncState === "syncing" ? "⏳" :
    syncState === "done"    ? "✅" :
    syncState === "error"   ? "❌" : "✈️";

  const syncColor =
    syncState === "done"  ? { bg: "rgba(34,197,94,0.15)",  border: "#22c55e", text: "#4ade80" } :
    syncState === "error" ? { bg: "rgba(239,68,68,0.15)",  border: "#ef4444", text: "#f87171" } :
                            { bg: "rgba(59,130,246,0.15)", border: "#3b82f6", text: "#93c5fd" };

  return (
    <header className="flex-shrink-0 flex items-center justify-between px-5 py-3"
      style={{ background: "#0a1628", borderBottom: "1px solid #1e3a5f" }}>
      <div className="flex items-center gap-3">
        <button onClick={onToggleSidebar}
          className="text-lg leading-none transition-colors"
          style={{ color: "#64748b" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#cbd5e1")}
          onMouseLeave={e => (e.currentTarget.style.color = "#64748b")}>
          ☰
        </button>
        <h1 className="font-bold text-sm tracking-wide" style={{ color: "#e2e8f0" }}>
          CRYPTO TRADING BOT{" "}
          <span style={{ color: "#3b82f6" }}>|</span>{" "}
          <span className="text-xs font-bold" style={{ color: "#60a5fa" }}>MASTER CONTROL</span>
        </h1>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={handleSync}
          disabled={syncState === "syncing"}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all"
          style={{
            background: syncColor.bg,
            border: `1px solid ${syncColor.border}`,
            color: syncColor.text,
            cursor: syncState === "syncing" ? "not-allowed" : "pointer",
            opacity: syncState === "syncing" ? 0.7 : 1,
          }}>
          <span>{syncIcon}</span>
          <span>{syncLabel}</span>
        </button>

        <div className="flex items-center gap-2 text-xs" style={{ color: "#94a3b8" }}>
          <span className="px-2 py-1 rounded"
            style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
            API: Bitget
          </span>
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full"
              style={{
                background: isOnline ? "#4ade80" : "#f87171",
                boxShadow: isOnline ? "0 0 6px #4ade80" : "0 0 6px #f87171",
                animation: isOnline ? "pulse 2s infinite" : "none",
              }} />
            <span style={{ color: isOnline ? "#4ade80" : "#f87171" }}>
              {isOnline ? "Online" : "Offline"}
            </span>
          </span>
          {btcPrice && btcPrice > 0 && (
            <span className="font-mono" style={{ color: "#64748b" }}>
              BTC ${btcPrice.toLocaleString()}
            </span>
          )}
        </div>

        <span className="text-xs font-mono" style={{ color: "#475569" }}>
          {clock.toLocaleTimeString()}
        </span>

        <button className="transition-colors text-lg"
          style={{ color: "#475569" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#94a3b8")}
          onMouseLeave={e => (e.currentTarget.style.color = "#475569")}>
          🔔
        </button>
      </div>
    </header>
  );
}
