import { useState, useEffect } from "react";
import { saveSlots, fetchLocalSettings, saveLocalSettings, SlotsData, FngSettings } from "@/hooks/useBotData";

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
  const [maxTrades, setMaxTrades] = useState(3);
  const [amount, setAmount]       = useState(50);
  const [leverage, setLeverage]   = useState(10);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [loaded, setLoaded]       = useState(false);

  // Load from server on mount — server is source of truth for amount/leverage
  useEffect(() => {
    fetchLocalSettings().then(ls => {
      if (ls) {
        setAmount(ls.amount_per_trade);
        setLeverage(ls.default_leverage);
      }
      setLoaded(true);
    });
  }, []);

  // max_trades comes from the live /api/slots response (actual bot state)
  useEffect(() => {
    if (slots?.max_trades) setMaxTrades(slots.max_trades);
  }, [slots]);

  const handleSave = async () => {
    setSaveState("saving");

    // Save amount/leverage to Express server (always reliable)
    const localOk = await saveLocalSettings({ amount_per_trade: amount, default_leverage: leverage });

    // Also attempt to push max_trades to the Flask bot
    const slotsOk = await saveSlots(maxTrades);

    if (localOk) {
      setSaveState("ok");
      onSaved();
    } else if (slotsOk) {
      // Express server write failed but flask succeeded (unusual)
      setSaveState("ok");
      onSaved();
    } else {
      setSaveState("err");
    }
    setTimeout(() => setSaveState("idle"), 3000);
  };

  const st  = SAVE_STYLE[saveState];
  const min = slots?.min ?? 1;
  const max = slots?.max ?? 5;

  if (!loaded) {
    return (
      <div className="rounded-xl p-4" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
        <h2 className="text-xs font-bold uppercase tracking-widest mb-4" style={{ color: "#94a3b8" }}>
          Bot Settings
        </h2>
        <div className="space-y-3 animate-pulse">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-8 rounded" style={{ background: "#0d1f3c" }} />
          ))}
        </div>
      </div>
    );
  }

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
    </div>
  );
}
