import { useState, useEffect } from "react";
import { saveSlots, SlotsData } from "@/hooks/useBotData";

// ── Browser localStorage — persists amount/leverage across page refreshes ──
// Flask has no endpoint for these two settings, so localStorage is the storage layer.
const LS_KEY = "botDashboard_settings_v1";
interface StoredSettings { amount_per_trade: number; default_leverage: number }
const LS_DEFAULTS: StoredSettings = { amount_per_trade: 50, default_leverage: 10 };

function lsRead(): StoredSettings {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return { ...LS_DEFAULTS };
    const p = JSON.parse(raw) as Partial<StoredSettings>;
    return {
      amount_per_trade: typeof p.amount_per_trade === "number" ? p.amount_per_trade : LS_DEFAULTS.amount_per_trade,
      default_leverage: typeof p.default_leverage === "number" ? p.default_leverage : LS_DEFAULTS.default_leverage,
    };
  } catch { return { ...LS_DEFAULTS }; }
}

function lsWrite(v: StoredSettings): void {
  try { localStorage.setItem(LS_KEY, JSON.stringify(v)); } catch { /* quota exceeded — ignore */ }
}

// ── Props ──────────────────────────────────────────────────────
interface BotSettingsProps {
  slots: SlotsData | null;
  onSaved: () => void;
}

// ── Slider component ───────────────────────────────────────────
function SliderRow({
  label, value, min, max, unit, onChange,
}: {
  label: string; value: number; min: number; max: number; unit: string;
  onChange: (v: number) => void;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="mb-4">
      <div className="flex justify-between mb-1.5">
        <span className="text-xs" style={{ color: "#94a3b8" }}>{label}</span>
        <span className="text-xs font-mono font-semibold" style={{ color: "#93c5fd" }}>
          {value}{unit}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs w-4 text-right" style={{ color: "#475569" }}>{min}</span>
        <div className="relative flex-1 h-1.5 rounded-full" style={{ background: "#1e3a5f" }}>
          <div
            className="absolute top-0 left-0 h-full rounded-full transition-all"
            style={{ width: `${pct}%`, background: "#3b82f6" }}
          />
          <input
            type="range" min={min} max={max} value={value}
            onChange={e => onChange(Number(e.target.value))}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
          />
        </div>
        <span className="text-xs w-6" style={{ color: "#475569" }}>{max}</span>
      </div>
    </div>
  );
}

// ── Save states ────────────────────────────────────────────────
// Two independent axes: slots (bot API) and local (localStorage)
interface SaveStatus {
  state: "idle" | "saving" | "done";
  slotsOk: boolean | null;   // null = not yet attempted
  localOk: boolean | null;
}

export function BotSettings({ slots, onSaved }: BotSettingsProps) {
  const stored = lsRead();
  const [maxTrades, setMaxTrades] = useState(slots?.max_trades ?? 3);
  const [amount,    setAmount]    = useState(stored.amount_per_trade);
  const [leverage,  setLeverage]  = useState(stored.default_leverage);
  const [status,    setStatus]    = useState<SaveStatus>({ state: "idle", slotsOk: null, localOk: null });

  // Sync max_trades from live bot API data
  useEffect(() => {
    if (slots?.max_trades) setMaxTrades(slots.max_trades);
  }, [slots]);

  const handleSave = async () => {
    setStatus({ state: "saving", slotsOk: null, localOk: null });

    // Save amount/leverage to localStorage (persistent, no server required)
    lsWrite({ amount_per_trade: amount, default_leverage: leverage });
    const localOk = lsRead().amount_per_trade === amount && lsRead().default_leverage === leverage;

    // Push max_trades to Flask bot via /api/slots
    const slotsOk = await saveSlots(maxTrades);

    setStatus({ state: "done", slotsOk, localOk });
    if (slotsOk || localOk) onSaved();

    // Reset to idle after 4 s
    setTimeout(() => setStatus({ state: "idle", slotsOk: null, localOk: null }), 4000);
  };

  // ── Button appearance ─────────────────────────────────────────
  const btnStyle = (() => {
    if (status.state === "saving") {
      return { bg: "rgba(59,130,246,0.15)", border: "#3b82f6", color: "#93c5fd", label: "Saving…" };
    }
    if (status.state === "done") {
      const allOk = status.slotsOk && status.localOk;
      const anyOk = status.slotsOk || status.localOk;
      if (allOk)  return { bg: "rgba(34,197,94,0.18)",  border: "#22c55e", color: "#4ade80", label: "✅ Saved!" };
      if (anyOk)  return { bg: "rgba(250,204,21,0.12)", border: "#facc15", color: "#facc15", label: "⚠️ Partially saved" };
      return        { bg: "rgba(239,68,68,0.15)",  border: "#ef4444", color: "#f87171", label: "❌ Save failed" };
    }
    return { bg: "linear-gradient(135deg,#1d4ed8,#2563eb)", border: "transparent", color: "#fff", label: "SAVE SETTINGS" };
  })();

  const slotMin = slots?.min ?? 1;
  const slotMax = slots?.max ?? 5;

  return (
    <div className="rounded-xl p-4" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
      <h2 className="text-xs font-bold uppercase tracking-widest mb-4" style={{ color: "#94a3b8" }}>
        Bot Settings
      </h2>

      <SliderRow
        label="Max Concurrent Trades"
        value={maxTrades} min={slotMin} max={slotMax} unit=""
        onChange={setMaxTrades}
      />
      <SliderRow
        label="Amount per Trade ($)"
        value={amount} min={10} max={200} unit="$"
        onChange={setAmount}
      />
      <SliderRow
        label="Default Leverage (x)"
        value={leverage} min={1} max={20} unit="x"
        onChange={setLeverage}
      />

      <button
        onClick={handleSave}
        disabled={status.state === "saving"}
        className="w-full py-2 rounded-lg text-xs font-bold uppercase tracking-widest transition-all"
        style={{
          background: btnStyle.bg,
          border: `1px solid ${btnStyle.border}`,
          color: btnStyle.color,
          cursor: status.state === "saving" ? "not-allowed" : "pointer",
        }}>
        {btnStyle.label}
      </button>

      {/* Per-axis feedback — shown only after a save attempt */}
      {status.state === "done" && (
        <div className="mt-2 space-y-0.5">
          <div className="flex justify-between text-xs" style={{ color: "#475569" }}>
            <span>Bot slots (max trades)</span>
            <span style={{ color: status.slotsOk ? "#4ade80" : "#f87171" }}>
              {status.slotsOk ? "✓ Updated" : "✗ Bot unreachable"}
            </span>
          </div>
          <div className="flex justify-between text-xs" style={{ color: "#475569" }}>
            <span>Amount / leverage</span>
            <span style={{ color: status.localOk ? "#4ade80" : "#f87171" }}>
              {status.localOk ? "✓ Saved in browser" : "✗ Storage error"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
