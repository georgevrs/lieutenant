/* ── State Indicator — shows current daemon state ───────────────── */

import type { DaemonState } from "../types";
import React from "react";

interface Props {
  state: DaemonState;
  connected: boolean;
}

const STATE_META: Record<
  DaemonState,
  { label: string; icon: string; color: string }
> = {
  IDLE: { label: "Αναμονή", icon: "◉", color: "var(--accent)" },
  LISTENING: { label: "Ακούω…", icon: "🎤", color: "var(--success)" },
  THINKING: { label: "Σκέφτομαι…", icon: "⚡", color: "var(--warning)" },
  SPEAKING: { label: "Μιλάω…", icon: "🔊", color: "var(--accent)" },
};

export function StateIndicator({ state, connected }: Props) {
  const meta = STATE_META[state];

  return (
    <div style={styles.container}>
      {/* Connection dot */}
      <span
        style={{
          ...styles.dot,
          background: connected ? "var(--success)" : "var(--error)",
          boxShadow: connected
            ? "0 0 8px var(--success)"
            : "0 0 8px var(--error)",
        }}
      />

      {/* State */}
      <span style={{ ...styles.icon }}>{meta.icon}</span>
      <span
        style={{
          ...styles.label,
          color: meta.color,
        }}
      >
        {meta.label}
      </span>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    justifyContent: "center",
    padding: "12px 0",
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: "50%",
    display: "inline-block",
  },
  icon: {
    fontSize: "18px",
  },
  label: {
    fontFamily: "var(--font-sans)",
    fontSize: "14px",
    fontWeight: 500,
    letterSpacing: "0.05em",
    textTransform: "uppercase" as const,
  },
};
