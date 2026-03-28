import { useEffect, useState } from "react";

/* ══════════════════════════════════════════════
   7-SEGMENT DISPLAY
   W=22 H=38 per digit — classic LED look
══════════════════════════════════════════════ */
const SEG_MAP: Record<string, boolean[]> = {
  //        a      b      c      d      e      f      g
  '0': [true,  true,  true,  true,  true,  true,  false],
  '1': [false, true,  true,  false, false, false, false],
  '2': [true,  true,  false, true,  true,  false, true ],
  '3': [true,  true,  true,  true,  false, false, true ],
  '4': [false, true,  true,  false, false, true,  true ],
  '5': [true,  false, true,  true,  false, true,  true ],
  '6': [true,  false, true,  true,  true,  true,  true ],
  '7': [true,  true,  true,  false, false, false, false],
  '8': [true,  true,  true,  true,  true,  true,  true ],
  '9': [true,  true,  true,  true,  false, true,  true ],
};
const ON  = '#facc15';   // yellow-400
const DIM = '#1c1600';   // barely visible "off" segment
const GLW = '0 0 8px #facc15cc';

function SevenSegDigit({ digit }: { digit: string }) {
  const s = SEG_MAP[digit] ?? Array(7).fill(false);
  const W = 15, H = 26, T = 2, G = 1, R = 1, H2 = H / 2;
  const seg = (on: boolean, x: number, y: number, w: number, h: number) => (
    <rect x={x} y={y} width={w} height={h} rx={R} ry={R}
      fill={on ? ON : DIM}
      style={on ? { filter: `drop-shadow(${GLW})` } : undefined} />
  );
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} style={{ display: 'block' }}>
      {/* a – top */}       {seg(s[0], T+G,   0,       W-2*T-2*G, T)}
      {/* b – top-right */} {seg(s[1], W-T,   T+G,     T, H2-T-2*G)}
      {/* c – bot-right */} {seg(s[2], W-T,   H2+G,    T, H2-T-2*G)}
      {/* d – bottom */}    {seg(s[3], T+G,   H-T,     W-2*T-2*G, T)}
      {/* e – bot-left */}  {seg(s[4], 0,     H2+G,    T, H2-T-2*G)}
      {/* f – top-left */}  {seg(s[5], 0,     T+G,     T, H2-T-2*G)}
      {/* g – middle */}    {seg(s[6], T+G,   H2-T/2,  W-2*T-2*G, T)}
    </svg>
  );
}

function SevenSegColon() {
  return (
    <div style={{ width: 7, height: 26, display: 'flex', flexDirection: 'column',
                  alignItems: 'center', justifyContent: 'space-evenly', paddingBottom: 1 }}>
      {[0, 1].map(i => (
        <div key={i} style={{
          width: 4, height: 4, borderRadius: '50%',
          background: ON, boxShadow: GLW,
        }} />
      ))}
    </div>
  );
}

function SevenSegDisplay({ value }: { value: string }) {
  return (
    <div dir="ltr" style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
      {value.split('').map((ch, i) =>
        ch === ':' ? <SevenSegColon key={i} /> : <SevenSegDigit key={i} digit={ch} />
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════
   FEAR & GREED SPEEDOMETER GAUGE
══════════════════════════════════════════════ */
const FNG_ZONES = [
  { from: 0,  to: 20,  color: '#ef4444' },
  { from: 20, to: 40,  color: '#f97316' },
  { from: 40, to: 60,  color: '#facc15' },
  { from: 60, to: 80,  color: '#84cc16' },
  { from: 80, to: 100, color: '#22c55e' },
];

function fngColor(v: number) {
  if (v < 20) return '#ef4444';
  if (v < 40) return '#f97316';
  if (v < 60) return '#facc15';
  if (v < 80) return '#84cc16';
  return '#22c55e';
}
function fngLabel(v: number) {
  if (v < 25) return 'פחד קיצוני';
  if (v < 45) return 'פחד';
  if (v < 55) return 'נייטרלי';
  if (v < 75) return 'חמדנות';
  return 'חמדנות קיצונית';
}

function FearGreedGauge({ value, label }: { value: number | null; label: string | null }) {
  const cx = 100, cy = 102, R = 78;
  const v = value ?? 50;

  const pt = (r: number, val: number) => {
    const θ = (180 - (val / 100) * 180) * Math.PI / 180;
    return { x: cx + r * Math.cos(θ), y: cy - r * Math.sin(θ) };
  };

  const arc = (from: number, to: number, r: number) => {
    const p1 = pt(r, from), p2 = pt(r, to);
    return `M ${p1.x.toFixed(1)} ${p1.y.toFixed(1)} A ${r} ${r} 0 0 1 ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  };

  const needle = pt(68, v);
  const color  = fngColor(v);

  return (
    <svg viewBox="0 0 200 118" width="100%" style={{ display: 'block', margin: '0 auto', maxWidth: 170 }}>
      <defs>
        <filter id="fg-glow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="2" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <radialGradient id="hub-grad" cx="40%" cy="35%">
          <stop offset="0%" stopColor="#4b5563"/>
          <stop offset="100%" stopColor="#111827"/>
        </radialGradient>
      </defs>

      {/* Dim background tracks */}
      {FNG_ZONES.map((z, i) => (
        <path key={`bg${i}`} d={arc(z.from, z.to, R)}
          fill="none" stroke={z.color} strokeWidth={14}
          strokeLinecap={i === 0 || i === 4 ? 'round' : 'butt'}
          opacity={0.2} />
      ))}

      {/* Filled arcs */}
      {FNG_ZONES.map((z, i) => {
        const end = Math.min(v, z.to);
        if (end <= z.from) return null;
        const isLast = end < z.to;
        return (
          <path key={`fill${i}`} d={arc(z.from, end, R)}
            fill="none" stroke={z.color} strokeWidth={14}
            strokeLinecap={i === 0 || isLast ? 'round' : 'butt'}
            filter="url(#fg-glow)" />
        );
      })}

      {/* Tick marks */}
      {[0, 25, 50, 75, 100].map(tv => {
        const inn = pt(R - 9, tv), out = pt(R + 3, tv);
        return <line key={tv} x1={inn.x} y1={inn.y} x2={out.x} y2={out.y}
          stroke="white" strokeWidth={1.2} opacity={0.35} />;
      })}

      {/* Needle shadow */}
      <line x1={cx} y1={cy} x2={needle.x} y2={needle.y}
        stroke="#000" strokeWidth={3.5} strokeLinecap="round" opacity={0.5} />

      {/* Needle */}
      <line x1={cx} y1={cy} x2={needle.x} y2={needle.y}
        stroke="white" strokeWidth={2.2} strokeLinecap="round"
        filter="url(#fg-glow)"
        style={{ transition: 'all 1.2s cubic-bezier(0.34,1.56,0.64,1)' }} />

      {/* Needle tip dot */}
      <circle cx={needle.x} cy={needle.y} r={3} fill={color} filter="url(#fg-glow)" />

      {/* Hub */}
      <circle cx={cx} cy={cy} r={6} fill="url(#hub-grad)" stroke={color} strokeWidth={2} />

      {/* Value */}
      <text x={cx} y={cy - 20} textAnchor="middle"
        fill={color} fontSize={22} fontWeight="bold" fontFamily="monospace"
        filter="url(#fg-glow)">{value ?? '—'}</text>

      {/* Zone label */}
      <text x={cx} y={cy - 7} textAnchor="middle"
        fill="#d1d5db" fontSize={7.5} fontFamily="sans-serif">
        {fngLabel(v)}
      </text>

      {/* Side labels */}
      <text x={9}   y={cy + 16} textAnchor="middle" fontSize={12}>😱</text>
      <text x={191} y={cy + 16} textAnchor="middle" fontSize={12}>🤑</text>

      {/* Numeric scale */}
      {[0, 50, 100].map(tv => {
        const p = pt(R + 12, tv);
        return <text key={tv} x={p.x} y={p.y + 3} textAnchor="middle"
          fill="#6b7280" fontSize={6.5} fontFamily="monospace">{tv}</text>;
      })}
    </svg>
  );
}

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

function useJson<T>(primaryUrl: string, fallbackUrl: string, interval = 30_000) {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    const fetch_ = () =>
      fetch(primaryUrl + "?t=" + Date.now())
        .then((r) => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
        .then(setData)
        .catch(() =>
          fetch(fallbackUrl + "?t=" + Date.now())
            .then((r) => r.json())
            .then(setData)
            .catch(() => {})
        );
    fetch_();
    const id = setInterval(fetch_, interval);
    return () => clearInterval(id);
  }, [primaryUrl, fallbackUrl, interval]);
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

  // Expected P&L at TP and SL  (position size = $500 = $50 margin × 10x leverage)
  const POSITION = 500;
  const estProfit = trade.tp && trade.entry
    ? Math.abs(trade.tp - trade.entry) / trade.entry * POSITION
    : null;
  const estLoss = trade.sl && trade.entry
    ? Math.abs(trade.sl - trade.entry) / trade.entry * POSITION
    : null;
  const rr = estProfit && estLoss && estLoss > 0
    ? (estProfit / estLoss).toFixed(2)
    : null;

  // price movement colour (green = good for this direction)
  const priceUp   = cp > trade.entry;
  const priceGood = isLong ? priceUp : !priceUp;
  const cpColor   = priceGood ? "text-green-400" : "text-red-400";

  // Bitget futures chart link
  const bgSymbol  = trade.symbol.replace("/", "");
  const tvUrl     = `https://www.bitget.com/futures/usdt/${bgSymbol}`;

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
            {/* TradingView chart link */}
            <a
              href={tvUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs bg-blue-900/40 hover:bg-blue-800/60 border border-blue-700/40 text-blue-300 px-2 py-0.5 rounded transition-colors"
              title="פתח גרף ב-Bitget"
            >
              📈 גרף Bitget
            </a>
          </div>
          <p className="text-xs text-gray-500 mt-0.5">{phase}{beLabel}{tp1Label}</p>
        </div>
        <ScoreBar score={trade.score} />
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
        {/* מחיר נוכחי — שורה ראשונה ומודגשת */}
        <div className="col-span-2 flex justify-between items-center bg-gray-800/50 rounded-lg px-3 py-1.5 mb-1">
          <span className="text-gray-400 font-medium">💹 מחיר נוכחי</span>
          <span className={`font-mono font-bold text-base ${cpColor}`}>
            {fmt(cp)}
            <span className="text-xs text-gray-500 font-normal ml-1">
              ({rawPct >= 0 ? "+" : ""}{rawPct.toFixed(2)}% מכניסה)
            </span>
          </span>
        </div>

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

        {/* ── Expected P&L at TP / SL ── */}
        {(estProfit !== null || estLoss !== null) && (
          <div className="col-span-2 border-t border-gray-700/50 mt-1 pt-2 space-y-1">
            {estProfit !== null && (
              <div className="flex justify-between items-center">
                <span className="text-gray-400 text-xs">Est. Profit at TP</span>
                <span className="font-mono font-semibold text-green-400">
                  +${estProfit.toFixed(2)}
                </span>
              </div>
            )}
            {estLoss !== null && (
              <div className="flex justify-between items-center">
                <span className="text-gray-400 text-xs">Est. Loss at SL</span>
                <span className="font-mono font-semibold text-red-400">
                  -${estLoss.toFixed(2)}
                </span>
              </div>
            )}
            {rr !== null && (
              <div className="flex justify-between items-center">
                <span className="text-gray-500 text-xs">Risk / Reward</span>
                <span className="font-mono text-xs text-yellow-400">1 : {rr}</span>
              </div>
            )}
          </div>
        )}

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

// Deployed bot Flask API — single source of truth for live data
const BOT_API = "https://python-script-bymzrkhy.replit.app";

interface FngData { value: number; label: string; }

interface RejectedCoin {
  symbol: string;
  direction: string;
  best_score: number;
  reason: string;
  scores?: { "4H"?: number; "1H"?: number; "15m"?: number };
}
interface ScanData {
  scan_time: string;
  total_scanned: number;
  signals_found: number;
  active_trades_count: number;
  btc_regime: string;
  fng_value: number;
  fng_label: string;
  market_sentiment_factor: string;
  rejected_coins: RejectedCoin[];
  system_message: string;
  scan_duration_s: number;
}

function LastScanStatus({ scan }: { scan: ScanData | null }) {
  if (!scan) return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center text-gray-600 text-xs">
      ממתין לסריקה הראשונה...
    </div>
  );
  const scanTime  = scan.scan_time ? new Date(scan.scan_time).toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" }) : "—";
  const scanDate  = scan.scan_time ? new Date(scan.scan_time).toLocaleDateString("he-IL") : "";
  const regEmoji  = scan.btc_regime === "BULL" ? "🟢" : scan.btc_regime === "BEAR" ? "🔴" : "🟡";
  const sigColor  = scan.signals_found > 0 ? "text-green-400" : "text-gray-500";

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden"
         style={{ background: "linear-gradient(135deg, #0d1117 0%, #0a1628 100%)" }}>
      {/* Header */}
      <div className="px-5 py-3 border-b border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-base">🔍</span>
          <h2 className="font-semibold text-gray-300 text-sm">Last Scan Status</h2>
          {scan.signals_found > 0 && (
            <span className="text-xs bg-green-500/20 text-green-400 border border-green-500/30 px-2 py-0.5 rounded-full">
              {scan.signals_found} איתות/ות
            </span>
          )}
        </div>
        <span className="text-xs text-gray-600">{scanDate} · {scanTime} · {scan.scan_duration_s}s</span>
      </div>

      <div className="p-4 space-y-3">
        {/* Summary row */}
        <div className="grid grid-cols-3 gap-3 text-center">
          <div className="bg-gray-800/50 rounded-lg py-2">
            <p className="text-lg font-bold text-blue-400">{scan.total_scanned}</p>
            <p className="text-xs text-gray-500">מטבעות שנסרקו</p>
          </div>
          <div className="bg-gray-800/50 rounded-lg py-2">
            <p className={`text-lg font-bold ${sigColor}`}>{scan.signals_found}</p>
            <p className="text-xs text-gray-500">איתותים שנמצאו</p>
          </div>
          <div className="bg-gray-800/50 rounded-lg py-2">
            <p className="text-lg font-bold text-gray-300">{regEmoji} {scan.btc_regime}</p>
            <p className="text-xs text-gray-500">BTC Regime</p>
          </div>
        </div>

        {/* Sentiment factor */}
        <div className="bg-gray-800/30 border border-gray-700/30 rounded-lg px-3 py-2 text-xs text-gray-400">
          <span className="text-yellow-400 font-semibold">📊 Sentiment: </span>
          {scan.market_sentiment_factor}
        </div>

        {/* System message */}
        <div className="bg-blue-900/20 border border-blue-700/30 rounded-lg px-3 py-2 text-xs text-blue-300">
          <span className="font-semibold">💬 </span>{scan.system_message}
        </div>

        {/* Rejected coins — top 5 near-misses */}
        {scan.rejected_coins && scan.rejected_coins.length > 0 && (
          <div>
            <p className="text-xs text-gray-500 mb-2 font-semibold uppercase tracking-wide">
              🚫 Top Near-Misses (לא עברו סף {scan.min_score ?? 90}/100)
            </p>
            <div className="space-y-1.5">
              {scan.rejected_coins.slice(0, 5).map((c, i) => {
                const dirColor  = c.direction === "LONG" ? "text-green-400" : "text-red-400";
                const scoreColor = c.best_score >= 80 ? "text-yellow-400" : c.best_score >= 60 ? "text-orange-400" : "text-gray-500";
                return (
                  <div key={`${c.symbol}-${i}`}
                       className="flex items-center justify-between bg-gray-800/40 rounded-lg px-3 py-1.5 text-xs">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-gray-600 w-4">{i + 1}.</span>
                      <span className="font-mono font-semibold text-gray-200">{c.symbol.replace("/USDT", "")}</span>
                      <span className={`font-semibold ${dirColor}`}>{c.direction}</span>
                    </div>
                    <div className="flex items-center gap-3 shrink-0 ml-2">
                      <span className={`font-mono font-bold ${scoreColor}`}>{c.best_score}/100</span>
                      <span className="text-gray-500 text-right max-w-[160px] truncate">{c.reason}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const now        = useClock();
  const hotData    = useJson<HotData>(`${BOT_API}/api/hot`,    "/hot_candidates.json", 60_000);
  const tradesData = useJson<TradesData>(`${BOT_API}/api/trades`, "/active_trades.json", 30_000);
  const walletData = useJson<WalletData>(`${BOT_API}/api/wallet`, "/wallet.json",        30_000);
  const fngData    = useJson<FngData>(`${BOT_API}/api/fng`,    "/fng.json",             300_000);
  const scanData   = useJson<ScanData>(`${BOT_API}/api/last_scan`, "/last_scan_results.json", 120_000);

  const SCAN_INTERVAL = 3600; // seconds
  const nextScan = new Date(Math.ceil(now.getTime() / (SCAN_INTERVAL * 1000)) * (SCAN_INTERVAL * 1000));
  const diff  = Math.max(0, Math.floor((nextScan.getTime() - now.getTime()) / 1000));
  const mm    = String(Math.floor(diff / 60)).padStart(2, "0");
  const ss    = String(diff % 60).padStart(2, "0");
  const scanPct = Math.round(((SCAN_INTERVAL - diff) / SCAN_INTERVAL) * 100);

  const candidates  = hotData?.candidates ?? [];
  const trades      = tradesData?.trades ?? [];
  const longTrades  = trades.filter((t) => t.direction === "LONG");
  const shortTrades = trades.filter((t) => t.direction === "SHORT");

  const starting = walletData?.starting ?? 200;
  const realized = walletData?.total_pnl ?? 0;
  const history  = walletData?.equity_history ?? [];

  const MARGIN = 50;

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
  // Free cash = everything not locked in open trades (computed, never from stale file balance)
  const freeCash = Math.max(0, starting - (trades.length * MARGIN) + realized + Math.min(0, floating));
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
          <div className="flex items-center gap-3">
            <a
              href={`${BOT_API}/api/audit`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 bg-blue-500/10 border border-blue-500/30 rounded-full px-3 py-1.5 hover:bg-blue-500/20 transition-colors"
              title="הורד דוח Audit"
            >
              <span className="text-base">📥</span>
              <span className="text-blue-400 text-sm font-medium">דוח Audit</span>
            </a>
            <div className="flex items-center gap-2 bg-green-500/10 border border-green-500/30 rounded-full px-3 py-1.5">
              <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
              <span className="text-green-400 text-sm font-medium">פעיל</span>
            </div>
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
              <p className="text-xl font-bold text-blue-400">${freeCash.toFixed(2)}</p>
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
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center flex flex-col items-center gap-2"
               style={{ background: 'linear-gradient(135deg, #0a1628 0%, #0d1f3c 100%)' }}>
            <SevenSegDisplay value={`${mm}:${ss}`} />
            <p className="text-gray-500 text-xs tracking-wide">עד סריקה הבאה</p>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 text-center">
            <p className="text-3xl font-bold text-violet-400">90+</p>
            <p className="text-gray-400 text-sm mt-1">סף כניסה</p>
          </div>
        </div>

        {/* Fear & Greed Gauge */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4"
             style={{ background: 'linear-gradient(135deg, #0d1117 0%, #0f1b2d 100%)' }}>
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <span className="text-base">📊</span>
              <h2 className="font-semibold text-gray-300 text-sm">מדד הפחד והחמדנות</h2>
            </div>
            <span className="text-xs text-gray-600">מתעדכן כל שעה · Alternative.me</span>
          </div>
          <FearGreedGauge value={fngData?.value ?? null} label={fngData?.label ?? null} />
          {fngData && (
            <p className="text-center text-xs mt-1" style={{ color: fngColor(fngData.value) }}>
              {fngData.value < 25 || fngData.value > 75
                ? `השפעה על ניקוד: ${fngData.value < 25 ? 'LONG +5 / SHORT -5' : 'SHORT +5 / LONG -5'}`
                : fngData.value < 45 || fngData.value > 55
                ? `השפעה על ניקוד: ${fngData.value < 45 ? 'LONG +2 / SHORT -2' : 'SHORT +2 / LONG -2'}`
                : 'השפעה על ניקוד: נייטרלי (0)'}
            </p>
          )}
        </div>

        {/* Last Scan Status */}
        <LastScanStatus scan={scanData ?? null} />

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
