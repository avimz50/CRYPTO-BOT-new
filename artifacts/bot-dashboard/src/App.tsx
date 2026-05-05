import { useState } from "react";
import { Sidebar } from "@/components/Sidebar";
import { Header } from "@/components/Header";
import { FinancialOverview } from "@/components/FinancialOverview";
import { FngGauge } from "@/components/FngGauge";
import { ActiveTradesTable } from "@/components/ActiveTradesTable";
import { ScanStatus } from "@/components/ScanStatus";
import { BotSettings } from "@/components/BotSettings";
import { TradeHistoryModal } from "@/components/TradeHistoryModal";
import { LogsView } from "@/components/LogsView";
import {
  useStatus, useTrades, useScan, useSlots,
  useWallet, useFngSettings, useAudit, useBotLog,
} from "@/hooks/useBotData";

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [activeNav, setActiveNav] = useState("Dashboard");
  const [showHistory, setShowHistory] = useState(false);

  const { data: status, loading: statusLoading } = useStatus();
  const { data: trades }  = useTrades();
  const { data: scan }    = useScan();
  const { data: slots, refetch: refetchSlots } = useSlots();
  const { data: wallet }  = useWallet();
  const { data: fngSettings } = useFngSettings();
  const { data: audit, loading: auditLoading } = useAudit(showHistory);
  const { data: logs,  loading: logsLoading  } = useBotLog(activeNav === "Logs");

  const isOnline = status != null;
  const equityHistory = wallet?.equity_history?.map(p => p.eq) ?? [];

  const equity   = status?.equity   ?? wallet?.equity   ?? wallet?.balance  ?? 200;
  const starting = status?.starting ?? wallet?.starting ?? 200;
  const realized  = status?.realized  ?? wallet?.total_pnl ?? 0;
  const unrealized = status?.unrealized ?? wallet?.unrealized_pnl ?? 0;

  const fngValue = status?.fng_value ?? null;
  const fngLabel = status?.fng_label ?? null;

  const activeTrades = trades?.trades ?? [];
  const maxTrades    = slots?.max_trades ?? 3;

  const scanResults   = (scan as any)?.results ?? (scan as any)?.last_scan ?? [];
  const scanUpdatedAt = scan?.updated ?? null;

  const auditTrades = (audit as any)?.trades ?? [];

  return (
    <div
      className="flex h-screen overflow-hidden"
      style={{ fontFamily: "'Inter', system-ui, sans-serif", background: "#070d1a", color: "#e2e8f0" }}>

      {/* ── Sidebar ── */}
      {sidebarOpen && (
        <Sidebar activeNav={activeNav} onNav={setActiveNav} />
      )}

      {/* ── Main content ── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Header
          onToggleSidebar={() => setSidebarOpen(o => !o)}
          isOnline={isOnline}
          btcPrice={status?.btc_price}
        />

        {/* ── Dashboard tab ── */}
        {activeNav === "Dashboard" && (
          <div className="flex-1 overflow-y-auto p-4 space-y-0"
            style={{ background: "#070d1a" }}>
            <div
              className="grid gap-4"
              style={{ gridTemplateColumns: "1fr 230px" }}>

              {/* Left column */}
              <div className="space-y-4 min-w-0">
                <FinancialOverview
                  starting={starting}
                  equity={equity}
                  realized={realized}
                  unrealized={unrealized}
                  equityHistory={equityHistory}
                  loading={statusLoading}
                  onShowHistory={() => setShowHistory(true)}
                />

                <ActiveTradesTable
                  trades={activeTrades}
                  maxTrades={maxTrades}
                  loading={statusLoading && activeTrades.length === 0}
                />

                <ScanStatus
                  results={scanResults}
                  updatedAt={scanUpdatedAt}
                  loading={scanResults.length === 0}
                />
              </div>

              {/* Right column */}
              <div className="space-y-4">
                {/* Fear & Greed */}
                <div className="rounded-xl p-4"
                  style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                  <h2 className="text-xs font-bold uppercase tracking-widest mb-2"
                    style={{ color: "#94a3b8" }}>
                    Fear & Greed Index
                  </h2>
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

                {/* Bot Settings */}
                <BotSettings
                  slots={slots}
                  fngSettings={fngSettings}
                  onSaved={refetchSlots}
                />

                {/* Quick stats */}
                <div className="rounded-xl p-4 space-y-3"
                  style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                  <h2 className="text-xs font-bold uppercase tracking-widest"
                    style={{ color: "#94a3b8" }}>
                    Quick Stats
                  </h2>
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
            </div>
          </div>
        )}

        {/* ── Logs tab ── */}
        {activeNav === "Logs" && (
          <div className="flex-1 overflow-y-auto p-4" style={{ background: "#070d1a" }}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-bold text-sm" style={{ color: "#e2e8f0" }}>Bot Logs</h2>
              <span className="text-xs" style={{ color: "#475569" }}>
                Auto-refreshes every 5 s
              </span>
            </div>
            <LogsView logData={logs} loading={logsLoading} />
          </div>
        )}

        {/* ── Backtest tab ── */}
        {activeNav === "Backtest" && (
          <div className="flex-1 flex items-center justify-center"
            style={{ background: "#070d1a" }}>
            <div className="text-center">
              <div className="text-4xl mb-4">📈</div>
              <h2 className="text-lg font-bold mb-2" style={{ color: "#e2e8f0" }}>
                Backtest Engine
              </h2>
              <p className="text-sm" style={{ color: "#64748b" }}>Coming soon</p>
            </div>
          </div>
        )}

        {/* ── Settings tab ── */}
        {activeNav === "Settings" && (
          <div className="flex-1 overflow-y-auto p-4" style={{ background: "#070d1a" }}>
            <h2 className="font-bold text-sm mb-4" style={{ color: "#e2e8f0" }}>Settings</h2>
            <div className="max-w-sm">
              <BotSettings
                slots={slots}
                fngSettings={fngSettings}
                onSaved={refetchSlots}
              />
            </div>
          </div>
        )}
      </div>

      {/* ── Trade History Modal ── */}
      {showHistory && (
        <TradeHistoryModal
          trades={auditTrades}
          loading={auditLoading}
          onClose={() => setShowHistory(false)}
        />
      )}
    </div>
  );
}
