import { useState, useEffect } from "react";

// ── Types ──────────────────────────────────────────────────────
interface Trade {
  symbol: string; direction: "LONG" | "SHORT";
  leverage: number; amount: number;
  entry: number; sl: number; tp: number;
  estProfit: number; pnl: number;
  status: "Running" | "Closing";
}
interface ScanCoin {
  symbol: string; signal: "BUY" | "SELL" | "HOLD";
  confidence: "High" | "Medium" | "Low"; scannedAgo: string;
}
interface HistoryTrade {
  id: string; symbol: string; direction: "LONG" | "SHORT";
  openTime: string; openPrice: number;
  closeTime: string; closePrice: number;
  leverage: number; amount: number;
  pnl: number; pnlPct: number; reason: string;
}

// ── Mock data ──────────────────────────────────────────────────
const TRADES: Trade[] = [
  { symbol: "ETH/USDT", direction: "LONG",  leverage: 10, amount: 50, entry: 2340, sl: 2296, tp: 2574, estProfit: 11.7, pnl:  2.22, status: "Running" },
  { symbol: "SOL/USDT", direction: "LONG",  leverage: 10, amount: 18, entry: 86.4, sl: 83.8, tp: 91.6, estProfit: 4.68, pnl:  0.44, status: "Running" },
  { symbol: "FET/USDT", direction: "LONG",  leverage: 10, amount: 14, entry: 0.216, sl: 0.210, tp: 0.228, estProfit: 3.92, pnl: -0.21, status: "Running" },
];

const SCAN_COINS: ScanCoin[] = [
  { symbol: "BTC/USDT",  signal: "BUY",  confidence: "High",   scannedAgo: "1m ago" },
  { symbol: "SOL/USDT",  signal: "HOLD", confidence: "Medium", scannedAgo: "1m ago" },
  { symbol: "XRP/USDT",  signal: "BUY",  confidence: "Medium", scannedAgo: "2m ago" },
  { symbol: "DOGE/USDT", signal: "HOLD", confidence: "Low",    scannedAgo: "2m ago" },
  { symbol: "ADA/USDT",  signal: "SELL", confidence: "Medium", scannedAgo: "3m ago" },
  { symbol: "LINK/USDT", signal: "BUY",  confidence: "High",   scannedAgo: "3m ago" },
  { symbol: "AVAX/USDT", signal: "HOLD", confidence: "Low",    scannedAgo: "4m ago" },
];

const HISTORY: HistoryTrade[] = [
  { id: "T001", symbol: "BTC/USDT",  direction: "LONG",  openTime: "03/01 09:12", openPrice: 82000, closeTime: "03/01 14:35", closePrice: 85400, leverage: 10, amount: 50, pnl: 20.73, pnlPct: 4.15, reason: "TP" },
  { id: "T002", symbol: "ETH/USDT",  direction: "LONG",  openTime: "03/05 11:00", openPrice: 2100,  closeTime: "03/05 18:20", closePrice: 2050,  leverage: 10, amount: 50, pnl: -5.95, pnlPct: -2.38, reason: "SL" },
  { id: "T003", symbol: "SOL/USDT",  direction: "LONG",  openTime: "03/10 08:45", openPrice: 75,    closeTime: "03/10 22:10", closePrice: 82,    leverage: 10, amount: 30, pnl: 28.0,  pnlPct: 9.33, reason: "TP" },
  { id: "T004", symbol: "XRP/USDT",  direction: "LONG",  openTime: "03/15 13:00", openPrice: 1.42,  closeTime: "03/15 20:30", closePrice: 1.38,  leverage: 10, amount: 25, pnl: -3.52, pnlPct: -2.82, reason: "SL" },
  { id: "T005", symbol: "DOGE/USDT", direction: "LONG",  openTime: "03/20 07:30", openPrice: 0.18,  closeTime: "03/21 09:15", closePrice: 0.195, leverage: 10, amount: 30, pnl: 12.5,  pnlPct: 8.33, reason: "TP" },
  { id: "T006", symbol: "BNB/USDT",  direction: "SHORT", openTime: "03/25 15:20", openPrice: 580,   closeTime: "03/25 19:45", closePrice: 562,   leverage: 10, amount: 40, pnl: 15.52, pnlPct: 3.1,  reason: "TP" },
  { id: "T007", symbol: "LINK/USDT", direction: "LONG",  openTime: "04/01 10:00", openPrice: 14.2,  closeTime: "04/02 08:00", closePrice: 14.2,  leverage: 10, amount: 25, pnl: 0.00,  pnlPct: 0.0,  reason: "BE" },
];

const FNG_VALUE = 55;

// ── Sub-components ─────────────────────────────────────────────
function FngGauge({ value }: { value: number }) {
  const cx = 80, cy = 80, R = 62;
  const col = value < 25 ? "#ef4444" : value < 45 ? "#f97316" : value < 55 ? "#facc15" : value < 75 ? "#84cc16" : "#22c55e";
  const label = value < 25 ? "EXTREME FEAR" : value < 45 ? "FEAR" : value < 55 ? "NEUTRAL" : value < 75 ? "GREED" : "EXTREME GREED";
  const pt = (r: number, v: number) => {
    const θ = (180 - (v / 100) * 180) * Math.PI / 180;
    return { x: cx + r * Math.cos(θ), y: cy - r * Math.sin(θ) };
  };
  const arc = (from: number, to: number) => {
    const p1 = pt(R, from), p2 = pt(R, to);
    return `M ${p1.x.toFixed(1)} ${p1.y.toFixed(1)} A ${R} ${R} 0 0 1 ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  };
  const zones = [
    { from: 0, to: 20, color: "#ef4444" }, { from: 20, to: 40, color: "#f97316" },
    { from: 40, to: 60, color: "#facc15" }, { from: 60, to: 80, color: "#84cc16" },
    { from: 80, to: 100, color: "#22c55e" },
  ];
  const needle = pt(54, value);
  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 160 100" width="160" height="100">
        {zones.map((z, i) => (
          <path key={i} d={arc(z.from, z.to)} fill="none" stroke={z.color} strokeWidth={12} strokeLinecap="butt" opacity={0.25} />
        ))}
        {zones.map((z, i) => {
          const end = Math.min(value, z.to);
          if (end <= z.from) return null;
          return <path key={`f${i}`} d={arc(z.from, end)} fill="none" stroke={z.color} strokeWidth={12} strokeLinecap="butt" />;
        })}
        <line x1={cx} y1={cy} x2={needle.x} y2={needle.y} stroke="#fff" strokeWidth={2} strokeLinecap="round" />
        <circle cx={cx} cy={cy} r={5} fill="#1e3a5f" stroke={col} strokeWidth={2} />
        <text x={cx} y={cy - 16} textAnchor="middle" fill={col} fontSize={20} fontWeight="bold" fontFamily="monospace">{value}</text>
        <text x={cx} y={cy - 5} textAnchor="middle" fill="#94a3b8" fontSize={6.5} fontFamily="sans-serif">{label}</text>
      </svg>
    </div>
  );
}

function Sparkline() {
  const pts = [200, 195, 205, 198, 210, 208, 215, 212, 220, 218, 225, 222, 228];
  const min = Math.min(...pts); const max = Math.max(...pts);
  const W = 140; const H = 40;
  const xs = pts.map((_, i) => (i / (pts.length - 1)) * W);
  const ys = pts.map(v => H - ((v - min) / (max - min)) * H);
  const polyline = xs.map((x, i) => `${x},${ys[i]}`).join(" ");
  const area = `0,${H} ${polyline} ${W},${H}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H}>
      <defs>
        <linearGradient id="spk" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#3b82f6" stopOpacity="0.4" />
          <stop offset="100%" stopColor="#3b82f6" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polygon points={area} fill="url(#spk)" />
      <polyline points={polyline} fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinejoin="round" />
      <circle cx={xs[xs.length - 1]} cy={ys[ys.length - 1]} r="3" fill="#3b82f6" />
    </svg>
  );
}

function Slider({ label, value, min, max, unit, onChange }: { label: string; value: number; min: number; max: number; unit: string; onChange: (v: number) => void }) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="mb-4">
      <div className="flex justify-between mb-1">
        <span className="text-xs text-slate-400">{label}</span>
        <span className="text-xs font-mono text-blue-300">{value} {unit}</span>
      </div>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs text-slate-600">{min}</span>
        <div className="relative flex-1 h-1.5 bg-slate-700 rounded-full">
          <div className="absolute top-0 left-0 h-full bg-blue-500 rounded-full" style={{ width: `${pct}%` }} />
          <input type="range" min={min} max={max} value={value}
            onChange={e => onChange(Number(e.target.value))}
            className="absolute inset-0 w-full opacity-0 cursor-pointer h-full" />
        </div>
        <span className="text-xs text-slate-600">{max}</span>
      </div>
    </div>
  );
}

// ── Main Dashboard ─────────────────────────────────────────────
export function MasterControl() {
  const [scanFilter, setScanFilter] = useState<"ALL" | "BUY" | "SELL" | "HOLD">("ALL");
  const [showHistory, setShowHistory] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [activeNav, setActiveNav] = useState("Dashboard");
  const [syncState, setSyncState] = useState<"idle" | "syncing" | "done">("idle");
  const [maxTrades, setMaxTrades] = useState(3);
  const [amountPerTrade, setAmountPerTrade] = useState(50);
  const [leverage, setLeverage] = useState(10);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [clock, setClock] = useState(new Date());

  useEffect(() => { const id = setInterval(() => setClock(new Date()), 1000); return () => clearInterval(id); }, []);

  const starting = 200;
  const equity   = 178.27;
  const totalPnl = equity - starting;
  const totalPct = (totalPnl / starting) * 100;
  const floating = 2.45;
  const realized = -23.95;

  const handleSync = () => {
    setSyncState("syncing");
    setTimeout(() => { setSyncState("done"); setTimeout(() => setSyncState("idle"), 2500); }, 1500);
  };

  const handleSaveSettings = () => {
    setSettingsSaved(true);
    setTimeout(() => setSettingsSaved(false), 2000);
  };

  const filteredScan = scanFilter === "ALL" ? SCAN_COINS : SCAN_COINS.filter(c => c.signal === scanFilter);

  const navItems = [
    { icon: "⊞", label: "Dashboard" },
    { icon: "📋", label: "Logs" },
    { icon: "📈", label: "Backtest" },
    { icon: "⚙️", label: "Settings" },
  ];

  return (
    <div className="flex h-screen overflow-hidden" style={{ fontFamily: "'Inter', sans-serif", background: "#070d1a", color: "#e2e8f0" }}>

      {/* ── Sidebar ── */}
      {sidebarOpen && (
        <aside className="flex-shrink-0 flex flex-col" style={{ width: 180, background: "#0a1628", borderRight: "1px solid #1e3a5f" }}>
          {/* Logo */}
          <div className="flex items-center gap-2 px-4 py-5 border-b" style={{ borderColor: "#1e3a5f" }}>
            <div className="w-8 h-8 rounded-lg flex items-center justify-center text-base font-bold" style={{ background: "linear-gradient(135deg,#3b82f6,#1d4ed8)" }}>$</div>
            <span className="text-sm font-bold text-blue-300">CryptoBot</span>
          </div>

          {/* Nav */}
          <nav className="flex-1 py-3">
            {navItems.map(({ icon, label }) => (
              <button key={label} onClick={() => setActiveNav(label)}
                className="w-full flex items-center gap-3 px-4 py-2.5 text-left transition-all text-sm"
                style={{
                  background: activeNav === label ? "rgba(59,130,246,0.15)" : "transparent",
                  color: activeNav === label ? "#93c5fd" : "#64748b",
                  borderLeft: activeNav === label ? "2px solid #3b82f6" : "2px solid transparent",
                }}>
                <span className="text-base">{icon}</span>
                <span>{label}</span>
              </button>
            ))}
          </nav>

          {/* Device view */}
          <div className="mx-3 mb-4 p-3 rounded-xl text-center" style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
            <div className="text-xs text-blue-400 mb-1">📱</div>
            <div className="text-xs text-slate-400 font-medium">Device View:</div>
            <div className="text-xs text-blue-300">Auto-Detect</div>
          </div>
        </aside>
      )}

      {/* ── Main ── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* Header */}
        <header className="flex-shrink-0 flex items-center justify-between px-5 py-3" style={{ background: "#0a1628", borderBottom: "1px solid #1e3a5f" }}>
          <div className="flex items-center gap-3">
            <button onClick={() => setSidebarOpen(o => !o)} className="text-slate-500 hover:text-slate-300 text-lg leading-none">☰</button>
            <h1 className="font-bold text-sm tracking-wide text-slate-200">
              CRYPTO TRADING BOT <span className="text-blue-400">|</span> <span className="text-blue-400 text-xs">MASTER CONTROL</span>
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={handleSync}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all"
              style={{ background: syncState === "done" ? "rgba(34,197,94,0.15)" : "rgba(59,130,246,0.15)", border: `1px solid ${syncState === "done" ? "#22c55e" : "#3b82f6"}`, color: syncState === "done" ? "#4ade80" : "#93c5fd" }}>
              <span>{syncState === "syncing" ? "⏳" : syncState === "done" ? "✅" : "✈️"}</span>
              <span>{syncState === "syncing" ? "Syncing..." : syncState === "done" ? "Bot Synced!" : "SYNC WITH TELEGRAM"}</span>
            </button>
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <span className="px-2 py-1 rounded" style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>API: Bitget</span>
              <span className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                <span className="text-green-400">Online</span>
              </span>
            </div>
            <span className="text-xs text-slate-500">{clock.toLocaleTimeString("he-IL")}</span>
            <button className="text-slate-500 hover:text-slate-300">🔔</button>
          </div>
        </header>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto p-4" style={{ background: "#070d1a" }}>
          <div className="grid gap-4" style={{ gridTemplateColumns: "1fr 220px" }}>

            {/* Left column */}
            <div className="space-y-4 min-w-0">

              {/* Financial Overview */}
              <div className="rounded-xl p-4" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-xs font-bold uppercase tracking-widest text-slate-400">Financial Overview</h2>
                  <button onClick={() => setShowHistory(true)}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs transition-all"
                    style={{ background: "rgba(59,130,246,0.1)", border: "1px solid rgba(59,130,246,0.3)", color: "#93c5fd" }}>
                    📜 Trade History
                  </button>
                </div>

                <div className="grid gap-3" style={{ gridTemplateColumns: "1fr 1fr" }}>
                  {/* Left: numbers */}
                  <div className="space-y-2">
                    <div className="flex justify-between items-baseline">
                      <span className="text-xs text-slate-500 uppercase tracking-wide">Starting Balance</span>
                      <span className="font-mono font-bold text-slate-200">${starting.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between items-baseline">
                      <span className="text-xs text-slate-500 uppercase tracking-wide">Current Equity</span>
                      <span className="font-mono font-bold text-2xl" style={{ color: equity >= starting ? "#4ade80" : "#f87171" }}>${equity.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between items-baseline">
                      <span className="text-xs text-slate-500 uppercase tracking-wide">Total P&L</span>
                      <span className={`font-mono font-bold ${totalPnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)}$ ({totalPct >= 0 ? "+" : ""}{totalPct.toFixed(1)}%)
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 mt-1">
                      <div className="rounded-lg p-2 text-center" style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
                        <div className={`text-sm font-bold font-mono ${floating >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {floating >= 0 ? "+" : ""}{floating.toFixed(2)}$
                        </div>
                        <div className="text-xs text-slate-500 mt-0.5">Floating P&L</div>
                      </div>
                      <div className="rounded-lg p-2 text-center" style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
                        <div className={`text-sm font-bold font-mono ${realized >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {realized >= 0 ? "+" : ""}{realized.toFixed(2)}$
                        </div>
                        <div className="text-xs text-slate-500 mt-0.5">Realized P&L</div>
                      </div>
                    </div>
                  </div>
                  {/* Right: sparkline */}
                  <div className="flex items-end justify-center pb-1">
                    <div>
                      <div className="text-xs text-slate-600 text-center mb-1">Equity Curve</div>
                      <Sparkline />
                    </div>
                  </div>
                </div>
              </div>

              {/* Active Trades */}
              <div className="rounded-xl overflow-hidden" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #1e3a5f" }}>
                  <h2 className="text-xs font-bold uppercase tracking-widest text-slate-400">Active Trades</h2>
                  <span className="text-xs px-2 py-0.5 rounded-full font-mono"
                    style={{ background: "rgba(59,130,246,0.15)", border: "1px solid #3b82f6", color: "#93c5fd" }}>
                    {TRADES.length} / {maxTrades} · Max: {maxTrades}
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr style={{ background: "#0d1f3c", borderBottom: "1px solid #1e3a5f" }}>
                        {["COIN ↗", "LVRG", "AMOUNT", "ENTRY", "SL", "TP", "EST.", "P&L", "STATUS"].map(h => (
                          <th key={h} className="px-3 py-2 text-left font-semibold text-slate-500 uppercase tracking-wide whitespace-nowrap">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {TRADES.map((t, i) => {
                        const isLong = t.direction === "LONG";
                        const bgSymbol = t.symbol.replace("/", "");
                        const tvUrl = `https://www.bitget.com/futures/usdt/${bgSymbol}`;
                        return (
                          <tr key={i} className="transition-colors" style={{ borderBottom: "1px solid #0d1f3c" }}
                            onMouseEnter={e => (e.currentTarget.style.background = "#0d1f3c")}
                            onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                            <td className="px-3 py-2.5">
                              <div className="flex items-center gap-2">
                                <span className={`text-xs px-1.5 py-0.5 rounded font-bold ${isLong ? "text-green-400" : "text-red-400"}`}
                                  style={{ background: isLong ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)" }}>
                                  {isLong ? "▲" : "▼"}
                                </span>
                                <a href={tvUrl} target="_blank" rel="noopener noreferrer"
                                  className="font-mono font-semibold text-blue-400 hover:text-blue-300 hover:underline transition-colors">
                                  {t.symbol}
                                </a>
                              </div>
                            </td>
                            <td className="px-3 py-2.5 font-mono text-slate-300">{t.leverage}x</td>
                            <td className="px-3 py-2.5 font-mono text-slate-300">${t.amount}</td>
                            <td className="px-3 py-2.5 font-mono text-slate-300">{t.entry}</td>
                            <td className="px-3 py-2.5 font-mono text-red-400">{t.sl}</td>
                            <td className="px-3 py-2.5 font-mono text-green-400">{t.tp}</td>
                            <td className="px-3 py-2.5 font-mono text-blue-300">+${t.estProfit.toFixed(2)}</td>
                            <td className="px-3 py-2.5 font-mono font-bold" style={{ color: t.pnl >= 0 ? "#4ade80" : "#f87171" }}>
                              {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)}$
                            </td>
                            <td className="px-3 py-2.5">
                              <span className="flex items-center gap-1 text-green-400">
                                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                                {t.status}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Scan Status */}
              <div className="rounded-xl overflow-hidden" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #1e3a5f" }}>
                  <h2 className="text-xs font-bold uppercase tracking-widest text-slate-400">Scan Status</h2>
                  <div className="flex gap-1">
                    {(["ALL", "BUY", "SELL", "HOLD"] as const).map(f => (
                      <button key={f} onClick={() => setScanFilter(f)}
                        className="px-2.5 py-1 rounded text-xs font-semibold transition-all"
                        style={{
                          background: scanFilter === f ? (f === "BUY" ? "rgba(34,197,94,0.2)" : f === "SELL" ? "rgba(239,68,68,0.2)" : f === "HOLD" ? "rgba(250,204,21,0.15)" : "rgba(59,130,246,0.2)") : "rgba(30,58,95,0.5)",
                          color: scanFilter === f ? (f === "BUY" ? "#4ade80" : f === "SELL" ? "#f87171" : f === "HOLD" ? "#facc15" : "#93c5fd") : "#64748b",
                          border: `1px solid ${scanFilter === f ? (f === "BUY" ? "#22c55e" : f === "SELL" ? "#ef4444" : f === "HOLD" ? "#facc15" : "#3b82f6") : "#1e3a5f"}`,
                        }}>
                        {f}
                      </button>
                    ))}
                  </div>
                </div>
                <table className="w-full text-xs">
                  <thead>
                    <tr style={{ background: "#0d1f3c", borderBottom: "1px solid #1e3a5f" }}>
                      {["COIN", "SIGNAL", "CONFIDENCE", "LAST SCANNED"].map(h => (
                        <th key={h} className="px-4 py-2 text-left font-semibold text-slate-500 uppercase tracking-wide">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredScan.map((c, i) => {
                      const sigColor = c.signal === "BUY" ? "#4ade80" : c.signal === "SELL" ? "#f87171" : "#facc15";
                      const sigBg   = c.signal === "BUY" ? "rgba(34,197,94,0.1)" : c.signal === "SELL" ? "rgba(239,68,68,0.1)" : "rgba(250,204,21,0.1)";
                      const confColor = c.confidence === "High" ? "#4ade80" : c.confidence === "Medium" ? "#facc15" : "#f87171";
                      return (
                        <tr key={i} style={{ borderBottom: "1px solid #0d1f3c" }}
                          onMouseEnter={e => (e.currentTarget.style.background = "#0d1f3c")}
                          onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                          <td className="px-4 py-2.5 font-mono font-semibold text-slate-300">{c.symbol}</td>
                          <td className="px-4 py-2.5">
                            <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ color: sigColor, background: sigBg }}>{c.signal}</span>
                          </td>
                          <td className="px-4 py-2.5" style={{ color: confColor }}>{c.confidence}</td>
                          <td className="px-4 py-2.5 text-slate-500">{c.scannedAgo}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Right column */}
            <div className="space-y-4">

              {/* Fear & Greed */}
              <div className="rounded-xl p-4" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                <h2 className="text-xs font-bold uppercase tracking-widest text-slate-400 mb-2">Fear & Greed Index</h2>
                <FngGauge value={FNG_VALUE} />
                <div className="text-center mt-1">
                  <span className="text-sm font-bold text-yellow-400">{FNG_VALUE} – Neutral</span>
                </div>
              </div>

              {/* Bot Settings */}
              <div className="rounded-xl p-4" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
                <h2 className="text-xs font-bold uppercase tracking-widest text-slate-400 mb-4">Bot Settings</h2>
                <Slider label="Max Concurrent Trades" value={maxTrades} min={1} max={5} unit="" onChange={setMaxTrades} />
                <Slider label="Amount per Trade ($)" value={amountPerTrade} min={10} max={100} unit="$" onChange={setAmountPerTrade} />
                <Slider label="Default Leverage (x)" value={leverage} min={1} max={20} unit="x" onChange={setLeverage} />
                <button onClick={handleSaveSettings}
                  className="w-full py-2 rounded-lg text-xs font-bold uppercase tracking-widest transition-all"
                  style={{
                    background: settingsSaved ? "rgba(34,197,94,0.2)" : "linear-gradient(135deg,#1d4ed8,#2563eb)",
                    color: settingsSaved ? "#4ade80" : "#fff",
                    border: settingsSaved ? "1px solid #22c55e" : "1px solid transparent",
                  }}>
                  {settingsSaved ? "✅ Saved!" : "Save Settings"}
                </button>
              </div>

            </div>
          </div>
        </div>
      </div>

      {/* ── Trade History Modal ── */}
      {showHistory && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(7,13,26,0.9)", backdropFilter: "blur(4px)" }}>
          <div className="w-full max-w-3xl max-h-[80vh] flex flex-col rounded-2xl overflow-hidden m-4"
            style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
            <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid #1e3a5f" }}>
              <div className="flex items-center gap-2">
                <span>📜</span>
                <h2 className="font-bold text-slate-200">Full Trade History</h2>
                <span className="text-xs px-2 py-0.5 rounded-full text-slate-400" style={{ background: "#0d1f3c" }}>{HISTORY.length} trades</span>
              </div>
              <button onClick={() => setShowHistory(false)}
                className="w-7 h-7 rounded-full flex items-center justify-center text-slate-400 hover:text-white transition-colors"
                style={{ background: "#0d1f3c" }}>✕</button>
            </div>
            <div className="overflow-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0" style={{ background: "#0d1f3c" }}>
                  <tr>
                    {["ID", "PAIR", "DIR", "BUY TIME", "BUY PRICE", "SELL TIME", "SELL PRICE", "LVRG", "VOL", "P&L", "REASON"].map(h => (
                      <th key={h} className="px-3 py-2.5 text-left text-slate-500 font-semibold uppercase tracking-wide whitespace-nowrap"
                        style={{ borderBottom: "1px solid #1e3a5f" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {HISTORY.map((t, i) => {
                    const win = t.pnl > 0;
                    const reasonColors: Record<string, string> = { TP: "#4ade80", SL: "#f87171", BE: "#facc15" };
                    return (
                      <tr key={i} style={{ borderBottom: "1px solid #0d1f3c" }}
                        onMouseEnter={e => (e.currentTarget.style.background = "#0d1f3c")}
                        onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                        <td className="px-3 py-2 font-mono text-slate-600">{t.id}</td>
                        <td className="px-3 py-2 font-mono font-semibold text-blue-400">{t.symbol}</td>
                        <td className="px-3 py-2">
                          <span className={`font-bold ${t.direction === "LONG" ? "text-green-400" : "text-red-400"}`}>
                            {t.direction === "LONG" ? "▲" : "▼"} {t.direction}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-slate-500 whitespace-nowrap">{t.openTime}</td>
                        <td className="px-3 py-2 font-mono text-slate-300">{t.openPrice.toLocaleString()}</td>
                        <td className="px-3 py-2 text-slate-500 whitespace-nowrap">{t.closeTime}</td>
                        <td className="px-3 py-2 font-mono text-slate-300">{t.closePrice.toLocaleString()}</td>
                        <td className="px-3 py-2 font-mono text-slate-400">{t.leverage}x</td>
                        <td className="px-3 py-2 font-mono text-slate-400">${t.amount}</td>
                        <td className="px-3 py-2 font-mono font-bold whitespace-nowrap" style={{ color: win ? "#4ade80" : t.pnl === 0 ? "#facc15" : "#f87171" }}>
                          {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)}$ ({t.pnlPct >= 0 ? "+" : ""}{t.pnlPct.toFixed(1)}%)
                        </td>
                        <td className="px-3 py-2">
                          <span className="px-1.5 py-0.5 rounded text-xs font-bold"
                            style={{ color: reasonColors[t.reason] ?? "#94a3b8", background: "rgba(255,255,255,0.05)" }}>
                            {t.reason}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
