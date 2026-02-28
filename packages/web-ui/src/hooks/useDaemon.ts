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
const MAX_CHAT = 200;

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  done: boolean;
  ts: number;
}

export interface DaemonSettings {
  wake_phrase_el: string;
  wake_phrase_en: string;
  display_name: string;
}

export interface DaemonStore {
  state: DaemonState;
  connected: boolean;
  micRms: number;
  ttsRms: number;
  sttPartial: string;
  sttFinal: string;
  agentText: string;
  agentDone: boolean;
  chatMessages: ChatMessage[];
  error: string | null;
  logs: LogEntry[];
  language: string;
  llmBackend: string;
  settings: DaemonSettings;
  simulateWake: () => void;
  killSwitch: () => void;
  setLanguage: (lang: string) => void;
}

export function useDaemon(): DaemonStore {
  const wsRef = useRef<WebSocket | null>(null);
  const disposedRef = useRef(false);
  const [connected, setConnected] = useState(false);
  const [state, setState] = useState<DaemonState>("IDLE");
  const [micRms, setMicRms] = useState(0);
  const [ttsRms, setTtsRms] = useState(0);
  const [sttPartial, setSttPartial] = useState("");
  const [sttFinal, setSttFinal] = useState("");
  const [agentText, setAgentText] = useState("");
  const [agentDone, setAgentDone] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const chatIdRef = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [language, setLanguageState] = useState("el");
  const [llmBackend, setLlmBackend] = useState("—");
  const [settings, setSettings] = useState<DaemonSettings>({
    wake_phrase_el: "υπολοχαγέ",
    wake_phrase_en: "lieutenant",
    display_name: "Lieutenant",
  });

  const connect = useCallback(() => {
    if (disposedRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    // Also skip if a connection is currently being established
    if (wsRef.current?.readyState === WebSocket.CONNECTING) return;

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
      if (!disposedRef.current) {
        setTimeout(connect, RECONNECT_DELAY);
      }
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
            if (val === "CONVERSING") {
              // Keep agent text visible during conversation follow-up window
              setSttPartial("");
              setSttFinal("");
              setTtsRms(0);
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
          case "stt.final": {
            const finalText = (msg as any).text as string;
            setSttFinal(finalText);
            setSttPartial("");
            // Add user message to chat
            if (finalText && finalText.trim()) {
              const id = ++chatIdRef.current;
              setChatMessages((prev) => {
                const next = [...prev, { id, role: "user" as const, text: finalText, done: true, ts: Date.now() }];
                return next.length > MAX_CHAT ? next.slice(-MAX_CHAT) : next;
              });
            }
            break;
          }
          case "agent.chunk":
            setAgentText((prev) => prev + (msg as any).text);
            // Append to or create assistant message
            setChatMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === "assistant" && !last.done) {
                // Append to existing assistant message
                const updated = { ...last, text: last.text + (msg as any).text };
                return [...prev.slice(0, -1), updated];
              }
              // Start new assistant message
              const id = ++chatIdRef.current;
              return [...prev, { id, role: "assistant" as const, text: (msg as any).text, done: false, ts: Date.now() }];
            });
            break;
          case "agent.done":
            setAgentDone(true);
            setChatMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === "assistant" && !last.done) {
                return [...prev.slice(0, -1), { ...last, done: true }];
              }
              return prev;
            });
            break;
          case "llm.backend":
            setLlmBackend((msg as any).name ?? "unknown");
            break;
          case "error":
            setError((msg as any).message);
            break;
          case "language":
            setLanguageState((msg as any).value ?? "el");
            break;
          case "settings": {
            const s = msg as any;
            setSettings({
              wake_phrase_el: s.wake_phrase_el ?? settings.wake_phrase_el,
              wake_phrase_en: s.wake_phrase_en ?? settings.wake_phrase_en,
              display_name: s.display_name ?? settings.display_name,
            });
            break;
          }
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
    disposedRef.current = false;
    connect();
    return () => {
      disposedRef.current = true;
      wsRef.current?.close();
      wsRef.current = null;
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
    chatMessages,
    error,
    logs,
    language,
    llmBackend,
    settings,
    simulateWake,
    killSwitch,
    setLanguage,
  };
}
