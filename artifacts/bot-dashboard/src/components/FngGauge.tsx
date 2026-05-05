interface FngGaugeProps {
  value: number | null;
  label: string | null;
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

export function FngGauge({ value, label }: FngGaugeProps) {
  const v = value ?? 50;
  const cx = 80, cy = 80, R = 62;
  const col = fngColor(v);
  const lbl = label ?? fngLabel(v);

  const pt = (r: number, val: number) => {
    const θ = (180 - (val / 100) * 180) * Math.PI / 180;
    return { x: cx + r * Math.cos(θ), y: cy - r * Math.sin(θ) };
  };

  const arc = (from: number, to: number) => {
    const p1 = pt(R, from), p2 = pt(R, to);
    return `M ${p1.x.toFixed(1)} ${p1.y.toFixed(1)} A ${R} ${R} 0 0 1 ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  };

  const needle = pt(54, v);

  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 160 100" width="160" height="100">
        {ZONES.map((z, i) => (
          <path key={i} d={arc(z.from, z.to)} fill="none" stroke={z.color}
            strokeWidth={12} strokeLinecap="butt" opacity={0.2} />
        ))}
        {ZONES.map((z, i) => {
          const end = Math.min(v, z.to);
          if (end <= z.from) return null;
          return (
            <path key={`f${i}`} d={arc(z.from, end)} fill="none" stroke={z.color}
              strokeWidth={12} strokeLinecap="butt" />
          );
        })}
        {[0, 25, 50, 75, 100].map(tv => {
          const inn = pt(R - 8, tv), out = pt(R + 2, tv);
          return <line key={tv} x1={inn.x} y1={inn.y} x2={out.x} y2={out.y}
            stroke="white" strokeWidth={1} opacity={0.3} />;
        })}
        <line x1={cx} y1={cy} x2={needle.x} y2={needle.y}
          stroke="#000" strokeWidth={3} strokeLinecap="round" opacity={0.4} />
        <line x1={cx} y1={cy} x2={needle.x} y2={needle.y}
          stroke="#fff" strokeWidth={2} strokeLinecap="round"
          style={{ transition: "all 1.2s cubic-bezier(0.34,1.56,0.64,1)" }} />
        <circle cx={needle.x} cy={needle.y} r={3} fill={col} />
        <circle cx={cx} cy={cy} r={6} fill="#0d1f3c" stroke={col} strokeWidth={2} />
        <text x={cx} y={cy - 16} textAnchor="middle"
          fill={col} fontSize={20} fontWeight="bold" fontFamily="monospace">
          {value ?? "—"}
        </text>
        <text x={cx} y={cy - 5} textAnchor="middle"
          fill="#94a3b8" fontSize={6.5} fontFamily="sans-serif">
          {lbl}
        </text>
      </svg>
    </div>
  );
}
