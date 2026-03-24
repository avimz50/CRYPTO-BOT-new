import { useEffect, useState } from "react";

const WATCHLIST = {
  "TOP 10": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
  "AI Gems": ["FET/USDT", "RENDER/USDT", "NEAR/USDT"],
  "Energy/Geo": ["PAXG/USDT", "POWR/USDT", "HNT/USDT"],
};

const STRATEGIES = [
  {
    category: "TOP 10",
    icon: "🏆",
    name: "RSI Oversold + Trend",
    description: "RSI < 35 + מחיר מעל EMA 200",
    color: "from-blue-500 to-blue-600",
  },
  {
    category: "AI Gems",
    icon: "🤖",
    name: "24H High Breakout",
    description: "פריצת שיא של 24 שעות אחרונות",
    color: "from-violet-500 to-violet-600",
  },
  {
    category: "Energy/Geo",
    icon: "⚡",
    name: "EMA Cross 9/21",
    description: "חציית ממוצעים נעים EMA 9 מעל EMA 21",
    color: "from-amber-500 to-amber-600",
  },
];

function useClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return time;
}

export default function App() {
  const now = useClock();
  const nextScan = new Date(
    Math.ceil(now.getTime() / (60 * 60 * 1000)) * (60 * 60 * 1000)
  );
  const diff = Math.max(0, Math.floor((nextScan.getTime() - now.getTime()) / 1000));
  const hh = String(Math.floor(diff / 3600)).padStart(2, "0");
  const mm = String(Math.floor((diff % 3600) / 60)).padStart(2, "0");
  const ss = String(diff % 60).padStart(2, "0");

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
            <p className="text-3xl font-bold text-white">9</p>
            <p className="text-gray-400 text-sm mt-1">מטבעות במעקב</p>
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

        {/* Scan Info */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-lg">🔍</span>
            <h2 className="font-semibold text-gray-200">מחזורי סריקה</h2>
          </div>
          <div className="grid grid-cols-2 gap-4 text-sm">
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

        {/* Strategies */}
        <div>
          <h2 className="font-semibold text-gray-200 mb-3 flex items-center gap-2">
            <span>📊</span> אסטרטגיות מסחר
          </h2>
          <div className="grid grid-cols-1 gap-3">
            {STRATEGIES.map((s) => (
              <div
                key={s.category}
                className="bg-gray-900 border border-gray-800 rounded-xl p-4 flex items-center gap-4"
              >
                <div className={`w-10 h-10 rounded-lg bg-gradient-to-br ${s.color} flex items-center justify-center text-xl flex-shrink-0`}>
                  {s.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="font-medium text-white">{s.name}</p>
                    <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded-full">
                      {s.category}
                    </span>
                  </div>
                  <p className="text-gray-400 text-sm mt-0.5">{s.description}</p>
                </div>
                <div className="text-left flex-shrink-0">
                  <p className="text-xs text-gray-500 mb-1">מטבעות</p>
                  <div className="flex flex-col gap-0.5">
                    {WATCHLIST[s.category as keyof typeof WATCHLIST]?.map((sym) => (
                      <span key={sym} className="text-xs font-mono text-gray-300 bg-gray-800 px-1.5 py-0.5 rounded">
                        {sym.replace("/USDT", "")}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Telegram notice */}
        <div className="bg-blue-500/10 border border-blue-500/20 rounded-xl p-4 flex items-start gap-3">
          <span className="text-2xl flex-shrink-0">✈️</span>
          <div>
            <p className="font-medium text-blue-300">עדכונים בטלגרם</p>
            <p className="text-blue-400/80 text-sm mt-1">
              כל האיתותים, פתיחת עסקאות, סגירות TP/SL והדוח היומי נשלחים ישירות לטלגרם שלך בזמן אמת.
            </p>
          </div>
        </div>

        <p className="text-center text-gray-600 text-xs pb-4">
          {now.toLocaleString("he-IL")} · Trading Bot v1.0
        </p>
      </div>
    </div>
  );
}
