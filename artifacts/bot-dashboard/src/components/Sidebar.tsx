interface SidebarProps {
  activeNav: string;
  onNav: (tab: string) => void;
  overlay?: boolean;
}

const NAV_ITEMS = [
  { icon: "⊞", label: "Dashboard" },
  { icon: "📋", label: "Logs" },
  { icon: "📈", label: "Backtest" },
  { icon: "⚙️", label: "Settings" },
];

export function Sidebar({ activeNav, onNav, overlay }: SidebarProps) {
  return (
    <aside
      className="flex-shrink-0 flex flex-col"
      style={{
        width: 180,
        background: "#0a1628",
        borderRight: "1px solid #1e3a5f",
        ...(overlay ? {
          position: "fixed" as const,
          top: 0,
          left: 0,
          height: "100vh",
          zIndex: 40,
        } : {}),
      }}>
      {/* Logo */}
      <div className="flex items-center gap-2 px-4 py-5 border-b"
        style={{ borderColor: "#1e3a5f" }}>
        <div className="w-8 h-8 rounded-lg flex items-center justify-center text-base font-bold text-white"
          style={{ background: "linear-gradient(135deg,#3b82f6,#1d4ed8)" }}>$</div>
        <span className="text-sm font-bold text-blue-300">CryptoBot</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3">
        {NAV_ITEMS.map(({ icon, label }) => (
          <button key={label} onClick={() => onNav(label)}
            className="w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm transition-all"
            style={{
              background: activeNav === label ? "rgba(59,130,246,0.15)" : "transparent",
              color: activeNav === label ? "#93c5fd" : "#64748b",
              borderLeft: `2px solid ${activeNav === label ? "#3b82f6" : "transparent"}`,
            }}>
            <span className="text-base">{icon}</span>
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <div className="mx-3 mb-4 p-3 rounded-xl text-center"
        style={{ background: "#0d1f3c", border: "1px solid #1e3a5f" }}>
        <div className="text-xs text-blue-400 mb-1">📱</div>
        <div className="text-xs font-medium" style={{ color: "#94a3b8" }}>Device View:</div>
        <div className="text-xs text-blue-300">Auto-Detect</div>
      </div>
    </aside>
  );
}
