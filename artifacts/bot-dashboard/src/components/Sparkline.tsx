interface SparklineProps {
  points: number[];
  color?: string;
  width?: number;
  height?: number;
}

export function Sparkline({ points, color = "#3b82f6", width = 140, height = 44 }: SparklineProps) {
  if (!points || points.length < 2) {
    return (
      <div className="flex items-center justify-center text-xs"
        style={{ width, height, color: "#334155" }}>
        no data
      </div>
    );
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const W = width, H = height;

  const xs = points.map((_, i) => (i / (points.length - 1)) * W);
  const ys = points.map(v => H - ((v - min) / range) * (H - 4) - 2);
  const polyline = xs.map((x, i) => `${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  const area = `0,${H} ${polyline} ${W},${H}`;

  const lastX = xs[xs.length - 1];
  const lastY = ys[ys.length - 1];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H}>
      <defs>
        <linearGradient id="spk-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polygon points={area} fill="url(#spk-grad)" />
      <polyline points={polyline} fill="none" stroke={color}
        strokeWidth="2" strokeLinejoin="round" />
      <circle cx={lastX} cy={lastY} r="3" fill={color} />
    </svg>
  );
}
