/* ── Controls — Kill switch + Wake button + Language toggle ──────── */

import React from "react";
import type { DaemonState } from "../types";

interface Props {
  state: DaemonState;
  language: string;
  onWake: () => void;
  onKill: () => void;
  onToggleSettings: () => void;
  onToggleLanguage: () => void;
}

export function Controls({ state, language, onWake, onKill, onToggleSettings, onToggleLanguage }: Props) {
  const isGreek = language === "el";

  return (
    <div style={styles.bar}>
      {/* Settings */}
      <button onClick={onToggleSettings} style={styles.btnSmall} title={isGreek ? "Ρυθμίσεις" : "Settings"}>
        ⚙
      </button>

      {/* Language toggle */}
      <button
        onClick={onToggleLanguage}
        style={styles.btnSmall}
        title={isGreek ? "Switch to English" : "Αλλαγή σε Ελληνικά"}
      >
        {isGreek ? "🇬🇷" : "🇬🇧"}
      </button>

      {/* Wake / PTT */}
      <button
        onClick={onWake}
        style={{
          ...styles.btn,
          ...(state === "IDLE" ? styles.btnPrimary : styles.btnDisabled),
        }}
        disabled={state !== "IDLE"}
      >
        {state === "IDLE"
          ? isGreek
            ? "🎤  Υπολοχαγέ"
            : "🎤  Lieutenant"
          : state === "LISTENING"
            ? isGreek ? "Ακούω…" : "Listening…"
            : "…"}
      </button>

      {/* Kill switch */}
      <button
        onClick={onKill}
        style={{
          ...styles.btn,
          ...styles.btnDanger,
          opacity: state === "IDLE" ? 0.3 : 1,
        }}
        disabled={state === "IDLE"}
        title="Kill Switch"
      >
        ■ Stop
      </button>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "12px",
    padding: "16px",
  },
  btn: {
    fontFamily: "var(--font-sans)",
    fontSize: "13px",
    fontWeight: 500,
    padding: "10px 24px",
    borderRadius: "8px",
    border: "1px solid var(--border)",
    cursor: "pointer",
    transition: "all 0.2s ease",
    color: "var(--text)",
    background: "var(--surface)",
  },
  btnPrimary: {
    background: "var(--accent)",
    border: "1px solid var(--accent)",
    color: "#fff",
    boxShadow: "0 0 20px var(--accent-glow)",
  },
  btnDanger: {
    background: "rgba(248, 113, 113, 0.15)",
    color: "var(--error)",
  },
  btnDisabled: {
    opacity: 0.5,
    cursor: "not-allowed",
  },
  btnSmall: {
    fontFamily: "var(--font-sans)",
    fontSize: "18px",
    padding: "8px 12px",
    borderRadius: "8px",
    border: "1px solid var(--border)",
    cursor: "pointer",
    color: "var(--text-dim)",
    background: "var(--surface)",
  },
};
