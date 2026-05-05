import { useState, useEffect } from "react";
import { saveSlots, saveFngSettings, SlotsData, FngSettings } from "@/hooks/useBotData";

// localStorage key — browser-side fallback when bot API is unavailable
const LS_KEY = "botSettings_v1";

interface Stored { maxTrades?: number; amount?: number; leverage?: number }
function loadStored(): Stored {
  try { return JSON.parse(localStorage.getItem(LS_KEY) ?? "{}") as Stored; } catch { return {}; }
}
function persistStored(v: Stored) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(v)); } catch { /* ignore */ }
}

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
          <div className="absolute top-0 left-0 h-full rounded-full transition-all"
            style={{ width: `${pct}%`, background: "#3b82f6" }} />
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

type SaveState = "idle" | "saving" | "ok" | "localOnly";

const SAVE_META: Record<SaveState, { bg: string; border: string; color: string; label: string }> = {
  idle:      { bg: "linear-gradient(135deg,#1d4ed8,#2563eb)", border: "transparent", color: "#fff",     label: "SAVE SETTINGS" },
  saving:    { bg: "rgba(59,130,246,0.15)", border: "#3b82f6", color: "#93c5fd",  label: "Saving..." },
  ok:        { bg: "rgba(34,197,94,0.18)",  border: "#22c55e", color: "#4ade80",  label: "✅ Saved!" },
  localOnly: { bg: "rgba(250,204,21,0.12)", border: "#facc15", color: "#facc15",  label: "💾 Saved locally" },
};

export function BotSettings({ slots, fngSettings, onSaved }: BotSettingsProps) {
  // Seed from stored values (localStorage), then overwrite from live API once loaded
  const stored = loadStored();
  const [maxTrades, setMaxTrades] = useState(stored.maxTrades ?? 3);
  const [amount,    setAmount]    = useState(stored.amount    ?? 50);
  const [leverage,  setLeverage]  = useState(stored.leverage  ?? 10);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  // When live API data arrives, it is the authoritative source (overrides stored)
  useEffect(() => {
    if (slots?.max_trades)          setMaxTrades(slots.max_trades);
    if (slots?.amount_per_trade)    setAmount(slots.amount_per_trade);
    if (slots?.default_leverage)    setLeverage(slots.default_leverage);
  }, [slots]);

  useEffect(() => {
    if (!slots) {
      if (fngSettings?.amount_per_trade) setAmount(fngSettings.amount_per_trade);
      if (fngSettings?.default_leverage) setLeverage(fngSettings.default_leverage);
    }
  }, [fngSettings, slots]);

  const handleSave = async () => {
    setSaveState("saving");

    // Always persist locally so values survive a page refresh
    persistStored({ maxTrades, amount, leverage });

    // Attempt both API writes — one for max_trades (slots), one for amount/leverage (fng_settings)
    const fngPayload: Partial<FngSettings> = {
      ...(fngSettings ?? {}),
      amount_per_trade: amount,
      default_leverage: leverage,
    };

    const [slotsOk, fngOk] = await Promise.all([
      saveSlots(maxTrades),
      saveFngSettings(fngPayload),
    ]);

    if (slotsOk || fngOk) {
      setSaveState("ok");
      onSaved();
    } else {
      // Both API calls failed — settings preserved in localStorage
      setSaveState("localOnly");
    }

    setTimeout(() => setSaveState("idle"), 3500);
  };

  const m = SAVE_META[saveState];
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
        disabled={saveState === "saving"}
        className="w-full py-2 rounded-lg text-xs font-bold uppercase tracking-widest transition-all"
        style={{
          background: m.bg,
          border: `1px solid ${m.border}`,
          color: m.color,
          cursor: saveState === "saving" ? "not-allowed" : "pointer",
        }}>
        {m.label}
      </button>

      {saveState === "localOnly" && (
        <p className="text-xs mt-1.5 text-center" style={{ color: "#64748b" }}>
          Bot unreachable — settings saved in browser until reconnected
        </p>
      )}
    </div>
  );
}
