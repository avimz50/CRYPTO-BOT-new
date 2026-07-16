import { useState, useEffect } from "react";
import { saveSlots, saveBotSettings, SlotsData, FngSettings } from "@/hooks/useBotData";
import { usePushNotifications } from "@/hooks/usePushNotifications";

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
  // amount & leverage are locked in config.py — always $50 / 10x
  const [maxTrades, setMaxTrades] = useState(slots?.max_trades ?? 3);
  const amount   = 50;   // locked — MARGIN in config.py
  const leverage = 10;   // locked — LEVERAGE in config.py
  const [status,    setStatus]    = useState<SaveStatus>({ phase: "idle", slotsOk: null, fngOk: null });

  // Sync max_trades from live /api/slots
  useEffect(() => {
    if (slots?.max_trades) setMaxTrades(slots.max_trades);
  }, [slots]);

  const handleSave = async () => {
    setStatus({ phase: "saving", slotsOk: null, fngOk: null });

    // POST max_trades via /api/slots
    // POST all settings (thresholds + amount/leverage) via /api/fng_settings
    // localStorage is also updated as a client-side cache inside saveBotSettings
    const [slotsOk, fngOk] = await Promise.all([
      saveSlots(maxTrades),
      saveBotSettings({
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

  const { state: pushState, subscribe, unsubscribe } = usePushNotifications();

  const pushBg      = pushState === "subscribed" ? "rgba(34,197,94,0.1)"  : "rgba(251,191,36,0.07)";
  const pushBorder  = pushState === "subscribed" ? "#22c55e55"             : "#f59e0b55";
  const pushBtnBg   = pushState === "subscribed" ? "rgba(239,68,68,0.15)" : "linear-gradient(135deg,#b45309,#d97706)";
  const pushBtnClr  = pushState === "subscribed" ? "#f87171"               : "#fff";
  const pushBtnTxt  = pushState === "subscribed" ? "🔕  כבה התראות"       :
                      pushState === "loading"     ? "⏳  בודק..."          :
                      pushState === "denied"      ? "🚫  חסום בדפדפן"      :
                      pushState === "unsupported" ? "❌  הדפדפן לא תומך"   : "🔔  הפעל התראות";

  return (
    <div className="space-y-3">

    {/* ── Push Notifications card ────────────────────────────────────── */}
    <div className="rounded-xl p-4" style={{ background: pushBg, border: `1px solid ${pushBorder}` }}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-bold" style={{ color: "#e2e8f0" }}>🔔 התראות Push</span>
        <span className="text-xs px-2 py-0.5 rounded-full font-semibold"
          style={{
            background: pushState === "subscribed" ? "rgba(34,197,94,0.2)" : "rgba(100,116,139,0.2)",
            color:      pushState === "subscribed" ? "#4ade80"              : "#94a3b8",
          }}>
          {pushState === "subscribed" ? "✅ פעיל" : pushState === "loading" ? "..." : "כבוי"}
        </span>
      </div>
      <p className="text-xs mb-3" style={{ color: "#94a3b8" }}>
        {pushState === "subscribed"
          ? "תקבל התראה לטלפון בכל פתיחה וסגירה של עסקה."
          : pushState === "denied"
          ? "התראות חסומות — כנס להגדרות הדפדפן ואפשר אותן עבור האתר הזה."
          : pushState === "unsupported"
          ? "הדפדפן שלך לא תומך ב-Push. נסה Chrome."
          : "לחץ כדי לקבל התראות על כל עסקה שנפתחת או נסגרת."}
      </p>
      <button
        onClick={pushState === "subscribed" ? unsubscribe : subscribe}
        disabled={pushState === "loading" || pushState === "denied" || pushState === "unsupported"}
        className="w-full py-2.5 rounded-lg text-sm font-bold transition-all"
        style={{
          background: pushBtnBg,
          color: pushBtnClr,
          border: "none",
          cursor: (pushState === "loading" || pushState === "denied" || pushState === "unsupported") ? "not-allowed" : "pointer",
          opacity: (pushState === "loading" || pushState === "denied" || pushState === "unsupported") ? 0.6 : 1,
        }}>
        {pushBtnTxt}
      </button>
      {pushState === "unsupported" && (
        <p className="text-xs mt-2 text-center" style={{ color: "#64748b" }}>
          פתח את הקישור ב-Chrome לאפשור התראות
        </p>
      )}
    </div>

    {/* ── Bot Settings card ─────────────────────────────────────────── */}
    <div className="rounded-xl p-4" style={{ background: "#0a1628", border: "1px solid #1e3a5f" }}>
      <h2 className="text-xs font-bold uppercase tracking-widest mb-4" style={{ color: "#94a3b8" }}>
        Bot Settings
      </h2>

      <SliderRow
        label="Max Concurrent Trades"
        value={maxTrades} min={slotMin} max={slotMax} unit=""
        onChange={setMaxTrades}
      />
      {/* Amount & Leverage — locked by config, display-only */}
      <div className="mb-4">
        <div className="flex justify-between mb-1">
          <span className="text-xs" style={{ color: "#94a3b8" }}>Amount per Trade ($)</span>
          <span className="text-xs font-mono font-semibold" style={{ color: "#4ade80" }}>
            ${amount} 🔒
          </span>
        </div>
        <div className="text-xs rounded px-2 py-1.5" style={{ background: "#0f2040", color: "#475569", border: "1px solid #1e3a5f" }}>
          נעול — $50 מרג'ין קבוע לכל עסקה (config.py)
        </div>
      </div>
      <div className="mb-4">
        <div className="flex justify-between mb-1">
          <span className="text-xs" style={{ color: "#94a3b8" }}>Default Leverage (x)</span>
          <span className="text-xs font-mono font-semibold" style={{ color: "#4ade80" }}>
            {leverage}x 🔒
          </span>
        </div>
        <div className="text-xs rounded px-2 py-1.5" style={{ background: "#0f2040", color: "#475569", border: "1px solid #1e3a5f" }}>
          נעול — 10x מינוף קבוע לכל עסקה (config.py)
        </div>
      </div>

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
            <span>Settings</span>
            <span style={{ color: status.fngOk ? "#4ade80" : "#f87171" }}>
              {status.fngOk ? "✓ Sent to bot" : "✗ Bot unreachable"}
            </span>
          </div>
        </div>
      )}
    </div>
    </div>
  );
}
