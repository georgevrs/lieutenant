import { useEffect, useRef, useState } from "react";
import type { LogEntry } from "../hooks/useDaemon";

interface Props {
  open: boolean;
  logs: LogEntry[];
  onClose: () => void;
}

const levelColor: Record<string, string> = {
  ERROR: "#ff4d4f",
  WARNING: "#faad14",
  INFO: "#8c8c8c",
  DEBUG: "#595959",
};

export function LogPanel({ open, logs, onClose }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filter, setFilter] = useState<string>("ALL");

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, autoScroll, open]);

  // Detect manual scroll-up to pause auto-scroll
  const handleScroll = () => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setAutoScroll(atBottom);
  };

  const filtered =
    filter === "ALL" ? logs : logs.filter((l) => l.level === filter);

  const fmtTime = (ts: number) => {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-GB", { hour12: false }) +
      "." + String(d.getMilliseconds()).padStart(3, "0");
  };

  return (
    <div
      style={{
        position: "fixed",
        bottom: 0,
        left: 0,
        right: 0,
        height: open ? "38vh" : 0,
        background: "#0a0a0f",
        borderTop: open ? "1px solid var(--border)" : "none",
        transition: "height 0.3s ease",
        overflow: "hidden",
        zIndex: 80,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Toolbar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "6px 16px",
          background: "#111118",
          borderBottom: "1px solid var(--border)",
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-dim)" }}>
          ⬢ DAEMON LOGS
        </span>
        {/* Level filter buttons */}
        {["ALL", "ERROR", "WARNING", "INFO", "DEBUG"].map((lvl) => (
          <button
            key={lvl}
            onClick={() => setFilter(lvl)}
            style={{
              fontSize: 11,
              padding: "2px 8px",
              borderRadius: 4,
              border: "1px solid",
              borderColor: filter === lvl ? "var(--accent)" : "var(--border)",
              background: filter === lvl ? "var(--accent)" : "transparent",
              color: filter === lvl ? "#fff" : (levelColor[lvl] ?? "var(--text-dim)"),
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            {lvl}
          </button>
        ))}

        <span style={{ flex: 1 }} />

        <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
          {filtered.length} entries
        </span>

        {!autoScroll && (
          <button
            onClick={() => {
              setAutoScroll(true);
              bottomRef.current?.scrollIntoView({ behavior: "smooth" });
            }}
            style={{
              fontSize: 11,
              padding: "2px 8px",
              borderRadius: 4,
              border: "1px solid var(--accent)",
              background: "transparent",
              color: "var(--accent)",
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            ↓ Snap to bottom
          </button>
        )}

        <button
          onClick={onClose}
          style={{
            fontSize: 16,
            background: "transparent",
            border: "none",
            color: "var(--text-dim)",
            cursor: "pointer",
            padding: "2px 6px",
          }}
          title="Close logs"
        >
          ✕
        </button>
      </div>

      {/* Log lines */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflowY: "auto",
          overflowX: "hidden",
          padding: "4px 16px",
          fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
          fontSize: 12,
          lineHeight: 1.6,
        }}
      >
        {filtered.length === 0 && (
          <div style={{ color: "var(--text-dim)", padding: "24px 0", textAlign: "center" }}>
            {logs.length === 0
              ? "Waiting for daemon logs…"
              : `No ${filter} logs yet.`}
          </div>
        )}

        {filtered.map((entry, i) => (
          <div key={i} style={{ display: "flex", gap: 10, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            <span style={{ color: "#555", flexShrink: 0 }}>{fmtTime(entry.ts)}</span>
            <span
              style={{
                color: levelColor[entry.level] ?? "#8c8c8c",
                flexShrink: 0,
                width: 52,
                textAlign: "right",
              }}
            >
              {entry.level}
            </span>
            <span style={{ color: "#666", flexShrink: 0 }}>[{entry.source}]</span>
            <span style={{ color: entry.level === "ERROR" ? "#ff6b6b" : "#c0c0c0" }}>
              {entry.message}
            </span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
