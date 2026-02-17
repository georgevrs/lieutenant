/* ── Transcript display — partial + final STT text ──────────────── */

import React from "react";

interface Props {
  partial: string;
  final_: string;
}

export function Transcript({ partial, final_ }: Props) {
  const text = final_ || partial;
  if (!text) return null;

  return (
    <div style={styles.container}>
      <span style={final_ ? styles.final : styles.partial}>
        {text}
        {!final_ && <span style={styles.cursor}>▎</span>}
      </span>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    textAlign: "center",
    padding: "8px 24px",
    minHeight: "36px",
  },
  partial: {
    fontFamily: "var(--font-sans)",
    fontSize: "18px",
    fontWeight: 300,
    color: "var(--text-dim)",
    fontStyle: "italic",
  },
  final: {
    fontFamily: "var(--font-sans)",
    fontSize: "18px",
    fontWeight: 400,
    color: "var(--text)",
  },
  cursor: {
    color: "var(--accent)",
    animation: "blink 1s step-end infinite",
    marginLeft: 2,
  },
};
