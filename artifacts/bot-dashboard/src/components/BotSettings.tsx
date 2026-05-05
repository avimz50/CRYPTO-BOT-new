import { useState, useEffect } from "react";
import { saveSlots, saveFngSettings, SlotsData, FngSettings } from "@/hooks/useBotData";

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
          <div className="absolute top-0 left-0 h-full rounded-full"
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

export function BotSettings({ slots, fngSettings, onSaved }: BotSettingsProps) {
  const [maxTrades, setMaxTrades] = useState(3);
  const [amount, setAmount] = useState(50);
  const [leverage, setLeverage] = useState(10);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "ok" | "err">("idle");

  useEffect(() => {
    if (slots) setMaxTrades(slots.max_trades ?? 3);
    if (slots?.amount_per_trade) setAmount(slots.amount_per_trade);
    else if (fngSettings?.amount_per_trade) setAmount(fngSettings.amount_per_trade);
    if (slots?.default_leverage) setLeverage(slots.default_leverage);
    else if (fngSettings?.default_leverage) setLeverage(fngSettings.default_leverage);
  }, [slots, fngSettings]);

  const handleSave = async () => {
    setSaveState("saving");
    const [slotsOk, fngOk] = await Promise.all([
      saveSlots(maxTrades),
      saveFngSettings({
        ...(fngSettings ?? {}),
        amount_per_trade: amount,
        default_leverage: leverage,
      }),
    ]);
    const ok = slotsOk || fngOk;
    setSaveState(ok ? "ok" : "err");
    if (ok) onSaved();
    setTimeout(() => setSaveState("idle"), 2500);
  };

  const saveLabel =
    saveState === "saving" ? "Saving..." :
    saveState === "ok"     ? "✅ Saved!" :
    saveState === "err"    ? "❌ Error"  : "SAVE SETTINGS";

  const saveBg =
    saveState === "ok"  ? "rgba(34,197,94,0.2)" :
    saveState === "err" ? "rgba(239,68,68,0.2)" :
    "linear-gradient(135deg,#1d4ed8,#2563eb)";

  const saveBorder =
    saveState === "ok"  ? "1px solid #22c55e" :
    saveState === "err" ? "1px solid #ef4444" :
    "1px solid transparent";

  const saveColor =
    saveState === "ok"  ? "#4ade80" :
    saveState === "err" ? "#f87171" : "#fff";

  const min = slots?.min ?? 1;
  const max = slots?.max ?? 5;

  return (
    <div className="rounded-xl p-4" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
      <h2 className="text-xs font-bold uppercase tracking-widest mb-4" style={{ color: "#94a3b8" }}>
        Bot Settings
      </h2>

      <SliderRow
        label="Max Concurrent Trades"
        value={maxTrades} min={min} max={max} unit=""
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
          background: saveBg,
          border: saveBorder,
          color: saveColor,
          cursor: saveState === "saving" ? "not-allowed" : "pointer",
        }}>
        {saveLabel}
      </button>
    </div>
  );
}
