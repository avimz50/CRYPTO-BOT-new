import { useEffect, useState, useCallback, useRef } from "react";
import {
  PieChart, Pie, Cell, ResponsiveContainer, Tooltip,
  AreaChart, Area, ReferenceLine, YAxis, ComposedChart,
} from "recharts";

/* ══════════════════════════════════════════════
   TERMINAL COLOUR PALETTE
══════════════════════════════════════════════ */
const T = {
  bg:     '#0d1117',
  panel:  '#161b22',
  border: '#30363d',
  green:  '#39ff14',
  red:    '#ff4444',
  amber:  '#f5a623',
  blue:   '#58a6ff',
  purple: '#bc8cff',
  cyan:   '#79c0ff',
  text:   '#c9d1d9',
  dim:    '#8b949e',
  dimmer: '#484f58',
  font:   "'JetBrains Mono', 'Fira Code', monospace",
} as const;

const panel: React.CSSProperties = {
  background: T.panel,
  border: `1px solid ${T.border}`,
  borderRadius: 6,
  fontFamily: T.font,
};

/* ══════════════════════════════════════════════
   7-SEGMENT DISPLAY (scan countdown)
══════════════════════════════════════════════ */
const SEG_MAP: Record<string, boolean[]> = {
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
const ON  = '#facc15';
const DIM_SEG = '#1c1600';
const GLW = '0 0 8px #facc15cc';

function SevenSegDigit({ digit }: { digit: string }) {
  const s = SEG_MAP[digit] ?? Array(7).fill(false);
  const W = 13, H = 22, Th = 2, G = 1, R = 1, H2 = H / 2;
  const seg = (on: boolean, x: number, y: number, w: number, h: number) => (
    <rect x={x} y={y} width={w} height={h} rx={R} ry={R}
      fill={on ? ON : DIM_SEG}
      style={on ? { filter: `drop-shadow(${GLW})` } : undefined} />
  );
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} style={{ display: 'block' }}>
      {seg(s[0], Th+G, 0,       W-2*Th-2*G, Th)}
      {seg(s[1], W-Th, Th+G,    Th, H2-Th-2*G)}
      {seg(s[2], W-Th, H2+G,    Th, H2-Th-2*G)}
      {seg(s[3], Th+G, H-Th,    W-2*Th-2*G, Th)}
      {seg(s[4], 0,    H2+G,    Th, H2-Th-2*G)}
      {seg(s[5], 0,    Th+G,    Th, H2-Th-2*G)}
      {seg(s[6], Th+G, H2-Th/2, W-2*Th-2*G, Th)}
    </svg>
  );
}
function SevenSegColon() {
  return (
    <div style={{ width: 6, height: 22, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'space-evenly' }}>
      {[0,1].map(i => <div key={i} style={{ width:3, height:3, borderRadius:'50%', background: ON, boxShadow: GLW }} />)}
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
   INTERFACES
══════════════════════════════════════════════ */
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
  partial_25_triggered?: boolean;
  pos_size?: number;
  score: number;
  atr: number;
  peak_price: number;
  trailing_sl: number | null;
  timeframe?: string;
  track?: string;
}
interface TradesData { updated: string; count: number; trades: Trade[]; }

interface EquityPoint { t: string; eq: number; }
interface WalletData {
  balance: number;
  starting: number;
  total_pnl: number;
  trades_opened: number;
  equity_history: EquityPoint[];
  available_balance?: number;
  locked_balance?: number;
  unrealized_pnl?: number;
  equity?: number;
  active_count?: number;
}

interface FngData { value: number; label: string; updated_at?: number; time_until_update?: number; }

/** Shape returned by /api/status — combined snapshot for the top status bar */
interface StatusData {
  connected: boolean; exchange: string; mode: string;
  equity: number; available: number; starting: number;
  unrealized: number; realized: number; locked: number;
  fng_value: number; fng_label: string;
  active_trades: number; ts: number;
}

interface Candidate { symbol: string; change_pct: number; volume_usd: number; price: number; }
interface HotData { updated: string; count: number; candidates: Candidate[]; }

interface RejectedCoin {
  symbol: string; direction: string; best_score: number; reason: string;
  scores?: { "4H"?: number; "1H"?: number; "15m"?: number };
}
interface BubbleCoin { symbol: string; change_pct: number; direction: string; price: number; volume_usd: number; }
interface ScanData {
  scan_time: string; total_scanned: number; signals_found: number;
  active_trades_count: number; btc_regime: string; fng_value: number;
  fng_label: string; market_sentiment_factor: string; rejected_coins: RejectedCoin[];
  system_message: string; scan_duration_s: number; bubble_watch?: BubbleCoin[];
  min_score?: number;
}

interface AuditTrade {
  symbol: string; direction: string; track: string; entry_price: number;
  close_price: number; pnl_usd: number; close_reason: string; score: number;
  fng_at_entry: number | null; rr_ratio: number | null; rr_achieved: number | null;
  duration_min: number; opened_at: string; closed_at: string; lesson: string;
  sniper: boolean; scalp: boolean; hunter_mode: boolean;
}
interface AuditData { updated: string | null; count: number; trades: AuditTrade[]; }

interface FngRange { min: number; max: number; desc: string; }
interface FngSettings {
  extreme_fear: number; fear: number; greed: number;
  ranges: { extreme_fear: FngRange; fear: FngRange; greed: FngRange; };
}
interface SlotsData { max_trades: number; active_trades: number; open_slots: number; min: number; max: number; }

/* ══════════════════════════════════════════════
   UTILITY HOOKS & FUNCTIONS
══════════════════════════════════════════════ */
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
  const [refreshKey, setRefreshKey] = useState(0);
  const refetch = useCallback(() => setRefreshKey(k => k + 1), []);
  useEffect(() => {
    const fetch_ = () =>
      fetch(primaryUrl + "?t=" + Date.now())
        .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
        .then(setData)
        .catch(() =>
          fetch(fallbackUrl + "?t=" + Date.now())
            .then(r => r.json()).then(setData).catch(() => {})
        );
    fetch_();
    const id = setInterval(fetch_, interval);
    return () => clearInterval(id);
  }, [primaryUrl, fallbackUrl, interval, refreshKey]);
  return { data, refetch };
}

interface PricePoint { ts: number; price: number; }

/** Fetches real close-price series for a symbol from /api/price_history/:symbol */
function usePriceHistory(botApi: string, symbol: string, interval = 15_000) {
  const [series, setSeries] = useState<PricePoint[]>([]);
  useEffect(() => {
    const sym = encodeURIComponent(symbol);
    const load = () =>
      fetch(`${botApi}/api/price_history/${sym}?t=${Date.now()}`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d?.series?.length) setSeries(d.series); })
        .catch(() => {});
    load();
    const id = setInterval(load, interval);
    return () => clearInterval(id);
  }, [botApi, symbol, interval]);
  return series;
}

/** Returns true for ~700ms whenever `value` changes — used to trigger a pulse animation */
function usePulse(value: number) {
  const [pulsing, setPulsing] = useState(false);
  const prev = useRef(value);
  useEffect(() => {
    if (Math.abs(value - prev.current) > 0.001) {
      prev.current = value;
      setPulsing(true);
      const t = setTimeout(() => setPulsing(false), 700);
      return () => clearTimeout(t);
    }
    prev.current = value;
    return undefined;
  }, [value]);
  return pulsing;
}

/** Animate a numeric value from its previous to its current value (count-up/down) */
function useCountUp(target: number, duration = 500) {
  const [display, setDisplay] = useState(target);
  const prevRef = useRef(target);
  const rafRef  = useRef<number>(0);
  useEffect(() => {
    const from  = prevRef.current;
    const delta = target - from;
    if (Math.abs(delta) < 0.001) { prevRef.current = target; return; }
    const start = performance.now();
    const step  = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const ease = 1 - Math.pow(1 - t, 3); // cubic ease-out
      setDisplay(from + delta * ease);
      if (t < 1) rafRef.current = requestAnimationFrame(step);
      else { setDisplay(target); prevRef.current = target; }
    };
    rafRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration]);
  return display;
}

function fmt(n: number) {
  if (n === undefined || n === null) return "—";
  if (Math.abs(n) < 0.001) return n.toExponential(3);
  if (Math.abs(n) < 1)     return n.toFixed(4);
  if (Math.abs(n) < 10000) return n.toFixed(2);
  return n.toLocaleString();
}
function formatVolume(v: number) {
  if (v >= 1_000_000_000) return `$${(v/1_000_000_000).toFixed(1)}B`;
  if (v >= 1_000_000)     return `$${(v/1_000_000).toFixed(1)}M`;
  return `$${v.toLocaleString()}`;
}
function fngColor(v: number) {
  if (v < 20) return '#ef4444';
  if (v < 40) return '#f97316';
  if (v < 60) return '#facc15';
  if (v < 80) return '#84cc16';
  return '#22c55e';
}

const BOT_API = import.meta.env.DEV ? "" : "https://python-script-bymzrkhy.replit.app";

/* ══════════════════════════════════════════════
   STATUS BAR (fixed top) — feeds from /api/status
══════════════════════════════════════════════ */
function StatusBar({
  status, clock,
}: {
  status: StatusData | null; clock: Date;
}) {
  const fngVal   = status?.fng_value ?? 50;
  const fngLbl   = status?.fng_label ?? 'Neutral';
  const fColor   = fngColor(fngVal);
  const equity   = status?.equity ?? 200;
  const starting = status?.starting ?? 200;
  const pnlPct   = starting > 0 ? ((equity - starting) / starting * 100) : 0;
  const eqColor  = equity >= starting ? T.green : T.red;

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
      height: 44, background: T.panel, borderBottom: `1px solid ${T.border}`,
      display: 'flex', alignItems: 'center',
      padding: '0 16px', gap: 0,
      fontFamily: T.font, fontSize: 12, color: T.dim,
    }}>
      {/* Logo */}
      <span style={{ color: T.green, fontWeight: 700, fontSize: 13, letterSpacing: 1, marginRight: 16 }}>
        BotOS v2
      </span>
      <Divider />

      {/* Connection — hidden on mobile */}
      <div className="sb-hide-mobile" style={{ display: 'flex' }}>
        <StatusChip color={T.green} label="CONNECTED" sub="Bitget VIRTUAL" />
        <Divider />
      </div>

      {/* FNG */}
      <div className="sb-item" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0 12px' }}>
        <span style={{ color: T.dimmer }}>FNG</span>
        <span style={{ color: fColor, fontWeight: 700 }}>{fngVal}</span>
        <span style={{ color: fColor, fontSize: 11 }}>{fngLbl}</span>
      </div>
      <Divider />

      {/* Portfolio — hidden on mobile */}
      <div className="sb-hide-mobile" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0 12px' }}>
        <span style={{ color: T.dimmer }}>EQUITY</span>
        <span style={{ color: eqColor, fontWeight: 700 }}>${equity.toFixed(2)}</span>
        <span style={{ color: eqColor, fontSize: 11 }}>
          {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
        </span>
      </div>
      <div className="sb-hide-mobile" style={{ display: 'flex' }}><Divider /></div>

      {/* Active trades — hidden on mobile */}
      <div className="sb-hide-mobile" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0 12px' }}>
        <span style={{ color: T.dimmer }}>TRADES</span>
        <span style={{ color: (status?.active_trades ?? 0) > 0 ? T.amber : T.dimmer, fontWeight: 700 }}>
          {status?.active_trades ?? 0}/3
        </span>
      </div>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Clock */}
      <span style={{ color: T.dimmer, fontSize: 11 }}>
        {clock.toLocaleTimeString('he-IL')}
      </span>
    </div>
  );
}

function Divider() {
  return <div style={{ width: 1, height: 20, background: T.border, flexShrink: 0 }} />;
}

function StatusChip({ color, label, sub }: { color: string; label: string; sub: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0 12px' }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: color,
        boxShadow: `0 0 6px ${color}`, display: 'inline-block', flexShrink: 0 }} />
      <span style={{ color, fontWeight: 700 }}>{label}</span>
      <span style={{ color: T.dimmer, fontSize: 11 }}>{sub}</span>
    </div>
  );
}

/* ══════════════════════════════════════════════
   PANEL HEADER
══════════════════════════════════════════════ */
function PanelHeader({ label, right }: { label: string; right?: React.ReactNode }) {
  return (
    <div style={{
      padding: '7px 12px', borderBottom: `1px solid ${T.border}`,
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    }}>
      <span style={{ color: T.dimmer, fontSize: 10, letterSpacing: 2, textTransform: 'uppercase', fontWeight: 600 }}>
        {label}
      </span>
      {right && <span style={{ color: T.dim, fontSize: 10 }}>{right}</span>}
    </div>
  );
}

/* ══════════════════════════════════════════════
   ANALYTICS SIDEBAR
══════════════════════════════════════════════ */
function AnalyticsSidebar({
  wallet, trades, fng, scanCountdown, scanPct,
}: {
  wallet: WalletData | null; trades: Trade[]; fng: FngData | null;
  scanCountdown: string; scanPct: number;
}) {
  const starting   = wallet?.starting ?? 200;
  const freeCash   = wallet?.available_balance ?? Math.max(0, starting - trades.length * 50 + (wallet?.total_pnl ?? 0));
  const lockedBal  = wallet?.locked_balance ?? trades.length * 50;
  const equity     = wallet?.equity ?? starting;
  const total      = Math.max(freeCash + lockedBal, 0.01);

  const COLORS = ['#39ff14', '#f5a623', '#58a6ff', '#bc8cff', '#79c0ff', '#ff4444'];

  // Pie: USDT (available) + per-symbol position share — matches "USDT%/BTC%/active-symbol%" spec
  const pieData = [
    { name: 'USDT', value: Math.max(freeCash, 0) },
    ...trades.map(t => ({ name: t.symbol.replace('/USDT', ''), value: t.pos_size ?? 50 })),
  ].filter(d => d.value > 0);

  // Volatility from average ATR %
  const avgAtrPct = trades.length > 0
    ? trades.reduce((s, t) => s + (t.atr / t.entry * 100), 0) / trades.length
    : 0;
  const volLabel = avgAtrPct > 3 ? 'HIGH' : avgAtrPct > 1.5 ? 'MEDIUM' : 'LOW';
  const volColor = avgAtrPct > 3 ? T.red : avgAtrPct > 1.5 ? T.amber : T.green;

  const fngVal   = fng?.value ?? 50;
  const fColor   = fngColor(fngVal);
  const equityPct = starting > 0 ? ((equity - starting) / starting * 100) : 0;

  return (
    <div style={{
      width: 220, flexShrink: 0,
      display: 'flex', flexDirection: 'column', gap: 8,
      fontFamily: T.font,
    }}>
      {/* Portfolio Allocation */}
      <div style={panel}>
        <PanelHeader label="ANALYTICS" />
        <div style={{ padding: '12px 12px 4px' }}>
          <ResponsiveContainer width="100%" height={130}>
            <PieChart>
              <Pie data={pieData} cx="50%" cy="50%" innerRadius={38} outerRadius={58}
                dataKey="value" strokeWidth={0}>
                {pieData.map((_e, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ background: T.panel, border: `1px solid ${T.border}`,
                  borderRadius: 4, fontFamily: T.font, fontSize: 11 }}
                formatter={(v: number) => [`$${v.toFixed(0)}`, '']}
              />
            </PieChart>
          </ResponsiveContainer>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
            {pieData.map((d, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2,
                    background: COLORS[i % COLORS.length], display: 'inline-block' }} />
                  <span style={{ color: T.dim, fontSize: 10 }}>{d.name}</span>
                </div>
                <span style={{ color: T.text, fontSize: 10, fontWeight: 600 }}>
                  ${d.value.toFixed(0)}
                  <span style={{ color: T.dimmer }}> ({((d.value / total) * 100).toFixed(0)}%)</span>
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Divider */}
        <div style={{ margin: '10px 12px', height: 1, background: T.border }} />

        {/* Volatility */}
        <div style={{ padding: '0 12px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ color: T.dimmer, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase' }}>VOLATILITY</span>
            <span style={{ color: volColor, fontSize: 12, fontWeight: 700,
              textShadow: `0 0 8px ${volColor}66` }}>
              {volLabel}
            </span>
          </div>
          {avgAtrPct > 0 && (
            <div style={{ background: T.bg, borderRadius: 4, height: 4, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${Math.min(avgAtrPct / 5 * 100, 100)}%`,
                background: volColor, borderRadius: 4, transition: 'width 0.5s' }} />
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ color: T.dimmer, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase' }}>EQUITY</span>
            <span style={{ color: equityPct >= 0 ? T.green : T.red, fontSize: 12, fontWeight: 700 }}>
              {equityPct >= 0 ? '+' : ''}{equityPct.toFixed(2)}%
            </span>
          </div>
        </div>
      </div>

      {/* Fear & Greed */}
      <div style={panel}>
        <PanelHeader label="FEAR & GREED" />
        <div style={{ padding: '12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
            <span style={{ color: fColor, fontSize: 36, fontWeight: 700, lineHeight: 1,
              textShadow: `0 0 20px ${fColor}55` }}>
              {fngVal}
            </span>
            <div>
              <div style={{ color: fColor, fontSize: 11, fontWeight: 600 }}>{fng?.label ?? 'Neutral'}</div>
              <div style={{ color: T.dimmer, fontSize: 10 }}>Fear & Greed</div>
            </div>
          </div>
          {/* FNG bar */}
          <div style={{ height: 6, background: T.bg, borderRadius: 3, overflow: 'hidden' }}>
            <div style={{
              height: '100%', width: `${fngVal}%`, borderRadius: 3,
              background: `linear-gradient(to right, #ef4444, #f97316, #facc15, #84cc16, #22c55e)`,
              transition: 'width 1s ease',
            }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 9, color: '#ef4444' }}>0 Extreme Fear</span>
            <span style={{ fontSize: 9, color: '#22c55e' }}>100 Extreme Greed</span>
          </div>
        </div>
      </div>

      {/* Scan countdown */}
      <div style={panel}>
        <PanelHeader label="NEXT SCAN" />
        <div style={{ padding: '12px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
          <SevenSegDisplay value={scanCountdown} />
          <div style={{ width: '100%', height: 3, background: T.bg, borderRadius: 2, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${scanPct}%`, background: T.amber, borderRadius: 2, transition: 'width 1s' }} />
          </div>
          <span style={{ color: T.dimmer, fontSize: 10 }}>ELAPSED {scanPct}%</span>
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════
   STAT BOX (large monospace number)
══════════════════════════════════════════════ */
function StatBox({
  label, value, sub, color, glow = false,
}: { label: string; value: string; sub?: string; color: string; glow?: boolean }) {
  return (
    <div style={{ ...panel, padding: '14px 16px', flex: 1 }}>
      <div style={{ color: T.dimmer, fontSize: 9, letterSpacing: 2, textTransform: 'uppercase', marginBottom: 6 }}>
        {label}
      </div>
      <div style={{
        color, fontSize: 24, fontWeight: 700, lineHeight: 1, letterSpacing: -0.5,
        textShadow: glow ? `0 0 16px ${color}55` : undefined, fontFamily: T.font,
      }}>
        {value}
      </div>
      {sub && (
        <div style={{ color: T.dimmer, fontSize: 10, marginTop: 4 }}>{sub}</div>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════
   SL/TP PROGRESS BAR
══════════════════════════════════════════════ */
function SlTpBar({ trade }: { trade: Trade }) {
  const cp    = trade.current_price ?? trade.entry;
  const isLong = trade.direction === 'LONG';
  const lo    = Math.min(trade.sl, trade.tp, cp) * 0.999;
  const hi    = Math.max(trade.sl, trade.tp, cp) * 1.001;
  const range = hi - lo || 1;

  const pct = (val: number) => ((val - lo) / range) * 100;

  const entryPct = pct(trade.entry);
  const cpPct    = pct(cp);
  const slPct    = pct(trade.sl);
  const tpPct    = pct(trade.tp);
  const tp1Pct   = trade.tp1 ? pct(trade.tp1) : null;
  const bePct    = trade.be_lvl ? pct(trade.be_lvl) : null;

  const rawPct = (cp - trade.entry) / trade.entry * 100;
  const pnlPct = isLong ? rawPct : -rawPct;
  const fillColor = pnlPct >= 0 ? T.green : T.red;

  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ position: 'relative', height: 16, background: T.bg, borderRadius: 3, overflow: 'visible' }}>
        {/* SL zone */}
        <div style={{
          position: 'absolute', left: `${Math.min(slPct, entryPct)}%`,
          width: `${Math.abs(entryPct - slPct)}%`, top: 0, bottom: 0,
          background: `${T.red}22`, borderRadius: 2,
        }} />
        {/* TP zone */}
        <div style={{
          position: 'absolute', left: `${Math.min(tpPct, entryPct)}%`,
          width: `${Math.abs(tpPct - entryPct)}%`, top: 0, bottom: 0,
          background: `${T.green}22`, borderRadius: 2,
        }} />
        {/* SL marker */}
        <div style={{
          position: 'absolute', left: `${slPct}%`, top: 0, bottom: 0, width: 2,
          background: T.red, transform: 'translateX(-50%)',
        }} />
        {/* TP marker */}
        <div style={{
          position: 'absolute', left: `${tpPct}%`, top: 0, bottom: 0, width: 2,
          background: T.green, transform: 'translateX(-50%)',
        }} />
        {/* TP1 marker */}
        {tp1Pct !== null && (
          <div style={{
            position: 'absolute', left: `${tp1Pct}%`, top: 2, bottom: 2, width: 1,
            background: `${T.green}88`, transform: 'translateX(-50%)',
          }} />
        )}
        {/* BE marker */}
        {bePct !== null && trade.be_triggered && (
          <div style={{
            position: 'absolute', left: `${bePct}%`, top: 0, bottom: 0, width: 1,
            background: T.blue, transform: 'translateX(-50%)', opacity: 0.7,
          }} />
        )}
        {/* Entry marker */}
        <div style={{
          position: 'absolute', left: `${entryPct}%`, top: 0, bottom: 0, width: 1.5,
          background: T.amber, transform: 'translateX(-50%)',
        }} />
        {/* Current price cursor */}
        <div style={{
          position: 'absolute', left: `${Math.max(0, Math.min(cpPct, 100))}%`,
          top: -2, bottom: -2, width: 3,
          background: fillColor, borderRadius: 2, transform: 'translateX(-50%)',
          boxShadow: `0 0 6px ${fillColor}`,
          transition: 'left 0.8s ease',
        }} />
      </div>
      {/* Labels */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 3, fontSize: 9, color: T.dimmer }}>
        <span style={{ color: T.red }}>SL {fmt(trade.sl)}</span>
        <span style={{ color: T.amber }}>ENTRY {fmt(trade.entry)}</span>
        <span style={{ color: T.green }}>TP {fmt(trade.tp)}</span>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════
   MINI PRICE CHART — recharts AreaChart
   Uses real accumulated close-price series from /api/price_history/:symbol.
   Falls back to a 2-point line (entry → current) when no history yet.
══════════════════════════════════════════════ */
function MiniPriceChart({ trade }: { trade: Trade }) {
  const cp     = trade.current_price ?? trade.entry;
  const isLong = trade.direction === 'LONG';
  const pnlPct = isLong ? ((cp - trade.entry) / trade.entry * 100) : ((trade.entry - cp) / trade.entry * 100);
  const color  = pnlPct >= 0 ? T.green : T.red;

  // Accumulated real close-price series from API
  const history = usePriceHistory(BOT_API, trade.symbol, 15_000);

  // Build chart data: use real series if available, else fallback 2-point line
  const chartData: { price: number }[] = history.length >= 2
    ? history.map(p => ({ price: p.price }))
    : [{ price: trade.entry }, { price: cp }];

  const prices = chartData.map(d => d.price);
  const lo = Math.min(...prices, trade.sl)  * 0.998;
  const hi = Math.max(...prices, trade.tp)  * 1.002;

  const gradId = `mg-${trade.symbol.replace('/', '')}`;

  return (
    <div style={{ width: 120, height: 48, flexShrink: 0 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={chartData} margin={{ top: 2, right: 2, bottom: 2, left: 0 }}>
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor={color} stopOpacity={0.3} />
              <stop offset="100%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <YAxis domain={[lo, hi]} hide />
          {/* SL dashed reference */}
          <ReferenceLine y={trade.sl}    stroke={T.red}   strokeDasharray="2 2" strokeWidth={0.7} />
          {/* TP dashed reference */}
          <ReferenceLine y={trade.tp}    stroke={T.green} strokeDasharray="2 2" strokeWidth={0.7} />
          {/* Entry reference */}
          <ReferenceLine y={trade.entry} stroke={T.amber} strokeDasharray="3 2" strokeWidth={0.7} />
          <Area
            type="monotone"
            dataKey="price"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#${gradId})`}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ══════════════════════════════════════════════
   TERMINAL TRADE CARD
══════════════════════════════════════════════ */
function TerminalTradeCard({ trade }: { trade: Trade }) {
  const [hovered, setHovered] = useState(false);
  const isLong   = trade.direction === 'LONG';
  const cp       = trade.current_price ?? trade.entry;
  const rawPct   = (cp - trade.entry) / trade.entry * 100;
  const pnlPct   = isLong ? rawPct : -rawPct;
  const posSize  = trade.pos_size ?? 500;
  const pnlUsd   = trade.tp1_triggered
    ? (trade.tp1_pnl ?? 0) + posSize * pnlPct / 100
    : posSize * pnlPct / 100;
  const isProfit = pnlUsd >= 0;
  const pnlColor = isProfit ? T.green : T.red;
  const dirColor = isLong ? T.green : T.red;
  const tf       = trade.timeframe ?? '4H';
  const bgSymbol = trade.symbol.replace('/', '');

  const badges = [
    trade.be_triggered       && { label: 'BE',  color: T.blue  },
    trade.tp1_triggered      && { label: 'TP1', color: T.green },
    trade.partial_25_triggered && { label: '25%', color: T.amber },
    trade.phase === 'trailing' && { label: 'TRAIL', color: T.purple },
  ].filter(Boolean) as { label: string; color: string }[];

  const scoreColor = trade.score >= 95 ? T.green : trade.score >= 90 ? T.amber : T.red;

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        ...panel,
        borderColor: hovered ? dirColor + '55' : T.border,
        boxShadow: hovered ? `0 0 12px ${dirColor}18` : undefined,
        transition: 'border-color 0.2s, box-shadow 0.2s',
        fontFamily: T.font,
        overflow: 'hidden',
      }}
    >
      {/* Header row */}
      <div style={{
        padding: '8px 12px', borderBottom: `1px solid ${T.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: `${dirColor}08`,
      }}>
        <div className="card-badges" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ color: dirColor, fontWeight: 700, fontSize: 15 }}>
            {trade.symbol.replace('/USDT', '')}
          </span>
          <span style={{
            color: dirColor, fontSize: 10, fontWeight: 700,
            border: `1px solid ${dirColor}55`, borderRadius: 3, padding: '1px 5px',
          }}>
            {isLong ? '▲ LONG' : '▼ SHORT'}
          </span>
          <span style={{
            color: T.dimmer, fontSize: 10,
            background: T.bg, border: `1px solid ${T.border}`,
            borderRadius: 3, padding: '1px 4px',
          }}>{tf}</span>
          {/* Strategy label — derived from track field (Swing / Breakout / Scalp etc.) */}
          {trade.track && (
            <span style={{
              color: T.purple, fontSize: 9, fontWeight: 700,
              border: `1px solid ${T.purple}55`, borderRadius: 3, padding: '1px 4px',
            }}>{trade.track.toUpperCase()}</span>
          )}
          {badges.map(b => (
            <span key={b.label} style={{
              color: b.color, fontSize: 9, fontWeight: 700,
              border: `1px solid ${b.color}55`, borderRadius: 3, padding: '1px 4px',
            }}>{b.label}</span>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: scoreColor, fontSize: 10, fontWeight: 700 }}>
            {trade.score}/100
          </span>
          <a
            href={`https://www.bitget.com/futures/usdt/${bgSymbol}`}
            target="_blank" rel="noopener noreferrer"
            style={{ color: T.blue, fontSize: 10, textDecoration: 'none' }}
          >↗ CHART</a>
        </div>
      </div>

      {/* Main content: stats + mini chart */}
      <div style={{ display: 'flex', padding: '10px 12px', gap: 12 }}>
        {/* Stats column */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {/* Current price */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <span style={{ color: T.dimmer, fontSize: 10 }}>PRICE</span>
            <span style={{ color: isLong ? (cp > trade.entry ? T.green : T.red) : (cp < trade.entry ? T.green : T.red), fontSize: 13, fontWeight: 700 }}>
              {fmt(cp)}
              <span style={{ color: T.dimmer, fontSize: 9, marginLeft: 4 }}>
                {rawPct >= 0 ? '+' : ''}{rawPct.toFixed(2)}%
              </span>
            </span>
          </div>
          <DataRow label="ENTRY" value={fmt(trade.entry)} color={T.amber} />
          <DataRow label="SL" value={fmt(trade.sl)} color={T.red} />
          <DataRow label="TP" value={fmt(trade.tp)} color={`${T.green}cc`} />
          {trade.trailing_sl !== null && (
            <DataRow label="TRAIL SL" value={fmt(trade.trailing_sl!)} color={T.purple} />
          )}
          <DataRow label="ATR" value={fmt(trade.atr)} color={T.dimmer} />
          {/* P&L — count-up animation on change */}
          <PnlDisplay pnlUsd={pnlUsd} pnlPct={pnlPct} pnlColor={pnlColor} isProfit={isProfit} />
        </div>

        {/* Mini chart */}
        <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          <MiniPriceChart trade={trade} />
        </div>
      </div>

      {/* SL/TP progress bar */}
      <div style={{ padding: '0 12px 10px' }}>
        <SlTpBar trade={trade} />
      </div>
    </div>
  );
}

function DataRow({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
      <span style={{ color: T.dimmer, fontSize: 10 }}>{label}</span>
      <span style={{ color, fontSize: 11, fontWeight: 600 }}>{value}</span>
    </div>
  );
}

/** P&L row with count-up animation and pulse/glow whenever pnlUsd changes */
function PnlDisplay({
  pnlUsd, pnlPct, pnlColor, isProfit,
}: { pnlUsd: number; pnlPct: number; pnlColor: string; isProfit: boolean }) {
  const animUsd = useCountUp(pnlUsd, 450);
  const animPct = useCountUp(pnlPct, 450);
  const pulsing = usePulse(pnlUsd);
  return (
    <div style={{
      marginTop: 4, display: 'flex', justifyContent: 'space-between',
      alignItems: 'baseline', borderTop: `1px solid ${T.border}`, paddingTop: 6,
    }}>
      <span style={{ color: T.dimmer, fontSize: 10 }}>P&L</span>
      <span
        className={pulsing ? 'pnl-pulse' : undefined}
        style={{
          color: pnlColor, fontSize: 14, fontWeight: 700,
          textShadow: isProfit ? `0 0 8px ${T.green}55` : undefined,
          borderRadius: 4, padding: '1px 4px',
          transition: 'box-shadow 0.15s ease',
          ['--pulse-color' as string]: pnlColor,
        }}
      >
        {animUsd >= 0 ? '+' : ''}{animUsd.toFixed(2)}$
        <span style={{ fontSize: 10, marginLeft: 4 }}>
          ({animPct >= 0 ? '+' : ''}{animPct.toFixed(2)}%)
        </span>
      </span>
    </div>
  );
}

/* ══════════════════════════════════════════════
   REASONING LOG (terminal feed)
   Format: HH:MM:SS | SYMBOL | message
   Sources: trade_audit (closed trades) + last_scan (near-misses, scan events)
   Mobile: 8 lines; Desktop: 15 lines
══════════════════════════════════════════════ */
interface LogEntry { ts: number; tsStr: string; sym: string; message: string; color: string; }

function ReasoningLog({
  audit, scan, isMobile,
}: { audit: AuditData | null; scan: ScanData | null; isMobile: boolean }) {
  const logRef = useRef<HTMLDivElement>(null);

  // Build merged entries from both sources
  const entries: LogEntry[] = [];

  // 1. Closed-trade audit entries
  for (const t of (audit?.trades ?? [])) {
    const ts = t.closed_at ? new Date(t.closed_at).getTime() : 0;
    const tsStr = t.closed_at
      ? new Date(t.closed_at).toLocaleTimeString('he-IL',
          { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      : '--:--:--';
    const sym = t.symbol.replace('/USDT', '');
    const msg = [
      t.track && t.track !== sym ? t.track : null,
      `${t.close_reason} ${t.pnl_usd >= 0 ? '+' : ''}${t.pnl_usd.toFixed(2)}$`,
      t.lesson,
    ].filter(Boolean).join(' · ');
    const color =
      t.close_reason === 'TP' ? T.green :
      t.close_reason === 'SL' ? T.red :
      t.close_reason === 'BE' ? T.amber : T.blue;
    entries.push({ ts, tsStr, sym, message: msg, color });
  }

  // 2. Scan near-miss entries from last_scan.rejected_coins
  if (scan?.scan_time && scan.rejected_coins?.length) {
    const scanTs  = new Date(scan.scan_time).getTime();
    const tsStr   = new Date(scan.scan_time).toLocaleTimeString('he-IL',
        { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    for (const r of scan.rejected_coins.slice(0, 5)) {
      const sym = r.symbol.replace('/USDT', '');
      const msg = `[SCAN] score=${r.best_score} · ${r.reason ?? 'near-miss'}`;
      entries.push({ ts: scanTs, tsStr, sym, message: msg, color: T.dimmer });
    }
    // Also add the scan summary line
    entries.push({
      ts: scanTs, tsStr, sym: 'BOT',
      message: `scan done: ${scan.signals_found} signals · BTC ${scan.btc_regime} · FNG ${scan.fng_value}`,
      color: T.cyan,
    });
  }

  // Sort ascending (oldest→newest) so newest entry is at the bottom; auto-scroll there
  entries.sort((a, b) => a.ts - b.ts);
  const visible = entries.slice(-20); // last 20 = newest 20, oldest at top

  const totalCount = entries.length;
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; // scroll to newest
  }, [totalCount]);

  const LINE_H   = 18;
  const logHeight = isMobile ? LINE_H * 8 : LINE_H * 15;

  return (
    <div style={panel}>
      <PanelHeader
        label="BOT REASONING LOG"
        right={`${totalCount} entries · newest first`}
      />
      <div
        ref={logRef}
        style={{
          height: logHeight, overflowY: 'auto', padding: '6px 12px',
          background: '#0a0e14', fontFamily: T.font, fontSize: 11,
          display: 'flex', flexDirection: 'column', gap: 0,
        }}
      >
        {visible.length === 0 ? (
          <span style={{ color: T.green, lineHeight: `${LINE_H}px` }}>
            {'> '}<span style={{ color: T.dimmer }}>waiting for trade or scan data...</span>
          </span>
        ) : (
          visible.map((e, i) => (
            <div key={i} style={{ lineHeight: `${LINE_H}px`, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              <span style={{ color: T.dimmer }}>{e.tsStr}</span>
              <span style={{ color: T.border }}> | </span>
              <span style={{ color: T.amber, fontWeight: 600 }}>{e.sym}</span>
              <span style={{ color: T.border }}> | </span>
              <span style={{ color: e.color }}>{e.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════
   FNG SETTINGS PANEL (terminal-restyled)
══════════════════════════════════════════════ */
function FngSettingsPanel({ botApi }: { botApi: string }) {
  const [settings, setSettings] = useState<FngSettings | null>(null);
  const [draft, setDraft]       = useState<{ extreme_fear: number; fear: number; greed: number } | null>(null);
  const [open, setOpen]         = useState(false);
  const [saving, setSaving]     = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  const load = useCallback(() => {
    fetch(`${botApi}/api/fng_settings`)
      .then(r => r.json()).then((d: FngSettings) => {
        setSettings(d);
        setDraft({ extreme_fear: d.extreme_fear, fear: d.fear, greed: d.greed });
      }).catch(() => {});
  }, [botApi]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!draft) return;
    setSaving(true); setFeedback(null);
    try {
      const r = await fetch(`${botApi}/api/fng_settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft),
      });
      const d = await r.json();
      if (r.ok && d.ok) {
        setSettings(s => s ? { ...s, ...draft } : s);
        setFeedback({ ok: true, msg: 'SAVED ✓' });
        setTimeout(() => setFeedback(null), 3000);
      } else {
        setFeedback({ ok: false, msg: (d.errors ?? [d.error ?? 'ERROR']).join(' | ') });
      }
    } catch { setFeedback({ ok: false, msg: 'NETWORK ERROR' }); }
    setSaving(false);
  };

  type DraftKey = 'extreme_fear' | 'fear' | 'greed';
  const rows: Array<{ key: DraftKey; label: string; color: string }> = [
    { key: 'extreme_fear', label: 'KILL-SWITCH',  color: T.red  },
    { key: 'fear',         label: 'FEAR LEVEL',   color: T.amber },
    { key: 'greed',        label: 'GREED LEVEL',  color: T.green },
  ];

  if (!settings || !draft) return null;
  const changed = draft.extreme_fear !== settings.extreme_fear
               || draft.fear !== settings.fear
               || draft.greed !== settings.greed;

  return (
    <div style={panel}>
      <button onClick={() => setOpen(o => !o)} style={{
        width: '100%', background: 'none', border: 'none', cursor: 'pointer',
        padding: '8px 12px', display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', fontFamily: T.font,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: T.dimmer, fontSize: 10, letterSpacing: 2, textTransform: 'uppercase', fontWeight: 600 }}>
            FNG THRESHOLDS
          </span>
          <span style={{ color: T.dimmer, fontSize: 10 }}>
            KS&lt;{settings.extreme_fear} · F≤{settings.fear} · G≥{settings.greed}
          </span>
        </div>
        <span style={{ color: T.dimmer, fontSize: 10 }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div style={{ padding: '0 12px 12px', borderTop: `1px solid ${T.border}` }}>
          {rows.map(({ key, label, color }) => {
            const range = settings.ranges[key];
            const val   = draft[key];
            return (
              <div key={key} style={{ marginTop: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ color, fontSize: 10, fontWeight: 700 }}>{label}</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <input type="number" min={range.min} max={range.max} value={val}
                      onChange={e => setDraft(d => d ? { ...d, [key]: Number(e.target.value) } : d)}
                      style={{
                        width: 52, textAlign: 'center', background: T.bg,
                        border: `1px solid ${T.border}`, borderRadius: 3,
                        color: T.text, fontSize: 11, padding: '2px 4px', fontFamily: T.font,
                      }} />
                    <span style={{ color: T.dimmer, fontSize: 9 }}>({range.min}–{range.max})</span>
                  </div>
                </div>
                <input type="range" min={range.min} max={range.max} value={val}
                  onChange={e => setDraft(d => d ? { ...d, [key]: Number(e.target.value) } : d)}
                  style={{ width: '100%', accentColor: color, cursor: 'pointer', height: 3 }} />
              </div>
            );
          })}
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button onClick={save} disabled={!changed || saving} style={{
              flex: 1, padding: '6px 0', borderRadius: 4, fontFamily: T.font, fontSize: 11, fontWeight: 700,
              background: changed ? T.blue + '22' : T.bg,
              border: `1px solid ${changed ? T.blue : T.border}`,
              color: changed ? T.blue : T.dimmer, cursor: changed ? 'pointer' : 'not-allowed',
            }}>{saving ? 'SAVING...' : 'SAVE'}</button>
            <button onClick={() => setDraft({ extreme_fear: settings.extreme_fear, fear: settings.fear, greed: settings.greed })}
              disabled={!changed} style={{
                padding: '6px 12px', borderRadius: 4, fontFamily: T.font, fontSize: 11,
                background: 'none', border: `1px solid ${T.border}`, color: T.dimmer,
                cursor: changed ? 'pointer' : 'not-allowed',
              }}>RESET</button>
          </div>
          {feedback && (
            <div style={{ marginTop: 8, textAlign: 'center', color: feedback.ok ? T.green : T.red, fontSize: 11 }}>
              {feedback.msg}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════
   SLOTS PANEL (terminal-restyled)
══════════════════════════════════════════════ */
function SlotsPanel({ botApi }: { botApi: string }) {
  const [data,     setData]     = useState<SlotsData | null>(null);
  const [pending,  setPending]  = useState<number | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  const load = useCallback(() => {
    fetch(`${botApi}/api/slots`).then(r => r.json()).then((d: SlotsData) => setData(d)).catch(() => {});
  }, [botApi]);
  useEffect(() => { load(); const id = setInterval(load, 15_000); return () => clearInterval(id); }, [load]);

  const setSlots = async (v: number) => {
    setPending(v); setFeedback(null);
    try {
      const r  = await fetch(`${botApi}/api/slots`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_trades: v }),
      });
      const d = await r.json();
      if (r.ok && d.ok) {
        setData(d as SlotsData);
        setFeedback({ ok: true, msg: `SLOTS → ${v}` });
        setTimeout(() => setFeedback(null), 3000);
      } else { setFeedback({ ok: false, msg: d.error ?? 'ERROR' }); }
    } catch { setFeedback({ ok: false, msg: 'NETWORK ERROR' }); }
    setPending(null);
  };

  const current = data?.max_trades ?? 3;
  const nOpen   = data?.active_trades ?? 0;

  return (
    <div style={panel}>
      <div style={{
        padding: '8px 12px', borderBottom: `1px solid ${T.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: T.dimmer, fontSize: 10, letterSpacing: 2, textTransform: 'uppercase', fontWeight: 600 }}>
            TRADE SLOTS
          </span>
          <span style={{ color: T.dimmer, fontSize: 10 }}>
            {nOpen}/{current} ACTIVE · {Math.max(0, current - nOpen)} FREE
          </span>
        </div>
        {/* Slot buttons */}
        <div style={{ display: 'flex', gap: 4 }}>
          {[1,2,3,4,5].map(v => {
            const isActive = v === current;
            const isWarn   = v < nOpen;
            return (
              <button key={v} onClick={() => setSlots(v)}
                disabled={pending !== null || isActive}
                style={{
                  width: 30, height: 26, borderRadius: 4, fontFamily: T.font,
                  fontSize: 12, fontWeight: isActive ? 700 : 500,
                  border: `1px solid ${isActive ? T.green : isWarn ? T.amber : T.border}`,
                  background: isActive ? `${T.green}18` : 'none',
                  color: isActive ? T.green : isWarn ? T.amber : T.dimmer,
                  cursor: isActive || pending !== null ? 'not-allowed' : 'pointer',
                  opacity: pending !== null && pending !== v ? 0.5 : 1,
                }}
              >{pending === v ? '…' : v}</button>
            );
          })}
        </div>
      </div>
      {/* Slot bar */}
      <div style={{ padding: '8px 12px', display: 'flex', gap: 4 }}>
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} style={{
            flex: 1, height: 4, borderRadius: 2,
            background: i < nOpen ? T.green : i < current ? `${T.green}22` : 'none',
            border: `1px solid ${i < current ? T.border : 'transparent'}`,
            transition: 'background 0.3s',
          }} />
        ))}
      </div>
      {feedback && (
        <div style={{ padding: '0 12px 8px', color: feedback.ok ? T.green : T.red, fontSize: 10, textAlign: 'center' }}>
          {feedback.msg}
        </div>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════
   LAST SCAN (compact terminal panel)
══════════════════════════════════════════════ */
function LastScanPanel({ scan }: { scan: ScanData | null }) {
  const [open, setOpen] = useState(false);
  if (!scan) return (
    <div style={{ ...panel, padding: '8px 12px', color: T.dimmer, fontSize: 11 }}>
      — waiting for first scan...
    </div>
  );
  const scanTime = scan.scan_time ? new Date(scan.scan_time).toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' }) : '—';
  const regColor = scan.btc_regime === 'BULL' ? T.green : scan.btc_regime === 'BEAR' ? T.red : T.amber;

  return (
    <div style={panel}>
      <button onClick={() => setOpen(o => !o)} style={{
        width: '100%', background: 'none', border: 'none', cursor: 'pointer',
        padding: '8px 12px', display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', fontFamily: T.font,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ color: T.dimmer, fontSize: 10, letterSpacing: 2, textTransform: 'uppercase', fontWeight: 600 }}>
            LAST SCAN
          </span>
          <span style={{ color: T.dim, fontSize: 10 }}>{scanTime}</span>
          <span style={{ color: T.dim, fontSize: 10 }}>{scan.total_scanned} scanned</span>
          <span style={{ color: scan.signals_found > 0 ? T.green : T.dimmer, fontSize: 10, fontWeight: 700 }}>
            {scan.signals_found} signals
          </span>
          <span style={{ color: regColor, fontSize: 10 }}>BTC:{scan.btc_regime}</span>
        </div>
        <span style={{ color: T.dimmer, fontSize: 10 }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div style={{ padding: '0 12px 12px', borderTop: `1px solid ${T.border}` }}>
          {/* Sentiment */}
          <div style={{
            marginTop: 8, padding: '6px 10px', background: T.bg, borderRadius: 4,
            color: T.amber, fontSize: 10,
          }}>
            SENTIMENT: {scan.market_sentiment_factor}
          </div>
          {/* System msg */}
          <div style={{
            marginTop: 6, padding: '6px 10px', background: `${T.blue}11`,
            border: `1px solid ${T.blue}33`, borderRadius: 4,
            color: T.blue, fontSize: 10,
          }}>
            {scan.system_message}
          </div>
          {/* Near-misses */}
          {scan.rejected_coins && scan.rejected_coins.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ color: T.dimmer, fontSize: 9, letterSpacing: 2, marginBottom: 6, textTransform: 'uppercase' }}>
                NEAR-MISSES (score &lt; {scan.min_score ?? 90})
              </div>
              {scan.rejected_coins.slice(0, 5).map((c, i) => (
                <div key={i} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '4px 0', borderBottom: `1px solid ${T.border}33`,
                }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ color: T.dimmer, fontSize: 10 }}>{i+1}.</span>
                    <span style={{ color: T.text, fontSize: 11, fontWeight: 600 }}>{c.symbol.replace('/USDT', '')}</span>
                    <span style={{ color: c.direction === 'LONG' ? T.green : T.red, fontSize: 10 }}>{c.direction}</span>
                  </div>
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                    <span style={{ color: c.best_score >= 80 ? T.amber : T.dimmer, fontSize: 11, fontWeight: 700 }}>
                      {c.best_score}/100
                    </span>
                    <span style={{ color: T.dimmer, fontSize: 10, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {c.reason}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════
   HOT CANDIDATES (compact strip)
══════════════════════════════════════════════ */
function HotStrip({ candidates }: { candidates: Candidate[] }) {
  if (candidates.length === 0) return null;
  return (
    <div style={panel}>
      <PanelHeader label="HOT CANDIDATES" right={`Top ${candidates.length} Gainers`} />
      <div style={{ padding: '8px 12px', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {candidates.map((c, i) => {
          const color = c.change_pct >= 5 ? T.green : c.change_pct >= 0 ? `${T.green}aa` : T.red;
          return (
            <span key={c.symbol} style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              background: T.bg, border: `1px solid ${T.border}`,
              borderRadius: 4, padding: '3px 8px', fontSize: 10, fontFamily: T.font,
            }}>
              <span style={{ color: T.dimmer }}>{i+1}.</span>
              <span style={{ color: T.text, fontWeight: 600 }}>{c.symbol.replace('/USDT', '')}</span>
              <span style={{ color }}>{c.change_pct >= 0 ? '+' : ''}{c.change_pct.toFixed(1)}%</span>
              <span style={{ color: T.dimmer }}>{formatVolume(c.volume_usd)}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════
   EQUITY SPARKLINE (for sidebar / main area)
══════════════════════════════════════════════ */
function EquitySparkline({ history, starting }: { history: EquityPoint[]; starting: number }) {
  if (!history || history.length < 2) return null;
  const vals = history.map(p => p.eq);
  const min  = Math.min(...vals, starting * 0.95);
  const max  = Math.max(...vals, starting * 1.05);
  const W = 300, H = 60, pad = 6;
  const pts = history.map((p, i) => {
    const x = pad + (i / (history.length - 1)) * (W - pad * 2);
    const y = H - pad - ((p.eq - min) / (max - min || 1)) * (H - pad * 2);
    return `${x},${y}`;
  }).join(' ');
  const baseY = H - pad - ((starting - min) / (max - min || 1)) * (H - pad * 2);
  const last  = history[history.length - 1].eq;
  const color = last >= starting ? T.green : T.red;
  const lastX = pad + ((history.length - 1) / (history.length - 1)) * (W - pad * 2);
  const lastY = H - pad - ((last - min) / (max - min || 1)) * (H - pad * 2);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 60, display: 'block' }}>
      <defs>
        <linearGradient id="eq-g2" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.2" />
          <stop offset="100%" stopColor={color} stopOpacity="0.01" />
        </linearGradient>
      </defs>
      <line x1={pad} y1={baseY} x2={W-pad} y2={baseY}
        stroke={T.border} strokeWidth={1} strokeDasharray="3 2" />
      <polygon points={`${pad},${H-pad} ${pts} ${W-pad},${H-pad}`} fill="url(#eq-g2)" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={lastX} cy={lastY} r={3} fill={color} />
    </svg>
  );
}

/* ══════════════════════════════════════════════
   MAIN APP
══════════════════════════════════════════════ */
export default function App() {
  const clock = useClock();

  const { data: hotData,    refetch: refetchHot    } = useJson<HotData>(`${BOT_API}/api/hot`,             '/hot_candidates.json',     60_000);
  const { data: tradesData, refetch: refetchTrades  } = useJson<TradesData>(`${BOT_API}/api/active_trades`, '/active_trades.json',       4_000);
  const { data: walletData, refetch: refetchWallet  } = useJson<WalletData>(`${BOT_API}/api/wallet`,        '/wallet.json',             30_000);
  const { data: fngData,    refetch: refetchFng     } = useJson<FngData>(`${BOT_API}/api/fng`,              '/fng.json',               120_000);
  const { data: scanData,   refetch: refetchScan    } = useJson<ScanData>(`${BOT_API}/api/last_scan`,       '/last_scan_results.json', 120_000);
  const { data: auditData }                            = useJson<AuditData>(`${BOT_API}/api/trade_audit`,   '/trade_audit.json',       10_000);
  const { data: statusData, refetch: refetchStatus  } = useJson<StatusData>(`${BOT_API}/api/status`,        '',                        15_000);

  const [refreshing, setRefreshing] = useState(false);
  const refreshAll = useCallback(() => {
    setRefreshing(true);
    refetchHot(); refetchTrades(); refetchWallet(); refetchFng(); refetchScan(); refetchStatus();
    setTimeout(() => setRefreshing(false), 1200);
  }, [refetchHot, refetchTrades, refetchWallet, refetchFng, refetchScan, refetchStatus]);

  // Scan countdown
  const SCAN_INTERVAL = 3600;
  const lastScanTs = scanData?.scan_time ? new Date(scanData.scan_time).getTime() : null;
  const nextScanTs = lastScanTs
    ? lastScanTs + SCAN_INTERVAL * 1000
    : Math.ceil(clock.getTime() / (SCAN_INTERVAL * 1000)) * (SCAN_INTERVAL * 1000);
  const diff    = Math.max(0, Math.floor((nextScanTs - clock.getTime()) / 1000));
  const mm      = String(Math.floor(diff / 60)).padStart(2, '0');
  const ss      = String(diff % 60).padStart(2, '0');
  const scanPct = Math.round(((SCAN_INTERVAL - diff) / SCAN_INTERVAL) * 100);

  // Derived values
  const trades      = tradesData?.trades ?? [];
  const candidates  = hotData?.candidates ?? [];
  const starting    = walletData?.starting ?? 200;
  const MARGIN      = 50;

  const floatingAPI  = walletData?.unrealized_pnl;
  const floatingCalc = trades.reduce((sum, t) => {
    const cp   = t.current_price ?? t.entry;
    const raw  = (cp - t.entry) / t.entry * 100;
    const pct  = t.direction === 'LONG' ? raw : -raw;
    const tSz  = t.pos_size ?? 500;
    const usd  = t.tp1_triggered ? (t.tp1_pnl ?? 0) + tSz * pct / 100 : tSz * pct / 100;
    return sum + usd;
  }, 0);
  const floating  = floatingAPI ?? floatingCalc;
  const realized  = walletData?.total_pnl ?? 0;
  const equity    = walletData?.equity ?? (starting + realized + floating);
  const freeCash  = walletData?.available_balance
    ?? Math.max(0, starting - (trades.length * MARGIN) + realized + Math.min(0, floating));
  const lockedBal = walletData?.locked_balance ?? (trades.length * MARGIN);

  const floatPos = floating >= 0;
  const floatColor = floatPos ? T.green : T.red;

  // Responsive: detect mobile
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  useEffect(() => {
    const fn = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', fn);
    return () => window.removeEventListener('resize', fn);
  }, []);

  return (
    <div style={{ background: T.bg, minHeight: '100vh', fontFamily: T.font, color: T.text }}>
      <style>{`
        @keyframes pnl-pulse {
          0%   { box-shadow: 0 0 0px transparent; background: transparent; }
          25%  { box-shadow: 0 0 10px var(--pulse-color, #39ff14); background: color-mix(in srgb, var(--pulse-color, #39ff14) 18%, transparent); }
          100% { box-shadow: 0 0 0px transparent; background: transparent; }
        }
        .pnl-pulse { animation: pnl-pulse 0.7s ease-out forwards; }

        /* ── Mobile overrides ── */
        @media (max-width: 767px) {
          /* Status bar: hide equity + trades + connection sub-label on small screens */
          .sb-hide-mobile { display: none !important; }
          /* Status bar: tighten padding so items don't wrap */
          .sb-item { padding: 0 8px !important; }
          /* Trade card: allow badge row to wrap onto 2 lines */
          .card-badges { flex-wrap: wrap !important; row-gap: 4px !important; }
          /* Stat grid: third box spans full width */
          .stat-third { grid-column: 1 / -1 !important; }
          /* Footer: wrap buttons when narrow */
          .footer-actions { flex-wrap: wrap !important; }
          /* Last-scan near-miss reason: shorter max-width */
          .near-miss-reason { max-width: 120px !important; }
        }
      `}</style>
      {/* Fixed status bar */}
      <StatusBar
        status={statusData ?? null}
        clock={clock}
      />

      {/* Page body — below status bar */}
      <div style={{
        paddingTop: 44,
        display: isMobile ? 'block' : 'grid',
        gridTemplateColumns: isMobile ? undefined : '220px 1fr',
        gap: 8,
        padding: isMobile ? '52px 8px 24px' : '52px 12px 24px',
        maxWidth: 1400,
        margin: '0 auto',
      }}>

        {/* ── SIDEBAR ── */}
        {isMobile ? (
          /* Mobile: compact top strip */
          <div style={{ display: 'flex', gap: 8, marginBottom: 8, overflow: 'auto', paddingBottom: 4 }}>
            <div style={{ ...panel, padding: '8px 12px', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ color: T.dimmer, fontSize: 10 }}>FNG</span>
              <span style={{ color: fngColor(fngData?.value ?? 50), fontWeight: 700 }}>{fngData?.value ?? 50}</span>
              <span style={{ color: T.dimmer, fontSize: 10 }}>|</span>
              <span style={{ color: T.dimmer, fontSize: 10 }}>NEXT SCAN</span>
              <SevenSegDisplay value={`${mm}:${ss}`} />
            </div>
          </div>
        ) : (
          <div style={{ position: 'sticky', top: 52, maxHeight: 'calc(100vh - 60px)', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <AnalyticsSidebar
              wallet={walletData ?? null}
              trades={trades}
              fng={fngData ?? null}
              scanCountdown={`${mm}:${ss}`}
              scanPct={scanPct}
            />
          </div>
        )}

        {/* ── MAIN CONTENT ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>

          {/* Stat boxes row — 3 boxes per spec */}
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr 1fr' : 'repeat(3, 1fr)', gap: 8 }}>
            <StatBox
              label="BALANCE AVAILABLE"
              value={`$${freeCash.toFixed(2)}`}
              sub={`Starting $${starting.toFixed(0)}`}
              color={T.blue}
            />
            <StatBox
              label="UNREALIZED P&L"
              value={`${floatPos ? '+' : ''}${floating.toFixed(2)}$`}
              sub={`${trades.length} open position${trades.length !== 1 ? 's' : ''}`}
              color={floatColor}
              glow={floatPos && floating > 0}
            />
            <div className="stat-third">
              <StatBox
                label={`ACTIVE TRADES ${trades.length}/3`}
                value={`$${lockedBal.toFixed(2)}`}
                sub={`Locked · ${lockedBal > 0 ? `$${(freeCash).toFixed(0)} free` : 'no margin used'}`}
                color={T.amber}
              />
            </div>
          </div>

          {/* Equity curve */}
          {(walletData?.equity_history?.length ?? 0) >= 2 && (
            <div style={panel}>
              <PanelHeader
                label="EQUITY CURVE"
                right={`$${starting.toFixed(0)} start → $${equity.toFixed(2)} now`}
              />
              <div style={{ padding: '8px 12px 12px' }}>
                <EquitySparkline history={walletData!.equity_history} starting={starting} />
              </div>
            </div>
          )}

          {/* Active Positions */}
          <div style={panel}>
            <PanelHeader
              label={`ACTIVE POSITIONS (${trades.length})`}
              right={trades.length > 0
                ? `${trades.filter(t => t.direction === 'LONG').length}L · ${trades.filter(t => t.direction === 'SHORT').length}S`
                : undefined}
            />
            {trades.length === 0 ? (
              <div style={{ padding: '24px', textAlign: 'center', color: T.dimmer, fontSize: 12 }}>
                {'>'} no active positions — bot will open when score ≥90/100
              </div>
            ) : (
              <div style={{
                padding: '10px',
                display: 'grid',
                gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill, minmax(320px, 1fr))',
                gap: 8,
              }}>
                {trades.map(t => <TerminalTradeCard key={t.symbol} trade={t} />)}
              </div>
            )}
          </div>

          {/* Hot candidates strip */}
          <HotStrip candidates={candidates} />

          {/* Controls row */}
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: 8 }}>
            <FngSettingsPanel botApi={BOT_API} />
            <SlotsPanel botApi={BOT_API} />
          </div>

          {/* Last scan */}
          <LastScanPanel scan={scanData ?? null} />

          {/* Reasoning log */}
          <ReasoningLog audit={auditData ?? null} scan={scanData ?? null} isMobile={isMobile} />

          {/* Footer + actions */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 0' }}>
            <div className="footer-actions" style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={refreshAll}
                disabled={refreshing}
                style={{
                  background: 'none', border: `1px solid ${T.border}`, borderRadius: 4,
                  color: refreshing ? T.dimmer : T.amber, cursor: 'pointer',
                  padding: '5px 12px', fontFamily: T.font, fontSize: 11,
                }}
              >
                {refreshing ? '⟳ SYNCING...' : '⟳ SYNC'}
              </button>
              <a
                href={`${BOT_API}/api/audit`}
                target="_blank" rel="noopener noreferrer"
                style={{
                  border: `1px solid ${T.border}`, borderRadius: 4,
                  color: T.blue, padding: '5px 12px', fontFamily: T.font, fontSize: 11,
                  textDecoration: 'none',
                }}
              >↓ AUDIT REPORT</a>
              <a
                href="https://t.me/avi_cripto_bot"
                target="_blank" rel="noopener noreferrer"
                style={{
                  border: `1px solid ${T.border}`, borderRadius: 4,
                  color: T.cyan, padding: '5px 12px', fontFamily: T.font, fontSize: 11,
                  textDecoration: 'none',
                }}
              >✈ TELEGRAM</a>
            </div>
            <span style={{ color: T.dimmer, fontSize: 10 }}>
              BotOS v2 · Score≥90 · 4H→1H→15m · {clock.toLocaleTimeString('he-IL')}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
