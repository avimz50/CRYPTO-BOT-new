import { useEffect, useState } from "react";

interface Candidate {
  symbol: string;
  change_pct: number;
  volume_usd: number;
  price: number;
}

interface HotData {
  updated: string;
  count: number;
  candidates: Candidate[];
}

interface Trade {
  symbol: string;
  entry: number;
  current_price?: number;
  sl: number;
  tp: number;
  tp1: number;
  be_lvl: number;
  sl_pct: number;
  tp_pct: number;
  direction: "LONG" | "SHORT";
  phase: "initial" | "trailing";
  be_triggered: boolean;
  tp1_triggered: boolean;
  tp1_pnl?: number;
  score: number;
  atr: number;
  peak_price: number;
  trailing_sl: number | null;
  timeframe?: string;
}

interface TradesData {
  updated: string;
  count: number;
  trades: Trade[];
}

interface EquityPoint { t: string; eq: number; }
interface WalletData {
  balance: number;
  starting: number;
  total_pnl: number;
  trades_opened: number;
  equity_history: EquityPoint[];
}

function useClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return time;
}

function useJson<T>(url: string, interval = 15_000) {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    const fetch_ = () =>
      fetch(url + "?t=" + Date.now())
        .then((r) => r.json())
        .then(setData)
        .catch(() => {});
    fetch_();
    const id = setInterval(fetch_, interval);
    return () => clearInterval(id);
  }, [url, interval]);
  return data;
}

function fmt(n: number) {
  if (n === undefined || n === null) return "—";
  if (Math.abs(n) < 0.001) return n.toExponential(3);
  if (Math.abs(n) < 1) return n.toFixed(4);
  if (Math.abs(n) < 10000) return n.toFixed(2);
  return n.toLocaleString();
}

function formatVolume(v: number) {
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(1)}B`;
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  return `$${v.toLocaleString()}`;
}

function ScoreBar({ score }: { score: number }) {
  const color =
    score >= 90 ? "bg-green-400" : score >= 80 ? "bg-yellow-400" : "bg-red-400";
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${score}%` }} />
      </div>
      <span className="text-xs font-mono text-gray-300">{score}/100</span>
    </div>
  );
}

/* ── Equity Sparkline SVG ── */
function EquitySparkline({ history, starting }: { history: EquityPoint[]; starting: number }) {
  if (!history || history.length < 2) {
    return <div className="text-xs text-gray-600 text-center py-4">ממתין לנתוני היסטוריה...</div>;
  }
  const vals = history.map((p) => p.eq);
  const min  = Math.min(...vals, starting * 0.95);
  const max  = Math.max(...vals, starting * 1.05);
  const W = 400; const H = 80;
  const pad = 8;
  const pts = history.map((p, i) => {
    const x = pad + (i / (history.length - 1)) * (W - pad * 2);
    const y = H - pad - ((p.eq - min) / (max - min || 1)) * (H - pad * 2);
    return `${x},${y}`;
  }).join(" ");

  // baseline (starting balance)
  const baselineY = H - pad - ((starting - min) / (max - min || 1)) * (H - pad * 2);
  const last  = history[history.length - 1].eq;
  const color = last >= starting ? "#34d399" : "#f87171";
  const lastX = pad + ((history.length - 1) / (history.length - 1)) * (W - pad * 2);
  const lastY = H - pad - ((last - min) / (max - min || 1)) * (H - pad * 2);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 80 }}>
      {/* baseline */}
      <line x1={pad} y1={baselineY} x2={W - pad} y2={baselineY}
        stroke="#374151" strokeWidth={1} strokeDasharray="4 3" />
      {/* area fill */}
      <defs>
        <linearGradient id="eq-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polygon
        points={`${pad},${H - pad} ${pts} ${W - pad},${H - pad}`}
        fill="url(#eq-grad)"
      />
      {/* line */}
      <polyline points={pts} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
      {/* last dot */}
      <circle cx={lastX} cy={lastY} r={4} fill={color} />
    </svg>
  );
}

function TradeCard({ trade }: { trade: Trade }) {
  const isLong  = trade.direction === "LONG";
  const dirColor = isLong ? "text-green-400" : "text-red-400";
  const dirBg   = isLong ? "bg-green-500/10 border-green-500/25" : "bg-red-500/10 border-red-500/25";
  const phase   = trade.phase === "trailing" ? "🔄 Trailing" : "📊 Initial";
  const beLabel = trade.be_triggered ? " · 🔒 BE" : "";
  const tp1Label = trade.tp1_triggered ? " · TP1 ✅" : "";
  const tf      = trade.timeframe ?? "4H";

  // Floating P&L for this trade
  const cp      = trade.current_price ?? trade.entry;
  const rawPct  = (cp - trade.entry) / trade.entry * 100;
  const pnlPct  = isLong ? rawPct : -rawPct;
  const pnlUsd  = trade.tp1_triggered
    ? (trade.tp1_pnl ?? 0) + 250 * pnlPct / 100
    : 500 * pnlPct / 100;
  const isProfit = pnlUsd >= 0;

  return (
    <div className={`border rounded-xl p-4 ${dirBg}`}>
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`font-bold text-lg ${dirColor}`}>
              {trade.symbol.replace("/USDT", "")}
            </span>
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${dirBg} ${dirColor}`}>
              {isLong ? "▲ LONG" : "▼ SHORT"}
            </span>
            <span className="text-xs bg-gray-800 text-gray-400 px-1.5 py-0.5 rounded font-mono">
              {tf}
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-0.5">{phase}{beLabel}{tp1Label}</p>
        </div>
        <ScoreBar score={trade.score} />
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
        <div className="flex justify-between">
          <span className="text-gray-500">כניסה</span>
          <span className="font-mono text-gray-200">{fmt(trade.entry)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">ATR</span>
          <span className="font-mono text-gray-400">{fmt(trade.atr)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">🛑 SL</span>
          <span className="font-mono text-red-400">{fmt(trade.sl)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">🔒 BE</span>
          <span className={`font-mono ${trade.be_triggered ? "text-blue-400" : "text-gray-500"}`}>
            {fmt(trade.be_lvl)}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">🎯 TP1</span>
          <span className={`font-mono ${trade.tp1_triggered ? "text-green-400 line-through" : "text-green-400/70"}`}>
            {fmt(trade.tp1)}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">🎯 TP</span>
          <span className="font-mono text-green-400">{fmt(trade.tp)}</span>
        </div>
        {trade.trailing_sl !== null && (
          <div className="col-span-2 flex justify-between border-t border-gray-700/50 pt-1.5 mt-0.5">
            <span className="text-gray-500">📍 Trailing SL</span>
            <span className="font-mono text-yellow-400">{fmt(trade.trailing_sl)}</span>
          </div>
        )}
        {/* Running Profit / Loss */}
        <div className="col-span-2 flex justify-between border-t border-gray-700/50 pt-2 mt-1">
          <span className="text-gray-400 font-medium">{isProfit ? "💰 Running Profit" : "🔻 Running Loss"}</span>
          <span
            className="font-mono font-bold text-base"
            style={{
              color: isProfit ? "#39ff14" : "#f87171",
              textShadow: isProfit ? "0 0 8px #39ff1460" : "none",
            }}
          >
            {pnlUsd >= 0 ? "+" : ""}{pnlUsd.toFixed(2)}$ ({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%)
          </span>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const now        = useClock();
  const hotData    = useJson<HotData>("/hot_candidates.json", 60_000);
  const tradesData = useJson<TradesData>("/active_trades.json", 15_000);
  const walletData = useJson<WalletData>("/wallet.json", 20_000);

  const nextScan = new Date(Math.ceil(now.getTime() / (60 * 60 * 1000)) * (60 * 60 * 1000));
  const diff  = Math.max(0, Math.floor((nextScan.getTime() - now.getTime()) / 1000));
  const hh    = String(Math.floor(diff / 3600)).padStart(2, "0");
  const mm    = String(Math.floor((diff % 3600) / 60)).padStart(2, "0");
  const ss    = String(diff % 60).padStart(2, "0");

  const candidates  = hotData?.candidates ?? [];
  const trades      = tradesData?.trades ?? [];
  const longTrades  = trades.filter((t) => t.direction === "LONG");
  const shortTrades = trades.filter((t) => t.direction === "SHORT");

  const balance  = walletData?.balance ?? 200;
  const starting = walletData?.starting ?? 200;
  const realized = walletData?.total_pnl ?? 0;
  const history  = walletData?.equity_history ?? [];

  // Floating P&L — calculated from live current_price stored per trade
  const floating = trades.reduce((sum, t) => {
    const cp  = t.current_price ?? t.entry;
    const raw = (cp - t.entry) / t.entry * 100;
    const pct = t.direction === "LONG" ? raw : -raw;
    const usd = t.tp1_triggered
      ? (t.tp1_pnl ?? 0) + 250 * pct / 100
      : 500 * pct / 100;
    return sum + usd;
  }, 0);

  const totalBalance = starting + realized + floating;
  const totalPct     = starting > 0 ? ((totalBalance - starting) / starting * 100) : 0;
  const floatPos     = floating >= 0;
  const realizedPos  = realized >= 0;

  return (
    <div className="min-h-screen bg-gray-950 text-white" dir="rtl">
      {/* Header */}
      <div className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🤖</span>
            <div>
              <h1 className="font-bold text-lg leading-none">Crypto Trading Bot</h1>
              <p className="text-xs text-gray-400 mt-0.5">Professional Scoring · Bitget Demo · 4H/1H</p>
            </div>
          </div>
          <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/30 rounded-full px-3 py-1.5">
            <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            <span className="text-green-400 text-sm font-medium">פעיל</span>
          </div>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-6 py-6 space-y-6">

        {/* ── Wallet Panel ── */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-lg">💼</span>
              <h2 className="font-semibold text-gray-200">ארנק וירטואלי</h2>
              <span className="text-xs text-gray-500">התחיל ב-${starting}</span>
            </div>
            <span className={`text-sm font-bold ${totalBalance >= starting ? "text-emerald-400" : "text-red-400"}`}>
              {totalPct >= 0 ? "+" : ""}{totalPct.toFixed(1)}% total
            </span>
          </div>

          {/* Row 1: Total Balance (big) */}
          <div className="px-5 py-4 border-b border-gray-800 text-center">
            <p className="text-xs text-gray-500 uppercase tracking-widest mb-1">Total Balance</p>
            <p className={`text-4xl font-bold ${totalBalance >= starting ? "text-white" : "text-red-400"}`}>
              ${totalBalance.toFixed(2)}
            </p>
            <p className="text-xs text-gray-500 mt-1">
              ${starting.toFixed(0)} התחלתי
              {totalPct >= 0 ? " +" : " "}{totalPct.toFixed(2)}%
            </p>
          </div>

          {/* Row 2: Realized | Floating | Free Cash */}
          <div className="grid grid-cols-3 divide-x divide-gray-800 text-center">
            <div className="p-4">
              <p className={`text-xl font-bold ${realizedPos ? "text-emerald-400" : "text-red-400"}`}>
                {realizedPos ? "+" : ""}{realized.toFixed(2)}$
              </p>
              <p className="text-xs text-gray-500 mt-1">Realized P&L</p>
            </div>
            <div className="p-4">
              <p className={`text-xl font-bold ${floatPos ? "text-[#39ff14]" : "text-red-500"}`}
                 style={{ textShadow: floatPos ? "0 0 8px #39ff1460" : "none" }}>
                {floatPos ? "+" : ""}{floating.toFixed(2)}$
              </p>
              <p className="text-xs text-gray-500 mt-1">Floating P&L</p>
            </div>
            <div className="p-4">
              <p className="text-xl font-bold text-blue-400">${balance.toFixed(2)}</p>
              <p className="text-xs text-gray-500 mt-1">יתרה פנויה</p>
            </div>
          </div>

          {/* Equity Curve */}
          <div className="px-4 pb-4">
            <EquitySparkline history={history} starting={starting} />
            <div className="flex justify-between text-xs text-gray-600 mt-1 font-mono">
              <span>{history[0]?.t ?? ""}</span>
              <span className="text-gray-500">עקומת Equity</span>
              <span>{history[history.length - 1]?.t ?? ""}</span>
            </div>
          </div>
        </div>

        {/* Stats Row */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center">
            <p className="text-3xl font-bold text-emerald-400">{trades.length}<span className="text-lg text-gray-600">/3</span></p>
            <p className="text-gray-400 text-sm mt-1">עסקאות פעילות</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center">
            <p className="text-3xl font-bold text-blue-400">{hh}:{mm}:{ss}</p>
            <p className="text-gray-400 text-sm mt-1">עד סריקה הבאה</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center">
            <p className="text-3xl font-bold text-violet-400">90+</p>
            <p className="text-gray-400 text-sm mt-1">סף כניסה</p>
          </div>
        </div>

        {/* Active Trades */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-lg">📊</span>
              <h2 className="font-semibold text-gray-200">עסקאות פעילות</h2>
              {trades.length > 0 && (
                <span dir="ltr" className="text-xs bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-full">
                  {longTrades.length} LONG · {shortTrades.length} SHORT
                </span>
              )}
            </div>
            {tradesData?.updated && (
              <span className="text-xs text-gray-500">עודכן {tradesData.updated}</span>
            )}
          </div>

          {trades.length === 0 ? (
            <div className="px-5 py-10 text-center">
              <p className="text-gray-500 text-sm">אין עסקאות פעילות כרגע</p>
              <p className="text-gray-600 text-xs mt-1">הבוט יפתח עסקאות כשניקוד ≥ 90/100</p>
            </div>
          ) : (
            <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
              {trades.map((t) => (
                <TradeCard key={t.symbol} trade={t} />
              ))}
            </div>
          )}
        </div>

        {/* Hot Candidates */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span>🌡️</span>
              <h2 className="font-semibold text-gray-300 text-sm">Hot Scan Candidates</h2>
              <span className="text-xs text-gray-600 bg-gray-800 px-2 py-0.5 rounded-full">Top 15 Gainers</span>
            </div>
            {hotData?.updated && (
              <span className="text-xs text-gray-600">עודכן {hotData.updated}</span>
            )}
          </div>
          {candidates.length === 0 ? (
            <div className="px-5 py-4 text-center text-gray-600 text-xs">ממתין לסריקה הראשונה...</div>
          ) : (
            <div className="px-4 py-3 flex flex-wrap gap-2">
              {candidates.map((c, i) => (
                <span key={c.symbol}
                  className="flex items-center gap-1.5 text-xs font-mono bg-gray-800/80 border border-gray-700/50 text-gray-300 px-2.5 py-1.5 rounded-lg">
                  <span className="text-gray-600">{i + 1}.</span>
                  <span className="font-semibold">{c.symbol.replace("/USDT", "")}</span>
                  <span className="text-green-400">+{c.change_pct.toFixed(1)}%</span>
                  <span className="text-gray-600">{formatVolume(c.volume_usd)}</span>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Info bar */}
        <div className="bg-blue-500/10 border border-blue-500/20 rounded-xl p-4 flex items-start gap-3">
          <span className="text-2xl flex-shrink-0">✈️</span>
          <div>
            <p className="font-medium text-blue-300">עדכונים בטלגרם</p>
            <p className="text-blue-400/80 text-sm mt-1">
              כל האיתותים, TP/SL, BE, Trailing והדוח היומי נשלחים בזמן אמת.
              פקודות:{" "}
              <span className="font-mono">/status</span> ·{" "}
              <span className="font-mono">/report</span> ·{" "}
              <span className="font-mono">/close SYMBOL</span>
            </p>
          </div>
        </div>

        <p className="text-center text-gray-700 text-xs pb-4">
          {now.toLocaleTimeString("he-IL")} · Trading Bot v4.1 · Score ≥90 · Max 3 Trades · 4H→1H→15m · Anti-FOMO
        </p>
      </div>
    </div>
  );
}
