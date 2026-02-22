/* ── WebSocket hook — connects to voice-daemon ──────────────────── */

import { useCallback, useEffect, useRef, useState } from "react";
import type { DaemonState, WSMessage } from "../types";

const WS_URL = `ws://127.0.0.1:${import.meta.env.VITE_DAEMON_PORT ?? 8765}/ws`;
const RECONNECT_DELAY = 2000;

export interface LogEntry {
  ts: number;
  level: string;
  message: string;
  source: string;
}

const MAX_LOGS = 500;

export interface DaemonStore {
  state: DaemonState;
  connected: boolean;
  micRms: number;
  ttsRms: number;
  sttPartial: string;
  sttFinal: string;
  agentText: string;
  agentDone: boolean;
  error: string | null;
  logs: LogEntry[];
  language: string;
  simulateWake: () => void;
  killSwitch: () => void;
  setLanguage: (lang: string) => void;
}

export function useDaemon(): DaemonStore {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [state, setState] = useState<DaemonState>("IDLE");
  const [micRms, setMicRms] = useState(0);
  const [ttsRms, setTtsRms] = useState(0);
  const [sttPartial, setSttPartial] = useState("");
  const [sttFinal, setSttFinal] = useState("");
  const [agentText, setAgentText] = useState("");
  const [agentDone, setAgentDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [language, setLanguageState] = useState("el");

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setError(null);
      // Fetch current language
      fetch(
        `http://127.0.0.1:${import.meta.env.VITE_DAEMON_PORT ?? 8765}/control/language`
      )
        .then((r) => r.json())
        .then((d) => setLanguageState(d.language ?? "el"))
        .catch(() => {});
    };

    ws.onclose = () => {
      setConnected(false);
      setTimeout(connect, RECONNECT_DELAY);
    };

    ws.onerror = () => {
      setConnected(false);
    };

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data);
        switch (msg.type) {
          case "state": {
            const val = (msg as any).value as DaemonState;
            setState(val);
            // Reset on state transitions
            if (val === "LISTENING") {
              setSttPartial("");
              setSttFinal("");
              setAgentText("");
              setAgentDone(false);
            }
            if (val === "IDLE") {
              setTtsRms(0);
            }
            break;
          }
          case "mic.level":
            setMicRms((msg as any).rms);
            break;
          case "tts.level":
            setTtsRms((msg as any).rms);
            break;
          case "stt.partial":
            setSttPartial((msg as any).text);
            break;
          case "stt.final":
            setSttFinal((msg as any).text);
            setSttPartial("");
            break;
          case "agent.chunk":
            setAgentText((prev) => prev + (msg as any).text);
            break;
          case "agent.done":
            setAgentDone(true);
            break;
          case "error":
            setError((msg as any).message);
            break;
          case "language":
            setLanguageState((msg as any).value ?? "el");
            break;
          case "log": {
            const entry: LogEntry = {
              ts: (msg as any).ts ?? Date.now() / 1000,
              level: (msg as any).level ?? "INFO",
              message: (msg as any).message ?? "",
              source: (msg as any).source ?? "daemon",
            };
            setLogs((prev) => {
              const next = [...prev, entry];
              return next.length > MAX_LOGS ? next.slice(-MAX_LOGS) : next;
            });
            break;
          }
        }
      } catch {
        // ignore parse errors
      }
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  const httpPost = useCallback(async (path: string) => {
    try {
      await fetch(
        `http://127.0.0.1:${import.meta.env.VITE_DAEMON_PORT ?? 8765}${path}`,
        { method: "POST" }
      );
    } catch {
      // ignore
    }
  }, []);

  const simulateWake = useCallback(() => httpPost("/control/wake"), [httpPost]);
  const killSwitch = useCallback(() => httpPost("/control/stop"), [httpPost]);
  const setLanguage = useCallback(
    async (lang: string) => {
      try {
        const r = await fetch(
          `http://127.0.0.1:${import.meta.env.VITE_DAEMON_PORT ?? 8765}/control/language`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ language: lang }),
          }
        );
        const d = await r.json();
        setLanguageState(d.language ?? lang);
      } catch {
        // ignore
      }
    },
    []
  );

  return {
    state,
    connected,
    micRms,
    ttsRms,
    sttPartial,
    sttFinal,
    agentText,
    agentDone,
    error,
    logs,
    language,
    simulateWake,
    killSwitch,
    setLanguage,
  };
}
