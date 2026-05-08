import { useState, useEffect } from "react";
import { Sidebar } from "@/components/Sidebar";
import { Header } from "@/components/Header";
import { FinancialOverview } from "@/components/FinancialOverview";
import { FngGauge } from "@/components/FngGauge";
import { ActiveTradesTable } from "@/components/ActiveTradesTable";
import { ScanStatus } from "@/components/ScanStatus";
import { BotSettings } from "@/components/BotSettings";
import { TradeHistoryModal } from "@/components/TradeHistoryModal";
import { LogsView } from "@/components/LogsView";
import { StaleBadge } from "@/components/StaleBadge";
import {
  useStatus, useTrades, useScan, useSlots,
  useWallet, useFngSettings, useAudit, useBotLog,
} from "@/hooks/useBotData";

function useIsMobile() {
  const [mobile, setMobile] = useState(() => window.innerWidth < 768);
  useEffect(() => {
    const fn = () => setMobile(window.innerWidth < 768);
    window.addEventListener("resize", fn);
    return () => window.removeEventListener("resize", fn);
  }, []);
  return mobile;
}

export default function App() {
  const isMobile = useIsMobile();
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 768);
  const [activeNav, setActiveNav] = useState("Dashboard");
  const [showHistory, setShowHistory] = useState(false);

  // Auto-close sidebar when viewport drops below 768px
  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
  }, [isMobile]);

  const { data: status, loading: statusLoading, stale: statusStale, lastSuccessAt: statusLastOk } = useStatus();
  const { data: trades, stale: tradesStale, lastSuccessAt: tradesLastOk } = useTrades();
  const { data: scan,   stale: scanStale,   lastSuccessAt: scanLastOk   } = useScan();
  const { data: slots, refetch: refetchSlots } = useSlots();
  const { data: wallet, stale: walletStale, lastSuccessAt: walletLastOk } = useWallet();
  const { data: fngSettings } = useFngSettings();
  const { data: audit, loading: auditLoading, stale: auditStale, lastSuccessAt: auditLastOk } = useAudit(showHistory);
  const { data: logs,  loading: logsLoading  } = useBotLog(activeNav === "Logs");

  const isOnline = status != null;
  const equityHistory = wallet?.equity_history?.map(p => p.eq) ?? [];

  const equity   = status?.equity   ?? wallet?.equity   ?? wallet?.balance ?? null;
  const starting = status?.starting ?? wallet?.starting                     ?? null;
  const realized = status?.realized ?? wallet?.total_pnl                    ?? 0;

  // Flask /api/status always returns unrealized: 0 — compute from trades instead.
  // Uses the same formula as ActiveTradesTable so the two panels always agree.
  const activeTrades = trades?.trades ?? [];
  const unrealized = activeTrades.length > 0
    ? activeTrades.reduce((sum, t) => {
        const cp      = t.current_price ?? t.entry;
        const rawPct  = t.entry > 0 ? (cp - t.entry) / t.entry * 100 : 0;
        const pnlPct  = t.direction === "LONG" ? rawPct : -rawPct;
        const posSize = t.pos_size ?? 500;
        const pnlUsd  = t.tp1_triggered
          ? (t.tp1_pnl ?? 0) + posSize * pnlPct / 100
          : posSize * pnlPct / 100;
        return sum + pnlUsd;
      }, 0)
    : (status?.unrealized ?? wallet?.unrealized_pnl ?? 0);

  const fngValue = status?.fng_value ?? null;
  const fngLabel = status?.fng_label ?? null;

  const maxTrades = slots?.max_trades ?? 3;

  const auditTrades = audit?.trades ?? [];

  const handleNav = (tab: string) => {
    setActiveNav(tab);
    if (isMobile) setSidebarOpen(false);
  };

  return (
    <div
      className="flex h-screen overflow-hidden"
      style={{ fontFamily: "'Inter', system-ui, sans-serif", background: "#070d1a", color: "#e2e8f0" }}>

      {/* ── Mobile backdrop ── */}
      {sidebarOpen && isMobile && (
        <div
          className="fixed inset-0 z-30"
          style={{ background: "rgba(7,13,26,0.75)", backdropFilter: "blur(2px)" }}
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ── Sidebar ── */}
      {sidebarOpen && (
        <Sidebar activeNav={activeNav} onNav={handleNav} overlay={isMobile} />
      )}

      {/* ── Main ── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Header
          onToggleSidebar={() => setSidebarOpen(o => !o)}
          isOnline={isOnline}
          btcPrice={status?.btc_price}
          isMobile={isMobile}
        />

        {/* ── Dashboard ── */}
        {activeNav === "Dashboard" && (
          <div className="flex-1 overflow-y-auto p-3 mobile-content-pad" style={{ background: "#070d1a" }}>

            {/* Responsive grid: 1-col on mobile, 2-col on desktop */}
            <div className="dashboard-grid">

              {/* Left column — main content */}
              <div className="space-y-3 min-w-0">
                <FinancialOverview
                  starting={starting}
                  equity={equity}
                  realized={realized}
                  unrealized={unrealized}
                  equityHistory={equityHistory}
                  loading={statusLoading}
                  onShowHistory={() => setShowHistory(true)}
                  isMobile={isMobile}
                  stale={statusStale || walletStale}
                  lastSuccessAt={
                    statusStale && walletStale
                      ? Math.min(statusLastOk ?? Infinity, walletLastOk ?? Infinity) === Infinity ? null
                        : Math.min(statusLastOk!, walletLastOk!)
                      : statusStale ? statusLastOk
                      : walletStale ? walletLastOk
                      : statusLastOk ?? walletLastOk
                  }
                />

                {/* On mobile: FNG + Quick Stats in a compact 2-up row */}
                {isMobile && (
                  <div className="grid grid-cols-2 gap-3">
                    <div className="rounded-xl p-3"
                      style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                      <div className="flex items-center gap-1.5 mb-1">
                        <div className="text-xs font-bold uppercase tracking-widest"
                          style={{ color: "#94a3b8" }}>F&G Index</div>
                        {statusStale && <StaleBadge lastSuccessAt={statusLastOk} />}
                      </div>
                      <FngGauge value={fngValue} label={fngLabel} compact />
                    </div>
                    <div className="rounded-xl p-3 space-y-2"
                      style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                      <div className="flex items-center gap-1.5">
                        <div className="text-xs font-bold uppercase tracking-widest"
                          style={{ color: "#94a3b8" }}>Stats</div>
                        {statusStale && <StaleBadge lastSuccessAt={statusLastOk} />}
                      </div>
                      {[
                        { label: "Trades",    value: `${activeTrades.length} / ${maxTrades}`, color: "#93c5fd" },
                        { label: "Mode",      value: status?.mode ?? "VIRTUAL",                color: "#facc15" },
                        { label: "Available", value: status ? `$${status.available.toFixed(0)}` : "—", color: "#4ade80" },
                      ].map(({ label, value, color }) => (
                        <div key={label} className="flex justify-between items-center">
                          <span style={{ fontSize: 10, color: "#64748b" }}>{label}</span>
                          <span style={{ fontSize: 10, fontFamily: "monospace", fontWeight: 600, color }}>{value}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <ActiveTradesTable
                  trades={activeTrades}
                  maxTrades={maxTrades}
                  loading={statusLoading && activeTrades.length === 0}
                  stale={tradesStale}
                  lastSuccessAt={tradesLastOk}
                />

                {/* Bot settings on mobile: show inline after trades */}
                {isMobile && (
                  <BotSettings
                    slots={slots}
                    fngSettings={fngSettings}
                    onSaved={refetchSlots}
                  />
                )}

                <ScanStatus
                  scan={scan}
                  loading={scan == null}
                  stale={scanStale}
                  lastSuccessAt={scanLastOk}
                />
              </div>

              {/* Right column — desktop only */}
              {!isMobile && (
                <div className="space-y-3">
                  <div className="rounded-xl p-4"
                    style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                    <div className="flex items-center gap-2 mb-2">
                      <h2 className="text-xs font-bold uppercase tracking-widest"
                        style={{ color: "#94a3b8" }}>Fear & Greed Index</h2>
                      {statusStale && <StaleBadge lastSuccessAt={statusLastOk} />}
                    </div>
                    <FngGauge value={fngValue} label={fngLabel} />
                    {fngValue != null && (
                      <div className="text-center mt-1">
                        <span className="text-sm font-bold"
                          style={{
                            color: fngValue < 25 ? "#ef4444" : fngValue < 45 ? "#f97316"
                              : fngValue < 55 ? "#facc15" : fngValue < 75 ? "#84cc16" : "#22c55e",
                          }}>
                          {fngValue} – {fngLabel ?? ""}
                        </span>
                      </div>
                    )}
                  </div>

                  <BotSettings
                    slots={slots}
                    fngSettings={fngSettings}
                    onSaved={refetchSlots}
                  />

                  <div className="rounded-xl p-4 space-y-3"
                    style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                    <div className="flex items-center gap-2">
                      <h2 className="text-xs font-bold uppercase tracking-widest"
                        style={{ color: "#94a3b8" }}>Quick Stats</h2>
                      {statusStale && <StaleBadge lastSuccessAt={statusLastOk} />}
                    </div>
                    {[
                      { label: "Active Trades",  value: `${activeTrades.length} / ${maxTrades}`, color: "#93c5fd" },
                      { label: "Exchange",       value: status?.exchange ?? "Bitget",             color: "#e2e8f0" },
                      { label: "Mode",           value: status?.mode ?? "VIRTUAL",                color: "#facc15" },
                      { label: "Available",      value: status ? `$${status.available.toFixed(2)}` : "—", color: "#4ade80" },
                    ].map(({ label, value, color }) => (
                      <div key={label} className="flex justify-between items-center">
                        <span className="text-xs" style={{ color: "#64748b" }}>{label}</span>
                        <span className="text-xs font-mono font-semibold" style={{ color }}>{value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Logs ── */}
        {activeNav === "Logs" && (
          <div className="flex-1 overflow-y-auto p-3 mobile-content-pad" style={{ background: "#070d1a" }}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-bold text-sm" style={{ color: "#e2e8f0" }}>Bot Logs</h2>
              <span className="text-xs" style={{ color: "#475569" }}>auto-refresh 5s</span>
            </div>
            <LogsView logData={logs} loading={logsLoading} />
          </div>
        )}

        {/* ── Backtest ── */}
        {activeNav === "Backtest" && (
          <div className="flex-1 flex items-center justify-center" style={{ background: "#070d1a" }}>
            <div className="text-center">
              <div className="text-4xl mb-4">📈</div>
              <h2 className="text-lg font-bold mb-2" style={{ color: "#e2e8f0" }}>Backtest Engine</h2>
              <p className="text-sm" style={{ color: "#64748b" }}>Coming soon</p>
            </div>
          </div>
        )}

        {/* ── Settings ── */}
        {activeNav === "Settings" && (
          <div className="flex-1 overflow-y-auto p-3 mobile-content-pad" style={{ background: "#070d1a" }}>
            <h2 className="font-bold text-sm mb-3" style={{ color: "#e2e8f0" }}>Settings</h2>
            <div style={{ maxWidth: 400 }}>
              <BotSettings
                slots={slots}
                fngSettings={fngSettings}
                onSaved={refetchSlots}
              />
            </div>
          </div>
        )}
      </div>

      {/* ── Mobile bottom nav ── */}
      {isMobile && (
        <nav
          className="fixed bottom-0 left-0 right-0 flex z-20"
          style={{
            background: "#0a1628",
            borderTop: "1px solid #1e3a5f",
            paddingBottom: "env(safe-area-inset-bottom)",
          }}>
          {[
            { icon: "⊞", label: "Dashboard" },
            { icon: "📋", label: "Logs" },
            { icon: "⚙️", label: "Settings" },
          ].map(({ icon, label }) => (
            <button key={label} onClick={() => handleNav(label)}
              className="flex-1 flex flex-col items-center py-2 gap-0.5 text-xs transition-all"
              style={{
                color: activeNav === label ? "#60a5fa" : "#475569",
                background: activeNav === label ? "rgba(59,130,246,0.08)" : "transparent",
              }}>
              <span style={{ fontSize: 18 }}>{icon}</span>
              <span style={{ fontSize: 10 }}>{label}</span>
            </button>
          ))}
        </nav>
      )}

      {/* ── Trade History Modal ── */}
      {showHistory && (
        <TradeHistoryModal
          trades={auditTrades}
          loading={auditLoading}
          onClose={() => setShowHistory(false)}
          stale={auditStale}
          lastSuccessAt={auditLastOk}
        />
      )}
    </div>
  );
}
