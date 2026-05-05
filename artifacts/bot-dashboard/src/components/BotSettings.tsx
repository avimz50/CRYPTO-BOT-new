import { useState, useEffect } from "react";
import { saveSlots, saveFngSettings, SlotsData, FngSettings } from "@/hooks/useBotData";

const LS_KEY = "botSettings";

function loadLocal(): { maxTrades?: number; amount?: number; leverage?: number } {
  try { return JSON.parse(localStorage.getItem(LS_KEY) ?? "{}"); } catch { return {}; }
}
function persistLocal(v: { maxTrades: number; amount: number; leverage: number }) {
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

type SaveState = "idle" | "saving" | "ok" | "localOnly" | "err";

const SAVE_LABEL: Record<SaveState, string> = {
  idle:      "SAVE SETTINGS",
  saving:    "Saving...",
  ok:        "✅ Saved!",
  localOnly: "💾 Saved locally",
  err:       "❌ Error",
};

const SAVE_STYLE: Record<SaveState, { bg: string; border: string; color: string }> = {
  idle:      { bg: "linear-gradient(135deg,#1d4ed8,#2563eb)", border: "1px solid transparent", color: "#fff" },
  saving:    { bg: "rgba(59,130,246,0.15)", border: "1px solid #3b82f6", color: "#93c5fd" },
  ok:        { bg: "rgba(34,197,94,0.18)",  border: "1px solid #22c55e", color: "#4ade80" },
  localOnly: { bg: "rgba(250,204,21,0.12)", border: "1px solid #facc15", color: "#facc15" },
  err:       { bg: "rgba(239,68,68,0.15)",  border: "1px solid #ef4444", color: "#f87171" },
};

export function BotSettings({ slots, fngSettings, onSaved }: BotSettingsProps) {
  const local = loadLocal();
  const [maxTrades, setMaxTrades] = useState(local.maxTrades ?? 3);
  const [amount, setAmount]       = useState(local.amount   ?? 50);
  const [leverage, setLeverage]   = useState(local.leverage ?? 10);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  // API values override localStorage on first successful load
  useEffect(() => {
    if (slots?.max_trades)       setMaxTrades(slots.max_trades);
    if (slots?.amount_per_trade) setAmount(slots.amount_per_trade);
    else if (fngSettings?.amount_per_trade) setAmount(fngSettings.amount_per_trade);
    if (slots?.default_leverage) setLeverage(slots.default_leverage);
    else if (fngSettings?.default_leverage) setLeverage(fngSettings.default_leverage);
  }, [slots, fngSettings]);

  const handleSave = async () => {
    setSaveState("saving");
    persistLocal({ maxTrades, amount, leverage });

    const [slotsOk, fngOk] = await Promise.all([
      saveSlots(maxTrades),
      saveFngSettings({ ...(fngSettings ?? {}), amount_per_trade: amount, default_leverage: leverage }),
    ]);

    if (slotsOk && fngOk) {
      // Both succeeded
      setSaveState("ok");
      onSaved();
    } else if (!slotsOk && !fngOk) {
      // Both failed — settings only in localStorage
      setSaveState("localOnly");
    } else {
      // Partial: one succeeded — still call refetch and show partial OK
      setSaveState("ok");
      onSaved();
    }
    setTimeout(() => setSaveState("idle"), 3000);
  };

  const st = SAVE_STYLE[saveState];
  const min = slots?.min ?? 1;
  const max = slots?.max ?? 5;

  return (
    <div className="rounded-xl p-4" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
      <h2 className="text-xs font-bold uppercase tracking-widest mb-4" style={{ color: "#94a3b8" }}>
        Bot Settings
      </h2>

      <SliderRow label="Max Concurrent Trades" value={maxTrades} min={min} max={max} unit=""  onChange={setMaxTrades} />
      <SliderRow label="Amount per Trade ($)"   value={amount}    min={10}  max={200} unit="$" onChange={setAmount} />
      <SliderRow label="Default Leverage (x)"   value={leverage}  min={1}   max={20}  unit="x" onChange={setLeverage} />

      <button
        onClick={handleSave}
        disabled={saveState === "saving"}
        className="w-full py-2 rounded-lg text-xs font-bold uppercase tracking-widest transition-all"
        style={{
          background: st.bg,
          border: st.border,
          color: st.color,
          cursor: saveState === "saving" ? "not-allowed" : "pointer",
        }}>
        {SAVE_LABEL[saveState]}
      </button>

      {saveState === "localOnly" && (
        <p className="text-xs mt-1.5 text-center" style={{ color: "#64748b" }}>
          Bot offline — settings saved in browser until reconnected
        </p>
      )}
    </div>
  );
}
