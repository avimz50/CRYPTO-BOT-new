import { useEffect, useState } from "react";

const ENERGY_GEO = ["PAXG/USDT", "POWR/USDT", "HNT/USDT"];

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

  const fetchData = () => {
    fetch("/hot_candidates.json?t=" + Date.now())
      .then((r) => r.json())
      .then(setData)
      .catch(() => {});
  };

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 60_000);
    return () => clearInterval(id);
  }, []);

  return data;
}

function formatVolume(v: number) {
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(1)}B`;
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  return `$${v.toLocaleString()}`;
}

export default function App() {
  const now = useClock();
  const hotData = useHotCandidates();

  const nextScan = new Date(
    Math.ceil(now.getTime() / (60 * 60 * 1000)) * (60 * 60 * 1000)
  );
  const diff = Math.max(0, Math.floor((nextScan.getTime() - now.getTime()) / 1000));
  const hh = String(Math.floor(diff / 3600)).padStart(2, "0");
  const mm = String(Math.floor((diff % 3600) / 60)).padStart(2, "0");
  const ss = String(diff % 60).padStart(2, "0");

  const candidates = hotData?.candidates ?? [];
  const topFive = candidates.slice(0, 5);
  const rest = candidates.slice(5);

  return (
    <div className="min-h-screen bg-gray-950 text-white" dir="rtl">
      {/* Header */}
      <div className="border-b border-gray-800 bg-gray-900/50 backdrop-blur">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🤖</span>
            <div>
              <h1 className="font-bold text-lg leading-none">Crypto Trading Bot</h1>
              <p className="text-xs text-gray-400 mt-0.5">מצב דמו — Bitget Exchange</p>
            </div>
          </div>
          <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/30 rounded-full px-3 py-1.5">
            <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            <span className="text-green-400 text-sm font-medium">פעיל</span>
          </div>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-6 py-8 space-y-6">

        {/* Stats Row */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center">
            <p className="text-3xl font-bold text-orange-400">
              {candidates.length > 0 ? candidates.length : "—"}
            </p>
            <p className="text-gray-400 text-sm mt-1">מועמדים חמים</p>
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

        {/* Hot Scan Candidates */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-lg">🌡️</span>
              <h2 className="font-semibold text-gray-200">Hot Scan Candidates</h2>
              <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded-full">
                Top 15 Gainers · ווליום $1M+
              </span>
            </div>
            {hotData?.updated && (
              <span className="text-xs text-gray-500">עודכן {hotData.updated}</span>
            )}
          </div>

          {candidates.length === 0 ? (
            <div className="px-5 py-8 text-center text-gray-500 text-sm">
              ממתין לסריקה הראשונה...
            </div>
          ) : (
            <div>
              {/* Top 5 — גדול */}
              <div className="divide-y divide-gray-800/60">
                {topFive.map((c, i) => (
                  <div key={c.symbol} className="px-5 py-3 flex items-center gap-3">
                    <span className="text-gray-600 text-sm w-5 text-center font-mono">{i + 1}</span>
                    <span className="font-mono font-semibold text-white w-24">
                      {c.symbol.replace("/USDT", "")}
                    </span>
                    <span className="text-green-400 font-semibold text-sm w-16">
                      +{c.change_pct.toFixed(2)}%
                    </span>
                    <span className="text-gray-400 text-xs flex-1">
                      {formatVolume(c.volume_usd)}
                    </span>
                    <span className="text-gray-500 text-xs font-mono">
                      ${c.price < 1 ? c.price.toFixed(4) : c.price.toFixed(2)}
                    </span>
                  </div>
                ))}
              </div>

              {/* 6-15 — קומפקטי */}
              {rest.length > 0 && (
                <div className="px-5 py-3 border-t border-gray-800 flex flex-wrap gap-2">
                  {rest.map((c) => (
                    <span
                      key={c.symbol}
                      className="text-xs font-mono bg-gray-800 text-gray-300 px-2 py-1 rounded-lg flex items-center gap-1"
                    >
                      {c.symbol.replace("/USDT", "")}
                      <span className="text-green-400">+{c.change_pct.toFixed(1)}%</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Scan Info */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">🔍</span>
            <h2 className="font-semibold text-gray-200">שיטת סריקה — משפך</h2>
          </div>
          <div className="space-y-3 text-sm">
            <div className="flex gap-3 items-start">
              <span className="text-orange-400 font-bold w-6 flex-shrink-0">1</span>
              <span className="text-gray-300">שליפת כל זוגות USDT מ-Bitget עם <span className="text-white font-medium">fetch_tickers</span></span>
            </div>
            <div className="flex gap-3 items-start">
              <span className="text-orange-400 font-bold w-6 flex-shrink-0">2</span>
              <span className="text-gray-300">סינון Top 15 Gainers עם ווליום <span className="text-white font-medium">$1M+</span> ב-24 שעות</span>
            </div>
            <div className="flex gap-3 items-start">
              <span className="text-orange-400 font-bold w-6 flex-shrink-0">3</span>
              <span className="text-gray-300">סריקה עמוקה על 15 המועמדים בלבד — <span className="text-white font-medium">RSI + Breakout</span></span>
            </div>
            <div className="flex gap-3 items-start">
              <span className="text-amber-400 font-bold w-6 flex-shrink-0">⚡</span>
              <span className="text-gray-300">
                EMA 9/21 על רשימה קבועה:{" "}
                {ENERGY_GEO.map((s) => (
                  <span key={s} className="font-mono text-amber-300 text-xs bg-gray-800 px-1.5 py-0.5 rounded mr-1">
                    {s.replace("/USDT", "")}
                  </span>
                ))}
              </span>
            </div>
          </div>
          <div className="mt-4 pt-4 border-t border-gray-800 grid grid-cols-2 gap-4 text-sm">
            <div className="flex items-center gap-3 text-gray-300">
              <span className="text-gray-500">תדירות</span>
              <span className="font-medium">כל שעה</span>
            </div>
            <div className="flex items-center gap-3 text-gray-300">
              <span className="text-gray-500">דוח יומי</span>
              <span className="font-medium">09:00 בכל בוקר</span>
            </div>
            <div className="flex items-center gap-3 text-gray-300">
              <span className="text-gray-500">סטופ לוס</span>
              <span className="font-medium text-red-400">2% מתחת לכניסה</span>
            </div>
            <div className="flex items-center gap-3 text-gray-300">
              <span className="text-gray-500">יעד רווח</span>
              <span className="font-medium text-green-400">6% מעל הכניסה</span>
            </div>
          </div>
        </div>

        {/* Telegram notice */}
        <div className="bg-blue-500/10 border border-blue-500/20 rounded-xl p-4 flex items-start gap-3">
          <span className="text-2xl flex-shrink-0">✈️</span>
          <div>
            <p className="font-medium text-blue-300">עדכונים בטלגרם</p>
            <p className="text-blue-400/80 text-sm mt-1">
              כל האיתותים, פתיחת עסקאות, סגירות TP/SL והדוח היומי נשלחים ישירות לטלגרם שלך בזמן אמת.
              פקודות: <span className="font-mono">/test</span> · <span className="font-mono">/status</span> · <span className="font-mono">/report</span>
            </p>
          </div>
        </div>

        <p className="text-center text-gray-600 text-xs pb-4">
          {now.toLocaleString("he-IL")} · Trading Bot v2.0 · Funnel Scan
        </p>
      </div>
    </div>
  );
}
