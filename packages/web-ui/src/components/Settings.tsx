/* ── Settings drawer ─────────────────────────────────────────────── */

import React from "react";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function Settings({ open, onClose }: Props) {
  if (!open) return null;

  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.drawer} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <span style={styles.title}>Ρυθμίσεις</span>
          <button onClick={onClose} style={styles.close}>
            ✕
          </button>
        </div>

        <div style={styles.section}>
          <div style={styles.sectionTitle}>Σύνδεση</div>
          <div style={styles.item}>
            <span style={styles.label}>Voice Daemon</span>
            <span style={styles.value}>ws://127.0.0.1:8765</span>
          </div>
          <div style={styles.item}>
            <span style={styles.label}>Agent Gateway</span>
            <span style={styles.value}>http://127.0.0.1:8800</span>
          </div>
        </div>

        <div style={styles.section}>
          <div style={styles.sectionTitle}>Backends</div>
          <div style={styles.item}>
            <span style={styles.label}>STT</span>
            <span style={styles.value}>local (faster-whisper)</span>
          </div>
          <div style={styles.item}>
            <span style={styles.label}>TTS</span>
            <span style={styles.value}>local (say / espeak)</span>
          </div>
          <div style={styles.item}>
            <span style={styles.label}>Agent</span>
            <span style={styles.value}>local-agent</span>
          </div>
        </div>

        <div style={styles.section}>
          <div style={styles.sectionTitle}>Ασφάλεια</div>
          <div style={styles.item}>
            <span style={styles.label}>SAFE_MODE</span>
            <span style={styles.value}>
              {import.meta.env.VITE_SAFE_MODE === "true" ? "✅ Ενεργό" : "❌ Ανενεργό"}
            </span>
          </div>
        </div>

        <div style={styles.footer}>
          Lieutenant v0.1.0 — Crafted with precision.
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.6)",
    backdropFilter: "blur(4px)",
    zIndex: 100,
    display: "flex",
    justifyContent: "flex-end",
  },
  drawer: {
    width: "340px",
    maxWidth: "90vw",
    background: "var(--bg-card)",
    borderLeft: "1px solid var(--border)",
    padding: "24px",
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
    gap: "20px",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  title: {
    fontFamily: "var(--font-sans)",
    fontSize: "18px",
    fontWeight: 600,
    color: "var(--text)",
  },
  close: {
    background: "none",
    border: "none",
    color: "var(--text-dim)",
    fontSize: "18px",
    cursor: "pointer",
  },
  section: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  sectionTitle: {
    fontFamily: "var(--font-mono)",
    fontSize: "11px",
    fontWeight: 500,
    color: "var(--accent)",
    letterSpacing: "0.1em",
    textTransform: "uppercase" as const,
    marginBottom: "4px",
  },
  item: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "6px 0",
    borderBottom: "1px solid var(--border)",
  },
  label: {
    fontFamily: "var(--font-sans)",
    fontSize: "13px",
    color: "var(--text-dim)",
  },
  value: {
    fontFamily: "var(--font-mono)",
    fontSize: "12px",
    color: "var(--text)",
  },
  footer: {
    marginTop: "auto",
    paddingTop: "16px",
    fontFamily: "var(--font-mono)",
    fontSize: "11px",
    color: "var(--text-muted)",
    textAlign: "center",
  },
};
