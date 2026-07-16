import { useState, useEffect } from "react";
import { syncTelegram } from "@/hooks/useBotData";
import { usePushNotifications } from "@/hooks/usePushNotifications";

interface HeaderProps {
  onToggleSidebar: () => void;
  isOnline: boolean;
  btcPrice?: number;
  isMobile?: boolean;
}

export function Header({ onToggleSidebar, isOnline, btcPrice, isMobile }: HeaderProps) {
  const [clock, setClock] = useState(new Date());
  const [syncState, setSyncState] = useState<"idle" | "syncing" | "done" | "error">("idle");
  const { state: pushState, subscribe, unsubscribe } = usePushNotifications();

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

  const handlePush = async () => {
    if (pushState === "subscribed") {
      await unsubscribe();
    } else if (pushState === "unsubscribed") {
      await subscribe();
    }
  };

  const syncIcon =
    syncState === "syncing" ? "⏳" :
    syncState === "done"    ? "✅" :
    syncState === "error"   ? "❌" : "✈️";

  const syncLabel =
    syncState === "syncing" ? "Syncing..." :
    syncState === "done"    ? "Synced!" :
    syncState === "error"   ? "Error" :
    isMobile ? "SYNC" : "SYNC WITH TELEGRAM";

  const syncColor =
    syncState === "done"  ? { bg: "rgba(34,197,94,0.15)",  border: "#22c55e", text: "#4ade80" } :
    syncState === "error" ? { bg: "rgba(239,68,68,0.15)",  border: "#ef4444", text: "#f87171" } :
                            { bg: "rgba(59,130,246,0.15)", border: "#3b82f6", text: "#93c5fd" };

  const pushIcon =
    pushState === "subscribed"   ? "🔔" :
    pushState === "denied"       ? "🔕" :
    pushState === "unsupported"  ? "🔕" : "🔔";

  const pushTitle =
    pushState === "subscribed"  ? "התראות פעילות — לחץ לכיבוי" :
    pushState === "denied"      ? "התראות חסומות בדפדפן" :
    pushState === "unsupported" ? "הדפדפן לא תומך בהתראות" : "הפעל התראות לטלפון";

  const pushColor =
    pushState === "subscribed"  ? { bg: "rgba(34,197,94,0.12)",  border: "#22c55e66", text: "#4ade80" } :
    pushState === "denied" || pushState === "unsupported"
                                ? { bg: "rgba(100,116,139,0.1)", border: "#47536166", text: "#64748b" } :
                                  { bg: "rgba(251,191,36,0.1)",  border: "#f59e0b66", text: "#fbbf24" };

  const showPushBtn = pushState !== "loading" && pushState !== "unsupported";

  const compactTime = `${String(clock.getHours()).padStart(2, "0")}:${String(clock.getMinutes()).padStart(2, "0")}`;

  return (
    <header
      className="flex-shrink-0 flex items-center justify-between px-3 py-2"
      style={{ background: "#0a1628", borderBottom: "1px solid #1e3a5f", minHeight: 48 }}>

      {/* Left: hamburger + title */}
      <div className="flex items-center gap-2 min-w-0">
        <button
          onClick={onToggleSidebar}
          className="flex-shrink-0 text-lg leading-none transition-colors p-1"
          style={{ color: "#64748b" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#cbd5e1")}
          onMouseLeave={e => (e.currentTarget.style.color = "#64748b")}>
          ☰
        </button>
        {!isMobile && (
          <h1 className="font-bold text-sm tracking-wide whitespace-nowrap" style={{ color: "#e2e8f0" }}>
            CRYPTO TRADING BOT{" "}
            <span style={{ color: "#3b82f6" }}>|</span>{" "}
            <span className="text-xs font-bold" style={{ color: "#60a5fa" }}>MASTER CONTROL</span>
          </h1>
        )}
        {isMobile && (
          <span className="font-bold text-xs" style={{ color: "#60a5fa" }}>MASTER CONTROL</span>
        )}
      </div>

      {/* Right: controls */}
      <div className="flex items-center gap-2 flex-shrink-0">

        {/* Push notification toggle */}
        {showPushBtn && (
          <button
            onClick={handlePush}
            disabled={pushState === "denied"}
            title={pushTitle}
            className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs font-semibold transition-all"
            style={{
              background: pushColor.bg,
              border: `1px solid ${pushColor.border}`,
              color: pushColor.text,
              cursor: pushState === "denied" ? "not-allowed" : "pointer",
            }}>
            <span>{pushIcon}</span>
            {!isMobile && (
              <span>{pushState === "subscribed" ? "התראות ON" : "התראות"}</span>
            )}
          </button>
        )}

        <button
          onClick={handleSync}
          disabled={syncState === "syncing"}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-all"
          style={{
            background: syncColor.bg,
            border: `1px solid ${syncColor.border}`,
            color: syncColor.text,
            cursor: syncState === "syncing" ? "not-allowed" : "pointer",
          }}>
          <span>{syncIcon}</span>
          <span>{syncLabel}</span>
        </button>

        {!isMobile && (
          <div className="flex items-center gap-2 text-xs" style={{ color: "#94a3b8" }}>
            <span className="px-2 py-1 rounded"
              style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
              API: Bitget
            </span>
            {btcPrice && btcPrice > 0 && (
              <span className="font-mono" style={{ color: "#64748b" }}>
                BTC ${btcPrice.toLocaleString()}
              </span>
            )}
          </div>
        )}

        <span className="flex items-center gap-1 text-xs">
          <span className="w-1.5 h-1.5 rounded-full"
            style={{
              background: isOnline ? "#4ade80" : "#f87171",
              boxShadow: isOnline ? "0 0 6px #4ade80" : "none",
            }} />
          {!isMobile && (
            <span style={{ color: isOnline ? "#4ade80" : "#f87171" }}>
              {isOnline ? "Online" : "Offline"}
            </span>
          )}
        </span>

        {/* Clock — compact on mobile, full on desktop */}
        <span className="text-xs font-mono" style={{ color: "#475569" }}>
          {isMobile ? compactTime : clock.toLocaleTimeString()}
        </span>
      </div>
    </header>
  );
}
