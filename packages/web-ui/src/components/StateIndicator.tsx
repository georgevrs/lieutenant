/* ── State Indicator — shows current daemon state ───────────────── */

import type { DaemonState } from "../types";
import React from "react";
import { t, type Lang } from "../i18n";

interface Props {
  state: DaemonState;
  connected: boolean;
  llmBackend?: string;
  language: Lang;
}

const STATE_KEYS: Record<DaemonState, string> = {
  IDLE: "state.idle",
  LISTENING: "state.listening",
  THINKING: "state.thinking",
  SPEAKING: "state.speaking",
  CONVERSING: "state.conversing",
};

const STATE_ICONS: Record<DaemonState, string> = {
  IDLE: "◉",
  LISTENING: "🎤",
  THINKING: "⚡",
  SPEAKING: "🔊",
  CONVERSING: "💬",
};

const STATE_COLORS: Record<DaemonState, string> = {
  IDLE: "var(--accent)",
  LISTENING: "var(--success)",
  THINKING: "var(--warning)",
  SPEAKING: "var(--accent)",
  CONVERSING: "var(--success)",
};

export function StateIndicator({ state, connected, llmBackend, language }: Props) {
  const label = t(STATE_KEYS[state] as any, language);
  const icon = STATE_ICONS[state];
  const color = STATE_COLORS[state];

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
      <span style={styles.icon}>{icon}</span>
      <span style={{ ...styles.label, color }}>{label}</span>

      {/* LLM Backend badge */}
      {llmBackend && llmBackend !== "\u2014" && (
        <span style={styles.badge}>{llmBackend.toUpperCase()}</span>
      )}
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
  badge: {
    fontFamily: "var(--font-mono)",
    fontSize: "9px",
    fontWeight: 600,
    letterSpacing: "0.08em",
    padding: "2px 6px",
    borderRadius: "3px",
    background: "rgba(108, 99, 255, 0.15)",
    color: "var(--accent)",
    border: "1px solid rgba(108, 99, 255, 0.3)",
    marginLeft: "4px",
  },
};
