/* ── Types for Lieutenant UI ─────────────────────────────────────── */

export type DaemonState = "IDLE" | "LISTENING" | "THINKING" | "SPEAKING" | "CONVERSING";

export interface WSMessage {
  type: string;
  ts?: number;
  [key: string]: unknown;
}

export interface StateMsg extends WSMessage {
  type: "state";
  value: DaemonState;
}

export interface MicLevelMsg extends WSMessage {
  type: "mic.level";
  rms: number;
}

export interface STTPartialMsg extends WSMessage {
  type: "stt.partial";
  text: string;
}

export interface STTFinalMsg extends WSMessage {
  type: "stt.final";
  text: string;
}

export interface AgentChunkMsg extends WSMessage {
  type: "agent.chunk";
  text: string;
}

export interface AgentDoneMsg extends WSMessage {
  type: "agent.done";
}

export interface TTSLevelMsg extends WSMessage {
  type: "tts.level";
  rms: number;
}

export interface ErrorMsg extends WSMessage {
  type: "error";
  message: string;
}

export interface LogMsg extends WSMessage {
  type: "log";
  level: string;
  message: string;
  source: string;
}
