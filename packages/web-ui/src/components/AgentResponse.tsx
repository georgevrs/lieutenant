/* ── Agent Response — streaming assistant text ──────────────────── */

import React, { useRef, useEffect } from "react";

interface Props {
  text: string;
  done: boolean;
}

export function AgentResponse({ text, done }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [text]);

  if (!text) return null;

  return (
    <div style={styles.container} ref={ref}>
      <div style={styles.label}>Υπολοχαγός</div>
      <div style={styles.text}>
        {text}
        {!done && <span style={styles.cursor}>▎</span>}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    maxHeight: "200px",
    overflowY: "auto",
    padding: "16px 32px",
    textAlign: "center",
  },
  label: {
    fontFamily: "var(--font-mono)",
    fontSize: "11px",
    fontWeight: 500,
    color: "var(--accent)",
    letterSpacing: "0.1em",
    textTransform: "uppercase" as const,
    marginBottom: "8px",
  },
  text: {
    fontFamily: "var(--font-sans)",
    fontSize: "16px",
    fontWeight: 300,
    lineHeight: 1.7,
    color: "var(--text)",
    whiteSpace: "pre-wrap" as const,
  },
  cursor: {
    color: "var(--accent)",
    animation: "blink 1s step-end infinite",
    marginLeft: 2,
  },
};
