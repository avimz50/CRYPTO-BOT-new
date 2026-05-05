import { useState, useEffect } from "react";
import { saveSlots, saveBotConfig, fetchBotConfig, SlotsData } from "@/hooks/useBotData";

interface BotSettingsProps {
  slots: SlotsData | null;
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
  slotsOk:  boolean | null;
  configOk: boolean | null;
}

const BTN_META = {
  idle:    { bg: "linear-gradient(135deg,#1d4ed8,#2563eb)", border: "transparent", color: "#fff",    label: "SAVE SETTINGS" },
  saving:  { bg: "rgba(59,130,246,0.15)", border: "#3b82f6", color: "#93c5fd", label: "Saving…" },
  allOk:   { bg: "rgba(34,197,94,0.18)",  border: "#22c55e", color: "#4ade80", label: "✅ Saved!" },
  partial: { bg: "rgba(250,204,21,0.12)", border: "#facc15", color: "#facc15", label: "⚠️ Partially saved" },
  err:     { bg: "rgba(239,68,68,0.15)",  border: "#ef4444", color: "#f87171", label: "❌ Save failed" },
};

export function BotSettings({ slots, onSaved }: BotSettingsProps) {
  const [maxTrades, setMaxTrades] = useState(3);
  const [amount,    setAmount]    = useState(50);
  const [leverage,  setLeverage]  = useState(10);
  const [ready,     setReady]     = useState(false);
  const [status,    setStatus]    = useState<SaveStatus>({ phase: "idle", slotsOk: null, configOk: null });

  // Load amount/leverage from server-side config store on mount (source of truth)
  useEffect(() => {
    fetchBotConfig().then(cfg => {
      if (cfg) {
        setAmount(cfg.amount_per_trade);
        setLeverage(cfg.default_leverage);
      }
      setReady(true);
    });
  }, []);

  // max_trades from live /api/slots response
  useEffect(() => {
    if (slots?.max_trades) setMaxTrades(slots.max_trades);
  }, [slots]);

  const handleSave = async () => {
    setStatus({ phase: "saving", slotsOk: null, configOk: null });

    const [configOk, slotsOk] = await Promise.all([
      saveBotConfig({ amount_per_trade: amount, default_leverage: leverage }),
      saveSlots(maxTrades),
    ]);

    setStatus({ phase: "done", slotsOk, configOk });
    if (configOk || slotsOk) onSaved();
    setTimeout(() => setStatus({ phase: "idle", slotsOk: null, configOk: null }), 4000);
  };

  const meta = (() => {
    if (status.phase === "saving") return BTN_META.saving;
    if (status.phase === "done") {
      if (status.configOk && status.slotsOk) return BTN_META.allOk;
      if (status.configOk || status.slotsOk) return BTN_META.partial;
      return BTN_META.err;
    }
    return BTN_META.idle;
  })();

  const slotMin = slots?.min ?? 1;
  const slotMax = slots?.max ?? 5;

  if (!ready) {
    return (
      <div className="rounded-xl p-4" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
        <h2 className="text-xs font-bold uppercase tracking-widest mb-4" style={{ color: "#94a3b8" }}>
          Bot Settings
        </h2>
        <div className="space-y-3 animate-pulse">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-7 rounded" style={{ background: "#0d1f3c" }} />
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
            <span>Amount / leverage</span>
            <span style={{ color: status.configOk ? "#4ade80" : "#f87171" }}>
              {status.configOk ? "✓ Saved to server" : "✗ Server unreachable"}
            </span>
          </div>
          <div className="flex justify-between text-xs" style={{ color: "#475569" }}>
            <span>Max trades (bot API)</span>
            <span style={{ color: status.slotsOk ? "#4ade80" : "#f87171" }}>
              {status.slotsOk ? "✓ Updated" : "✗ Bot unreachable"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
