import { useState, useEffect } from "react";
import { saveSlots, saveSettings, lsReadSettings, SlotsData, FngSettings } from "@/hooks/useBotData";

interface BotSettingsProps {
  slots: SlotsData | null;
  fngSettings: FngSettings | null;
  onSaved: () => void;
}

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

interface SaveStatus {
  phase: "idle" | "saving" | "done";
  slotsOk: boolean | null;
  fngOk:   boolean | null;
}

const BTN_META = {
  idle:    { bg: "linear-gradient(135deg,#1d4ed8,#2563eb)", border: "transparent", color: "#fff",    label: "SAVE SETTINGS" },
  saving:  { bg: "rgba(59,130,246,0.15)", border: "#3b82f6", color: "#93c5fd", label: "Saving…" },
  allOk:   { bg: "rgba(34,197,94,0.18)",  border: "#22c55e", color: "#4ade80", label: "✅ Saved!" },
  partial: { bg: "rgba(250,204,21,0.12)", border: "#facc15", color: "#facc15", label: "⚠️ Partially saved" },
  err:     { bg: "rgba(239,68,68,0.15)",  border: "#ef4444", color: "#f87171", label: "❌ Save failed" },
};

export function BotSettings({ slots, fngSettings, onSaved }: BotSettingsProps) {
  // Initialize amount/leverage from localStorage cache (restored between sessions)
  const cached = lsReadSettings();
  const [maxTrades, setMaxTrades] = useState(slots?.max_trades ?? 3);
  const [amount,    setAmount]    = useState(cached.amount_per_trade);
  const [leverage,  setLeverage]  = useState(cached.default_leverage);
  const [status,    setStatus]    = useState<SaveStatus>({ phase: "idle", slotsOk: null, fngOk: null });

  // max_trades from live /api/slots
  useEffect(() => {
    if (slots?.max_trades) setMaxTrades(slots.max_trades);
  }, [slots]);

  const handleSave = async () => {
    setStatus({ phase: "saving", slotsOk: null, fngOk: null });

    // POST max_trades to Flask bot via /api/slots
    // POST amount/leverage + fng thresholds to Flask via /api/fng_settings
    // (also writes amount/leverage to localStorage cache)
    const [slotsOk, { fngOk }] = await Promise.all([
      saveSlots(maxTrades),
      saveSettings({
        fng: fngSettings ?? {},
        amount_per_trade: amount,
        default_leverage: leverage,
      }),
    ]);

    setStatus({ phase: "done", slotsOk, fngOk });
    if (slotsOk || fngOk) onSaved();
    setTimeout(() => setStatus({ phase: "idle", slotsOk: null, fngOk: null }), 4000);
  };

  const meta = (() => {
    if (status.phase === "saving") return BTN_META.saving;
    if (status.phase === "done") {
      if (status.fngOk && status.slotsOk) return BTN_META.allOk;
      if (status.fngOk || status.slotsOk) return BTN_META.partial;
      return BTN_META.err;
    }
    return BTN_META.idle;
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
        disabled={status.phase === "saving"}
        className="w-full py-2 rounded-lg text-xs font-bold uppercase tracking-widest transition-all"
        style={{
          background: meta.bg,
          border: `1px solid ${meta.border}`,
          color: meta.color,
          cursor: status.phase === "saving" ? "not-allowed" : "pointer",
        }}>
        {meta.label}
      </button>

      {status.phase === "done" && (
        <div className="mt-2 space-y-0.5">
          <div className="flex justify-between text-xs" style={{ color: "#475569" }}>
            <span>Max trades</span>
            <span style={{ color: status.slotsOk ? "#4ade80" : "#f87171" }}>
              {status.slotsOk ? "✓ Updated" : "✗ Bot unreachable"}
            </span>
          </div>
          <div className="flex justify-between text-xs" style={{ color: "#475569" }}>
            <span>Amount / leverage</span>
            <span style={{ color: status.fngOk ? "#4ade80" : "#f87171" }}>
              {status.fngOk ? "✓ Sent to API" : "✗ API unreachable"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
