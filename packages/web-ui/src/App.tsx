/* ── Lieutenant — Main App ───────────────────────────────────────── */

import React, { useState } from "react";
import { useDaemon } from "./hooks/useDaemon";
import { Waveform } from "./components/Waveform";
import { StateIndicator } from "./components/StateIndicator";
import { Transcript } from "./components/Transcript";
import { AgentResponse } from "./components/AgentResponse";
import { Controls } from "./components/Controls";
import { Settings } from "./components/Settings";
import { LogPanel } from "./components/LogPanel";

export function App() {
  const daemon = useDaemon();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);

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

      {/* Main content */}
      <main style={styles.main}>
        {/* State indicator */}
        <StateIndicator state={daemon.state} connected={daemon.connected} />

        {/* Transcript (above waveform) */}
        <Transcript partial={daemon.sttPartial} final_={daemon.sttFinal} />

        {/* Waveform */}
        <div style={styles.waveformContainer}>
          <Waveform
            state={daemon.state}
            micRms={daemon.micRms}
            ttsRms={daemon.ttsRms}
          />
        </div>

        {/* Agent response (below waveform) */}
        <AgentResponse text={daemon.agentText} done={daemon.agentDone} />

        {/* Error display */}
        {daemon.error && (
          <div style={styles.error}>⚠ {daemon.error}</div>
        )}
      </main>

      {/* Controls */}
      <footer style={styles.footer}>
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
      </footer>

      {/* Settings drawer */}
      <Settings open={settingsOpen} onClose={() => setSettingsOpen(false)} />

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
        title={logsOpen ? "Hide Logs" : "Show Logs"}
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
    padding: "20px 24px 0",
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
    justifyContent: "center",
    alignItems: "stretch",
    position: "relative",
    zIndex: 1,
    padding: "0 24px",
  },
  waveformContainer: {
    width: "100%",
    padding: "8px 0",
  },
  error: {
    textAlign: "center",
    padding: "8px 16px",
    fontFamily: "var(--font-mono)",
    fontSize: "12px",
    color: "var(--error)",
    background: "rgba(248, 113, 113, 0.1)",
    borderRadius: "6px",
    margin: "8px auto",
    maxWidth: "600px",
  },
  footer: {
    position: "relative",
    zIndex: 1,
    paddingBottom: "16px",
  },
};
