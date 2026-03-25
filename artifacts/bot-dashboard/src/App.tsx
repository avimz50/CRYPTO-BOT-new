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
  score: number;
  atr: number;
  peak_price: number;
  trailing_sl: number | null;
}

interface TradesData {
  updated: string;
  count: number;
  trades: Trade[];
}

function useClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return time;
}

function useHotCandidates() {
  const [data, setData] = useState<HotData | null>(null);
  useEffect(() => {
    const fetch_ = () =>
      fetch("/hot_candidates.json?t=" + Date.now())
        .then((r) => r.json())
        .then(setData)
        .catch(() => {});
    fetch_();
    const id = setInterval(fetch_, 60_000);
    return () => clearInterval(id);
  }, []);
  return data;
}

function useActiveTrades() {
  const [data, setData] = useState<TradesData | null>(null);
  useEffect(() => {
    const fetch_ = () =>
      fetch("/active_trades.json?t=" + Date.now())
        .then((r) => r.json())
        .then(setData)
        .catch(() => {});
    fetch_();
    const id = setInterval(fetch_, 15_000);
    return () => clearInterval(id);
  }, []);
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
    score >= 85 ? "bg-green-400" : score >= 75 ? "bg-yellow-400" : "bg-red-400";
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${color}`}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className="text-xs font-mono text-gray-300">{score}/100</span>
    </div>
  );
}

function TradeCard({ trade }: { trade: Trade }) {
  const isLong = trade.direction === "LONG";
  const dirColor = isLong ? "text-green-400" : "text-red-400";
  const dirBg = isLong
    ? "bg-green-500/10 border-green-500/25"
    : "bg-red-500/10 border-red-500/25";
  const phase = trade.phase === "trailing" ? "🔄 Trailing" : "📊 Initial";
  const beLabel = trade.be_triggered ? " · 🔒 BE" : "";
  const tp1Label = trade.tp1_triggered ? " · TP1 ✅" : "";

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
          </div>
          <p className="text-xs text-gray-500 mt-0.5">
            {phase}{beLabel}{tp1Label}
          </p>
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
        {trade.phase === "trailing" && trade.trailing_sl !== null && (
          <div className="col-span-2 flex justify-between border-t border-gray-700/50 pt-1.5 mt-0.5">
            <span className="text-gray-500">📍 Trailing SL</span>
            <span className="font-mono text-yellow-400">{fmt(trade.trailing_sl)}</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const now = useClock();
  const hotData = useHotCandidates();
  const tradesData = useActiveTrades();

  const nextScan = new Date(
    Math.ceil(now.getTime() / (60 * 60 * 1000)) * (60 * 60 * 1000)
  );
  const diff = Math.max(0, Math.floor((nextScan.getTime() - now.getTime()) / 1000));
  const hh = String(Math.floor(diff / 3600)).padStart(2, "0");
  const mm = String(Math.floor((diff % 3600) / 60)).padStart(2, "0");
  const ss = String(diff % 60).padStart(2, "0");

  const candidates = hotData?.candidates ?? [];
  const trades = tradesData?.trades ?? [];
  const longTrades = trades.filter((t) => t.direction === "LONG");
  const shortTrades = trades.filter((t) => t.direction === "SHORT");

  return (
    <div className="min-h-screen bg-gray-950 text-white" dir="rtl">
      {/* Header */}
      <div className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🤖</span>
            <div>
              <h1 className="font-bold text-lg leading-none">Crypto Trading Bot</h1>
              <p className="text-xs text-gray-400 mt-0.5">Professional Scoring · Bitget Demo</p>
            </div>
          </div>
          <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/30 rounded-full px-3 py-1.5">
            <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            <span className="text-green-400 text-sm font-medium">פעיל</span>
          </div>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-6 py-6 space-y-6">

        {/* Stats Row */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center">
            <p className="text-3xl font-bold text-emerald-400">{trades.length}</p>
            <p className="text-gray-400 text-sm mt-1">עסקאות פעילות</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center">
            <p className="text-3xl font-bold text-blue-400">{hh}:{mm}:{ss}</p>
            <p className="text-gray-400 text-sm mt-1">עד סריקה הבאה</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center">
            <p className="text-3xl font-bold text-violet-400">1:3</p>
            <p className="text-gray-400 text-sm mt-1">יחס סיכון/תשואה</p>
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
              <p className="text-gray-600 text-xs mt-1">הבוט יפתח עסקאות בסריקה הבאה כשניקוד ≥ 75/100</p>
            </div>
          ) : (
            <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
              {trades.map((t) => (
                <TradeCard key={t.symbol} trade={t} />
              ))}
            </div>
          )}
        </div>

        {/* Hot Candidates — compact */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span>🌡️</span>
              <h2 className="font-semibold text-gray-300 text-sm">Hot Scan Candidates</h2>
              <span className="text-xs text-gray-600 bg-gray-800 px-2 py-0.5 rounded-full">
                Top 15 Gainers
              </span>
            </div>
            {hotData?.updated && (
              <span className="text-xs text-gray-600">עודכן {hotData.updated}</span>
            )}
          </div>

          {candidates.length === 0 ? (
            <div className="px-5 py-4 text-center text-gray-600 text-xs">
              ממתין לסריקה הראשונה...
            </div>
          ) : (
            <div className="px-4 py-3 flex flex-wrap gap-2">
              {candidates.map((c, i) => (
                <span
                  key={c.symbol}
                  className="flex items-center gap-1.5 text-xs font-mono bg-gray-800/80 border border-gray-700/50 text-gray-300 px-2.5 py-1.5 rounded-lg"
                >
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
              כל האיתותים, TP/SL, BE והדוח היומי נשלחים בזמן אמת.
              פקודות:{" "}
              <span className="font-mono">/status</span> ·{" "}
              <span className="font-mono">/report</span> ·{" "}
              <span className="font-mono">/close SYMBOL</span>
            </p>
          </div>
        </div>

        <p className="text-center text-gray-700 text-xs pb-4">
          {now.toLocaleTimeString("he-IL")} · Trading Bot v3.0 · Professional Scoring ≥75
        </p>
      </div>
    </div>
  );
}
