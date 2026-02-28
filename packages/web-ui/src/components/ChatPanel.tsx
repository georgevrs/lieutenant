/* ── ChatPanel — plain-text scrollable conversation ─────────────── */

import React, { useRef, useEffect } from "react";
import type { ChatMessage } from "../hooks/useDaemon";
import { t, type Lang } from "../i18n";

/** Strip markdown formatting so assistant text reads as plain prose. */
function stripMd(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*\*(.+?)\*\*\*/g, "$1")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/__(.+?)__/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/_(.+?)_/g, "$1")
    .replace(/~~(.+?)~~/g, "$1")
    .replace(/^\s*>+\s?/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+[.)]\s*/gm, "")
    .replace(/^-{3,}$/gm, "")
    // remove standalone emoji
    .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]+/gu, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

interface Props {
  messages: ChatMessage[];
  sttPartial: string;
  language: Lang;
  displayName?: string;
}

export function ChatPanel({ messages, sttPartial, language, displayName }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Smooth auto-scroll on new content
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sttPartial]);

  const hasContent = messages.length > 0 || sttPartial;

  return (
    <div style={styles.container}>
      {!hasContent && (
        <div style={styles.placeholder}>
          {t("chat.placeholder", language)}
        </div>
      )}

      {messages.map((msg) => (
        <div key={msg.id} style={styles.entry}>
          <span style={msg.role === "user" ? styles.labelUser : styles.labelAssistant}>
            {msg.role === "user"
              ? t("chat.you", language)
              : displayName || t("chat.lieutenant", language)}
          </span>
          <span style={styles.text}>
            {msg.role === "assistant" ? stripMd(msg.text) : msg.text}
            {msg.role === "assistant" && !msg.done && (
              <span style={styles.cursor}>▎</span>
            )}
          </span>
        </div>
      ))}

      {/* Live STT partial */}
      {sttPartial && (
        <div style={styles.entry}>
          <span style={styles.labelUser}>{t("chat.you", language)}</span>
          <span style={{ ...styles.text, fontStyle: "italic", color: "var(--text-dim)" }}>
            {sttPartial}
            <span style={styles.cursor}>▎</span>
          </span>
        </div>
      )}

      {/* Scroll anchor */}
      <div ref={bottomRef} style={{ height: 1 }} />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    flex: 1,
    overflowY: "auto",
    padding: "12px 32px",
    display: "flex",
    flexDirection: "column",
    gap: "14px",
    scrollBehavior: "smooth",
  },
  placeholder: {
    textAlign: "center",
    fontFamily: "var(--font-sans)",
    fontSize: "13px",
    color: "var(--text-muted)",
    padding: "24px 0",
    letterSpacing: "0.02em",
  },
  entry: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "2px",
  },
  labelUser: {
    fontFamily: "var(--font-mono)",
    fontSize: "10px",
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color: "var(--accent)",
  },
  labelAssistant: {
    fontFamily: "var(--font-mono)",
    fontSize: "10px",
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color: "var(--success)",
  },
  text: {
    fontFamily: "var(--font-sans)",
    fontSize: "15px",
    fontWeight: 300,
    lineHeight: 1.7,
    color: "var(--text)",
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
  },
  cursor: {
    color: "var(--accent)",
    animation: "blink 1s step-end infinite",
    marginLeft: 2,
  },
};
