/* ── Settings drawer — wake words + display name ─────────────────── */

import React, { useEffect, useRef, useState } from "react";
import { t, type Lang } from "../i18n";

const DAEMON =
  `http://127.0.0.1:${import.meta.env.VITE_DAEMON_PORT ?? 8765}`;

interface Props {
  open: boolean;
  onClose: () => void;
  language: Lang;
  /** Externally-pushed settings from WS */
  settings?: { wake_phrase_el: string; wake_phrase_en: string; display_name: string };
}

export function Settings({ open, onClose, language, settings: pushed }: Props) {
  const [wakeEl, setWakeEl] = useState("υπολοχαγέ");
  const [wakeEn, setWakeEn] = useState("lieutenant");
  const [displayName, setDisplayName] = useState("Lieutenant");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const loaded = useRef(false);

  /* Fetch settings on first open */
  useEffect(() => {
    if (!open) return;
    if (loaded.current) return;
    loaded.current = true;
    fetch(`${DAEMON}/control/settings`)
      .then((r) => r.json())
      .then((d) => {
        if (d.wake_phrase_el) setWakeEl(d.wake_phrase_el);
        if (d.wake_phrase_en) setWakeEn(d.wake_phrase_en);
        if (d.display_name) setDisplayName(d.display_name);
      })
      .catch(() => {});
  }, [open]);

  /* Sync from WS push */
  useEffect(() => {
    if (pushed) {
      if (pushed.wake_phrase_el) setWakeEl(pushed.wake_phrase_el);
      if (pushed.wake_phrase_en) setWakeEn(pushed.wake_phrase_en);
      if (pushed.display_name) setDisplayName(pushed.display_name);
    }
  }, [pushed]);

  const save = async () => {
    setSaving(true);
    setSaved(false);
    try {
      await fetch(`${DAEMON}/control/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          wake_phrase_el: wakeEl.trim(),
          wake_phrase_en: wakeEn.trim(),
          display_name: displayName.trim(),
        }),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch { /* ignore */ }
    setSaving(false);
  };

  if (!open) return null;

  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.drawer} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div style={styles.header}>
          <span style={styles.title}>{t("settings.title", language)}</span>
          <button onClick={onClose} style={styles.close}>✕</button>
        </div>

        {/* Wake words */}
        <div style={styles.section}>
          <div style={styles.sectionTitle}>{t("settings.wakeWords", language)}</div>

          <label style={styles.fieldLabel}>{t("settings.wakeEl", language)}</label>
          <input
            style={styles.input}
            value={wakeEl}
            onChange={(e) => setWakeEl(e.target.value)}
            placeholder="υπολοχαγέ"
          />

          <label style={styles.fieldLabel}>{t("settings.wakeEn", language)}</label>
          <input
            style={styles.input}
            value={wakeEn}
            onChange={(e) => setWakeEn(e.target.value)}
            placeholder="lieutenant"
          />
        </div>

        {/* Display name */}
        <div style={styles.section}>
          <div style={styles.sectionTitle}>{t("settings.display", language)}</div>

          <label style={styles.fieldLabel}>{t("settings.chatName", language)}</label>
          <input
            style={styles.input}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Lieutenant"
          />
        </div>

        {/* Save */}
        <button style={styles.saveBtn} onClick={save} disabled={saving}>
          {saving
            ? t("settings.saving", language)
            : saved
              ? t("settings.saved", language)
              : t("settings.save", language)}
        </button>

        {/* Info section */}
        <div style={styles.section}>
          <div style={styles.sectionTitle}>{t("settings.connection", language)}</div>
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
            <span style={styles.value}>faster-whisper (medium)</span>
          </div>
          <div style={styles.item}>
            <span style={styles.label}>TTS</span>
            <span style={styles.value}>edge-tts (neural)</span>
          </div>
        </div>

        <div style={styles.footer}>
          Lieutenant v0.2.0 — Crafted with precision.
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
    width: "360px",
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
  fieldLabel: {
    fontFamily: "var(--font-sans)",
    fontSize: "12px",
    color: "var(--text-dim)",
    marginBottom: "-4px",
  },
  input: {
    fontFamily: "var(--font-mono)",
    fontSize: "13px",
    padding: "8px 12px",
    borderRadius: "6px",
    border: "1px solid var(--border)",
    background: "var(--bg-deep, #0a0a12)",
    color: "var(--text)",
    outline: "none",
    transition: "border-color 0.2s",
  },
  saveBtn: {
    fontFamily: "var(--font-mono)",
    fontSize: "13px",
    padding: "10px 0",
    borderRadius: "8px",
    border: "1px solid var(--accent)",
    background: "rgba(108,99,255,0.12)",
    color: "var(--accent)",
    cursor: "pointer",
    fontWeight: 600,
    letterSpacing: "0.05em",
    transition: "all 0.2s",
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
