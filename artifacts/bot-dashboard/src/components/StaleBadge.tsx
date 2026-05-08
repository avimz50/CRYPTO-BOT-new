import { useState, useEffect } from "react";

interface StaleBadgeProps {
  lastSuccessAt: number | null;
}

export function StaleBadge({ lastSuccessAt }: StaleBadgeProps) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 5_000);
    return () => clearInterval(id);
  }, []);

  if (!lastSuccessAt) return null;

  const sec = Math.round((Date.now() - lastSuccessAt) / 1000);
  const label = sec < 60 ? `~${sec}s ago` : `~${Math.round(sec / 60)}m ago`;

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        fontSize: 10,
        color: "#f59e0b",
        background: "rgba(245,158,11,0.1)",
        border: "1px solid rgba(245,158,11,0.3)",
        borderRadius: 4,
        padding: "1px 5px",
        fontFamily: "monospace",
        fontWeight: 600,
        lineHeight: 1.5,
      }}>
      ⚠ {label}
    </span>
  );
}
