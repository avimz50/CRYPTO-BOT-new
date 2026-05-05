interface FngGaugeProps {
  value: number | null;
  label: string | null;
  compact?: boolean;
}

function fngColor(v: number) {
  if (v < 25) return "#ef4444";
  if (v < 45) return "#f97316";
  if (v < 55) return "#facc15";
  if (v < 75) return "#84cc16";
  return "#22c55e";
}

function fngLabel(v: number) {
  if (v < 25) return "EXTREME FEAR";
  if (v < 45) return "FEAR";
  if (v < 55) return "NEUTRAL";
  if (v < 75) return "GREED";
  return "EXTREME GREED";
}

const ZONES = [
  { from: 0, to: 20, color: "#ef4444" },
  { from: 20, to: 40, color: "#f97316" },
  { from: 40, to: 60, color: "#facc15" },
  { from: 60, to: 80, color: "#84cc16" },
  { from: 80, to: 100, color: "#22c55e" },
];

export function FngGauge({ value, label, compact }: FngGaugeProps) {
  const v = value ?? 50;
  const col = fngColor(v);
  const lbl = label ?? fngLabel(v);

  const W = compact ? 120 : 160;
  const H = compact ? 76  : 100;
  const cx = W / 2, cy = H * 0.8, R = compact ? 46 : 62;

  const pt = (r: number, val: number) => {
    const θ = (180 - (val / 100) * 180) * Math.PI / 180;
    return { x: cx + r * Math.cos(θ), y: cy - r * Math.sin(θ) };
  };

  const arc = (from: number, to: number) => {
    const p1 = pt(R, from), p2 = pt(R, to);
    return `M ${p1.x.toFixed(1)} ${p1.y.toFixed(1)} A ${R} ${R} 0 0 1 ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  };

  const needle = pt(R * 0.87, v);

  return (
    <div className="flex flex-col items-center">
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H}>
        {ZONES.map((z, i) => (
          <path key={i} d={arc(z.from, z.to)} fill="none" stroke={z.color}
            strokeWidth={compact ? 9 : 12} strokeLinecap="butt" opacity={0.2} />
        ))}
        {ZONES.map((z, i) => {
          const end = Math.min(v, z.to);
          if (end <= z.from) return null;
          return (
            <path key={`f${i}`} d={arc(z.from, end)} fill="none" stroke={z.color}
              strokeWidth={compact ? 9 : 12} strokeLinecap="butt" />
          );
        })}
        <line x1={cx} y1={cy} x2={needle.x} y2={needle.y}
          stroke="#000" strokeWidth={compact ? 2 : 3} strokeLinecap="round" opacity={0.4} />
        <line x1={cx} y1={cy} x2={needle.x} y2={needle.y}
          stroke="#fff" strokeWidth={2} strokeLinecap="round" />
        <circle cx={needle.x} cy={needle.y} r={compact ? 2 : 3} fill={col} />
        <circle cx={cx} cy={cy} r={compact ? 4 : 6} fill="#0d1f3c" stroke={col} strokeWidth={2} />
        <text x={cx} y={cy - (compact ? 12 : 16)} textAnchor="middle"
          fill={col} fontSize={compact ? 15 : 20} fontWeight="bold" fontFamily="monospace">
          {value ?? "—"}
        </text>
        <text x={cx} y={cy - (compact ? 3 : 5)} textAnchor="middle"
          fill="#94a3b8" fontSize={compact ? 5 : 6.5} fontFamily="sans-serif">
          {lbl}
        </text>
      </svg>
      {compact && (
        <span className="text-xs font-bold mt-0.5" style={{ color: col }}>{lbl}</span>
      )}
    </div>
  );
}
