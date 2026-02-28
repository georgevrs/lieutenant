/* ── Lieutenant — Main App ───────────────────────────────────────── */

import React, { useState } from "react";
import { useDaemon } from "./hooks/useDaemon";
import { Waveform } from "./components/Waveform";
import { StateIndicator } from "./components/StateIndicator";
import { ChatPanel } from "./components/ChatPanel";
import { Controls } from "./components/Controls";
import { Settings } from "./components/Settings";
import { LogPanel } from "./components/LogPanel";
import type { Lang } from "./i18n";
import { t } from "./i18n";

export function App() {
  const daemon = useDaemon();
  const APP_VERSION = (import.meta.env.VITE_APP_VERSION as string) || "1.0.0";
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const lang = daemon.language as Lang;

  return (
    <div style={styles.root}>
      {/* Background gradient */}
      <div style={styles.bg} />

      {/* Header */}
      <header style={styles.header}>
        <div style={styles.logo}>
          <span style={styles.logoIcon}>◆</span>
          <span style={styles.logoText}>LIEUTENANT</span>
        </div>
      </header>

      {/* Spacer to push waveform toward vertical center */}
      <div style={{ flex: 1 }} />

      {/* State indicator */}
      <StateIndicator
        state={daemon.state}
        connected={daemon.connected}
        llmBackend={daemon.llmBackend}
        language={lang}
      />

      {/* Waveform — vertically centered */}
      <div style={styles.waveformContainer}>
        <Waveform
          state={daemon.state}
          micRms={daemon.micRms}
          ttsRms={daemon.ttsRms}
        />
      </div>

      {/* Conversation — scrollable, below waveform */}
      <main style={styles.main}>
        <ChatPanel
          messages={daemon.chatMessages}
          sttPartial={daemon.sttPartial}
          language={lang}
          displayName={daemon.settings.display_name}
        />
      </main>

      {/* Error display */}
      {daemon.error && (
        <div style={styles.error}>⚠ {daemon.error}</div>
      )}

      {/* Controls */}
      <footer style={styles.footer}>
        <div style={styles.controlsRow}>
          <Controls
            state={daemon.state}
            language={daemon.language}
            onWake={daemon.simulateWake}
            onKill={daemon.killSwitch}
            onToggleSettings={() => setSettingsOpen(true)}
            onToggleLanguage={() =>
              daemon.setLanguage(daemon.language === "el" ? "en" : "el")
            }
          />
        </div>

        <div style={styles.footerMeta}>
          <span style={styles.version}>v{APP_VERSION}</span>
          <span style={styles.attribution}>
              Proudly presented by <a style={styles.link} href="https://github.com/georgevrs" target="_blank" rel="noopener noreferrer">George Verouchis</a>
          </span>
        </div>
      </footer>

      {/* Settings drawer */}
      <Settings
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        language={lang}
        settings={daemon.settings}
      />

      {/* Log panel */}
      <LogPanel open={logsOpen} logs={daemon.logs} onClose={() => setLogsOpen(false)} />

      {/* Log toggle floating button */}
      <button
        onClick={() => setLogsOpen((v) => !v)}
        style={{
          position: "fixed",
          bottom: 16,
          right: 16,
          zIndex: 90,
          width: 40,
          height: 40,
          borderRadius: "50%",
          border: "1px solid var(--border)",
          background: logsOpen ? "var(--accent)" : "var(--surface)",
          color: logsOpen ? "#fff" : "var(--text-dim)",
          fontSize: 18,
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          transition: "all 0.2s",
          boxShadow: logsOpen ? "0 0 12px var(--accent-glow)" : "none",
        }}
        title={logsOpen ? t("logs.hide", lang) : t("logs.show", lang)}
      >
        ⌸
      </button>

      {/* CSS animations */}
      <style>{`
        @keyframes blink {
          50% { opacity: 0; }
        }
      `}</style>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    height: "100%",
    display: "flex",
    flexDirection: "column",
    position: "relative",
    overflow: "hidden",
  },
  bg: {
    position: "absolute",
    inset: 0,
    background:
      "radial-gradient(ellipse at 50% 40%, rgba(108,99,255,0.06) 0%, transparent 60%)",
    pointerEvents: "none",
    zIndex: 0,
  },
  header: {
    position: "relative",
    zIndex: 1,
    display: "flex",
    justifyContent: "center",
    padding: "16px 24px 0",
    flexShrink: 0,
  },
  logo: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
  },
  logoIcon: {
    fontSize: "20px",
    color: "var(--accent)",
  },
  logoText: {
    fontFamily: "var(--font-mono)",
    fontSize: "12px",
    fontWeight: 500,
    letterSpacing: "0.3em",
    color: "var(--text-dim)",
  },
  main: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    position: "relative",
    zIndex: 1,
    minHeight: 0, /* allow flex child to shrink & scroll */
  },
  waveformContainer: {
    width: "100%",
    padding: "8px 24px",
    flexShrink: 0,
    position: "relative",
    zIndex: 1,
  },
  error: {
    textAlign: "center",
    padding: "8px 16px",
    fontFamily: "var(--font-mono)",
    fontSize: "12px",
    color: "var(--error)",
    background: "rgba(248, 113, 113, 0.1)",
    borderRadius: "6px",
    margin: "4px 24px",
    flexShrink: 0,
  },
  footer: {
    position: "relative",
    zIndex: 1,
    paddingBottom: "16px",
    flexShrink: 0,
  },
  controlsRow: {
    display: "flex",
    justifyContent: "center",
    padding: "8px 24px",
  },
  footerMeta: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: "6px",
    padding: "6px 24px 12px",
    color: "var(--text-dim)",
    fontSize: "12px",
  },
  version: {
    fontFamily: "var(--font-mono)",
    color: "var(--text-dim)",
  },
  attribution: {
    textAlign: "center",
  },
  link: {
    color: "var(--accent)",
    textDecoration: "none",
  },
};
