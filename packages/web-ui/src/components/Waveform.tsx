/* ── Waveform Canvas — majestic procedural visualization ────────── */

import { useRef, useEffect, useCallback } from "react";
import type { DaemonState } from "../types";

interface Props {
  state: DaemonState;
  micRms: number;
  ttsRms: number;
}

/* ── Constants ──────────────────────────────────────────────────── */
const NUM_POINTS = 200;
const SMOOTHING = 0.12;
const IDLE_AMP = 0.08;
const LISTEN_AMP_MIN = 0.06;
const LISTEN_AMP_MAX = 0.9;
const SPEAK_AMP_MIN = 0.06;
const SPEAK_AMP_MAX = 0.85;
const THINK_AMP = 0.04;

/* ── Colour palette per state ───────────────────────────────────── */
const COLORS: Record<DaemonState, { main: string; glow: string; bg: string }> = {
  IDLE: {
    main: "rgba(108, 99, 255, 0.6)",
    glow: "rgba(108, 99, 255, 0.15)",
    bg: "rgba(108, 99, 255, 0.03)",
  },
  LISTENING: {
    main: "rgba(74, 222, 128, 0.9)",
    glow: "rgba(74, 222, 128, 0.25)",
    bg: "rgba(74, 222, 128, 0.04)",
  },
  THINKING: {
    main: "rgba(251, 191, 36, 0.7)",
    glow: "rgba(251, 191, 36, 0.2)",
    bg: "rgba(251, 191, 36, 0.03)",
  },
  SPEAKING: {
    main: "rgba(108, 99, 255, 0.9)",
    glow: "rgba(108, 99, 255, 0.3)",
    bg: "rgba(108, 99, 255, 0.05)",
  },
};

export function Waveform({ state, micRms, ttsRms }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);

  /* Smoothed values (persist across frames) */
  const smoothRms = useRef(0);
  const targetAmp = useRef(IDLE_AMP);
  const currentAmp = useRef(IDLE_AMP);
  const phase = useRef(0);
  const noisePhase = useRef(0);

  /* ── Animation loop ────────────────────────────────────────────── */
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const W = rect.width;
    const H = rect.height;
    const midY = H / 2;

    /* ── Compute target amplitude ─────────────────────────────── */
    const rawRms =
      state === "LISTENING" ? micRms : state === "SPEAKING" ? ttsRms : 0;

    // Smooth the RMS
    smoothRms.current += (rawRms - smoothRms.current) * SMOOTHING;
    const sRms = smoothRms.current;

    switch (state) {
      case "IDLE":
        targetAmp.current =
          IDLE_AMP + 0.03 * Math.sin(Date.now() * 0.001);
        break;
      case "LISTENING":
        targetAmp.current =
          LISTEN_AMP_MIN + (LISTEN_AMP_MAX - LISTEN_AMP_MIN) * Math.min(sRms * 6, 1);
        break;
      case "THINKING":
        targetAmp.current =
          THINK_AMP + 0.02 * Math.sin(Date.now() * 0.002);
        break;
      case "SPEAKING":
        targetAmp.current =
          SPEAK_AMP_MIN + (SPEAK_AMP_MAX - SPEAK_AMP_MIN) * Math.min(sRms * 5, 1);
        break;
    }

    // Smooth amplitude
    currentAmp.current +=
      (targetAmp.current - currentAmp.current) * 0.08;
    const amp = currentAmp.current;

    /* ── Phase advancement ────────────────────────────────────── */
    const speed =
      state === "IDLE"
        ? 0.008
        : state === "LISTENING"
          ? 0.015 + sRms * 0.05
          : state === "THINKING"
            ? 0.005
            : 0.012 + sRms * 0.04;

    phase.current += speed;
    noisePhase.current += 0.003;

    /* ── Clear ────────────────────────────────────────────────── */
    const colors = COLORS[state];
    ctx.clearRect(0, 0, W, H);

    /* ── Draw multiple wave layers ────────────────────────────── */
    const layers = [
      { freqMul: 1.0, ampMul: 1.0, alpha: 1.0, width: 2.5 },
      { freqMul: 1.5, ampMul: 0.5, alpha: 0.4, width: 1.5 },
      { freqMul: 0.7, ampMul: 0.7, alpha: 0.3, width: 1.0 },
      { freqMul: 2.3, ampMul: 0.25, alpha: 0.2, width: 0.8 },
    ];

    for (const layer of layers) {
      ctx.beginPath();
      ctx.strokeStyle = colors.main.replace(
        /[\d.]+\)$/,
        `${layer.alpha})`
      );
      ctx.lineWidth = layer.width;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";

      for (let i = 0; i <= NUM_POINTS; i++) {
        const x = (i / NUM_POINTS) * W;
        const t = i / NUM_POINTS;

        // Envelope: tapers at edges
        const envelope = Math.sin(t * Math.PI);
        const envelopeSq = envelope * envelope;

        // Multi-sine wave
        const wave =
          Math.sin(t * Math.PI * 4 * layer.freqMul + phase.current) * 0.6 +
          Math.sin(t * Math.PI * 6 * layer.freqMul + phase.current * 1.3) * 0.25 +
          Math.sin(t * Math.PI * 10 * layer.freqMul + phase.current * 0.7) * 0.15;

        // Noise for organic feel
        const noise =
          Math.sin(t * 47 + noisePhase.current * 13) * 0.3 +
          Math.sin(t * 97 + noisePhase.current * 7) * 0.15;

        const y =
          midY +
          (wave + noise * (state === "LISTENING" ? 1.5 : 0.5)) *
            envelopeSq *
            amp *
            layer.ampMul *
            (H * 0.35);

        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }

    /* ── Glow effect ──────────────────────────────────────────── */
    const gradient = ctx.createRadialGradient(
      W / 2,
      midY,
      0,
      W / 2,
      midY,
      W * 0.4 * amp
    );
    gradient.addColorStop(0, colors.glow);
    gradient.addColorStop(1, "transparent");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, W, H);

    /* ── Center dot/ring for THINKING ─────────────────────────── */
    if (state === "THINKING") {
      const pulse = 0.5 + 0.5 * Math.sin(Date.now() * 0.003);
      const radius = 20 + pulse * 15;
      ctx.beginPath();
      ctx.arc(W / 2, midY, radius, 0, Math.PI * 2);
      ctx.strokeStyle = colors.main;
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    animRef.current = requestAnimationFrame(draw);
  }, [state, micRms, ttsRms]);

  useEffect(() => {
    animRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animRef.current);
  }, [draw]);

  return (
    <canvas
      ref={canvasRef}
      style={{
        width: "100%",
        height: "260px",
        display: "block",
      }}
    />
  );
}
