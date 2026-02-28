"""STT engine — streaming speech-to-text with faster-whisper (medium) + Silero VAD."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from typing import AsyncIterator, Callable, Awaitable

import numpy as np

logger = logging.getLogger("lieutenant-daemon")

SAMPLE_RATE = 16000
_STT_BACKEND = os.getenv("STT_BACKEND", "local")
_MODEL_SIZE = os.getenv("STT_MODEL_SIZE", "medium")

# NOTE: Do NOT use initial_prompt — it causes Whisper to hallucinate the prompt
# text itself on silence/quiet audio. language="el" is sufficient to force Greek.


class STTResult:
    """Represents a partial or final STT result."""
    def __init__(self, text: str, is_final: bool = False):
        self.text = text
        self.is_final = is_final


class STTEngine:
    """
    Streaming STT using faster-whisper (medium model) for best Greek quality.
    Uses Silero VAD for robust ML-based speech/silence boundary detection.
    Falls back to Vosk if faster-whisper unavailable.
    """

    def __init__(self):
        self._audio_buffer: list[np.ndarray] = []
        self._audio_queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._active = False
        self._result_queue: asyncio.Queue[STTResult] = asyncio.Queue()
        self._thread: threading.Thread | None = None
        self._backend = "none"
        self._model = None  # cached whisper model
        self._vad_model = None  # Silero VAD model

        # ── Silero VAD state ──────────────────────────────────────────
        self._vad_triggered = False   # True once VAD detected speech
        self._vad_silence_ms = 0      # consecutive silence milliseconds
        self._vad_speech_ms = 0       # total speech milliseconds
        self._VAD_SILENCE_THRESHOLD_MS = 800   # 0.8s silence after speech → end (snappy)
        self._VAD_MIN_SPEECH_MS = 150          # require ≥150ms speech
        self._VAD_PROB_THRESHOLD = 0.25        # Lowered from 0.5 — laptop mics are quiet

        # ── RMS fallback state (when Silero unavailable) ──────────────
        self._silence_frames = 0
        self._max_silence = 15             # ~1s at 64ms/frame (was 25)
        self._noise_floor: float = 0.0
        self._noise_samples: list[float] = []
        self._noise_calibrated = False
        self._speech_frames = 0
        self._peak_rms: float = 0.0
        self._MIN_SPEECH_FRAMES = 3
        self._NOISE_CALIBRATION_FRAMES = 8
        self._SILENCE_FACTOR = 4.0
        self._MIN_SILENCE_THRESHOLD = 0.002
        self._SPEECH_THRESHOLD_FACTOR = 6.0

        # ── Utterance limits ──────────────────────────────────────────
        self._total_frames = 0
        self._max_total_frames = 250  # ~16s max utterance
        self._speech_detected = False
        self._rms_speech_detected = False
        self._language = "el"
        self._listen_start_time = 0.0  # Track when listening started
        self._NO_SPEECH_TIMEOUT = 5.0  # If no speech after 5s, give up

    @property
    def backend(self) -> str:
        return self._backend

    def preload(self):
        """Pre-load Whisper model + Silero VAD so first wake is instant."""
        self._preload_vad()
        self._preload_whisper()

    def _preload_vad(self):
        """Pre-load Silero VAD model."""
        try:
            import torch
            torch.set_num_threads(1)
            model, _ = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                trust_repo=True,
            )
            self._vad_model = model
            logger.info("Silero VAD model loaded (ML-based speech detection).")
        except Exception as e:
            logger.warning("Silero VAD not available, falling back to RMS: %s", e)

    def _preload_whisper(self):
        """Pre-load the faster-whisper model."""
        try:
            from faster_whisper import WhisperModel
            import pathlib

            local_model = pathlib.Path(__file__).parent.parent / "models" / f"whisper-{_MODEL_SIZE}"
            if local_model.exists():
                logger.info("Pre-loading local whisper model from %s…", local_model)
                self._model = WhisperModel(str(local_model), device="cpu", compute_type="int8")
            else:
                logger.info("Pre-loading faster-whisper '%s' (first time may download ~1.5 GB)…", _MODEL_SIZE)
                self._model = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
            self._backend = "faster-whisper"
            logger.info("faster-whisper '%s' ready.", _MODEL_SIZE)
        except ImportError:
            logger.warning("faster-whisper not installed, will try Vosk at runtime.")
        except Exception as e:
            logger.warning("Failed to pre-load whisper model: %s", e)

    def start_utterance(self, loop: asyncio.AbstractEventLoop, language: str = "el"):
        """Begin capturing an utterance."""
        self._active = True
        self._language = language
        self._audio_buffer.clear()
        self._total_frames = 0
        self._speech_detected = False
        self._rms_speech_detected = False
        self._listen_start_time = time.time()

        # Reset VAD state
        self._vad_triggered = False
        self._vad_silence_ms = 0
        self._vad_speech_ms = 0
        if self._vad_model is not None:
            try:
                self._vad_model.reset_states()
            except Exception:
                pass

        # Reset RMS fallback state
        self._silence_frames = 0
        self._noise_samples = []
        self._noise_calibrated = False
        self._noise_floor = 0.0
        self._speech_frames = 0
        self._peak_rms = 0.0

        # Clear queues
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        self._result_queue = asyncio.Queue()
        self._thread = threading.Thread(
            target=self._transcribe_loop, args=(loop,), daemon=True
        )
        self._thread.start()

    def stop_utterance(self):
        """Signal end of utterance — drain queue so transcription thread exits fast."""
        self._active = False
        drained = 0
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
                drained += 1
            except queue.Empty:
                break
        if drained:
            logger.info("Drained %d queued audio frames", drained)
        self._audio_queue.put(None)  # Sentinel

    def feed_audio(self, audio: np.ndarray):
        """Feed audio frames during listening."""
        if not self._active:
            return
        self._audio_buffer.append(audio.copy())
        self._audio_queue.put(audio.copy())
        self._total_frames += 1

        # ── Speech/silence detection — use BOTH Silero + RMS ─────────────
        if self._vad_model is not None:
            self._run_vad(audio)
        # Always run RMS as backup / co-signal
        self._run_rms_vad(audio)

        # If either detector found speech, mark it
        if not self._speech_detected and self._rms_speech_detected:
            self._speech_detected = True
            self._vad_triggered = True
            logger.info("RMS fallback promoted speech_detected (Silero missed it)")

    def _run_vad(self, audio: np.ndarray):
        """Run Silero VAD on the audio chunk.

        Silero VAD at 16kHz accepts chunk sizes: 256, 512, 768, 1024, 1536.
        Our BLOCK_SIZE=1024, so we feed the whole frame directly.
        We also apply gain boost so quiet laptop mics register properly.
        """
        import torch

        audio_f32 = audio.astype(np.float32) / 32768.0

        # ── Auto-gain: boost quiet audio so Silero can detect speech ──
        peak = np.max(np.abs(audio_f32))
        if peak > 0:
            # Target peak of 0.9 but cap gain at 30x to avoid amplifying pure noise
            gain = min(0.9 / peak, 30.0)
            if gain > 1.5:  # Only boost if meaningfully quiet
                audio_f32 = audio_f32 * gain

        tensor = torch.from_numpy(audio_f32)

        try:
            speech_prob = self._vad_model(tensor, SAMPLE_RATE).item()
        except Exception as e:
            logger.debug("Silero VAD error: %s", e)
            return

        chunk_ms = len(audio) * 1000 // SAMPLE_RATE  # 64ms per 1024 samples

        if speech_prob >= self._VAD_PROB_THRESHOLD:
            self._vad_speech_ms += chunk_ms
            self._vad_silence_ms = 0
            if not self._speech_detected and self._vad_speech_ms >= self._VAD_MIN_SPEECH_MS:
                self._speech_detected = True
                self._vad_triggered = True
                logger.info("VAD: speech onset (prob=%.2f, speech_ms=%d)",
                            speech_prob, self._vad_speech_ms)
        else:
            if self._vad_triggered:
                self._vad_silence_ms += chunk_ms

    def _run_rms_vad(self, audio: np.ndarray):
        """RMS energy-based speech detection — always runs alongside Silero."""
        rms = float(np.sqrt(np.mean((audio.astype(np.float32) / 32768.0) ** 2)))

        if not self._noise_calibrated:
            self._noise_samples.append(rms)
            if len(self._noise_samples) >= self._NOISE_CALIBRATION_FRAMES:
                self._noise_floor = float(np.median(self._noise_samples))
                self._noise_calibrated = True
                threshold = max(self._noise_floor * self._SILENCE_FACTOR, self._MIN_SILENCE_THRESHOLD)
                logger.info("Noise floor calibrated: %.4f (threshold: %.4f)", self._noise_floor, threshold)
            return

        threshold = max(self._noise_floor * self._SILENCE_FACTOR, self._MIN_SILENCE_THRESHOLD)
        speech_threshold = max(self._noise_floor * self._SPEECH_THRESHOLD_FACTOR, self._MIN_SILENCE_THRESHOLD * 2)

        if rms > self._peak_rms:
            self._peak_rms = rms

        if rms >= speech_threshold:
            self._speech_frames += 1
            if self._speech_frames >= self._MIN_SPEECH_FRAMES and not self._rms_speech_detected:
                self._rms_speech_detected = True
                logger.info("RMS: speech detected (rms=%.4f, threshold=%.4f, peak=%.4f)",
                            rms, speech_threshold, self._peak_rms)

        if self._rms_speech_detected:
            if rms < threshold:
                self._silence_frames += 1
            else:
                self._silence_frames = 0

    @property
    def silence_detected(self) -> bool:
        """True if speech was heard and then extended silence followed."""
        # Silero VAD check
        if self._vad_model is not None and self._vad_triggered:
            if self._vad_silence_ms >= self._VAD_SILENCE_THRESHOLD_MS:
                return True
        # RMS check
        if self._noise_calibrated and self._rms_speech_detected and self._silence_frames > self._max_silence:
            return True
        # No-speech timeout — if nothing detected after N seconds, end
        if not self._speech_detected and self._listen_start_time > 0:
            elapsed = time.time() - self._listen_start_time
            if elapsed > self._NO_SPEECH_TIMEOUT:
                logger.info("No speech timeout (%.1fs) — ending utterance.", elapsed)
                return True
        return False

    @property
    def max_duration_reached(self) -> bool:
        return self._total_frames > self._max_total_frames

    @property
    def speech_was_detected(self) -> bool:
        return self._speech_detected

    async def results(self) -> AsyncIterator[STTResult]:
        """Async generator yielding partial and final results.
        Yields empty heartbeat results on timeout so callers can check silence."""
        while True:
            try:
                result = await asyncio.wait_for(self._result_queue.get(), timeout=0.1)
                yield result
                if result.is_final:
                    return
            except asyncio.TimeoutError:
                if not self._active and self._result_queue.empty():
                    if self._thread and self._thread.is_alive():
                        continue
                    return
                yield STTResult("", is_final=False)

    def _transcribe_loop(self, loop: asyncio.AbstractEventLoop):
        """Background thread: accumulate audio and transcribe."""
        try:
            self._transcribe_whisper(loop)
        except ImportError:
            logger.warning("faster-whisper not available, trying Vosk STT")
            try:
                self._transcribe_vosk(loop)
            except ImportError:
                logger.error("No STT backend available!")
                asyncio.run_coroutine_threadsafe(
                    self._result_queue.put(STTResult("(STT unavailable)", is_final=True)),
                    loop,
                )

    def _transcribe_whisper(self, loop: asyncio.AbstractEventLoop):
        """Use faster-whisper (medium) with Greek-optimized settings."""
        from faster_whisper import WhisperModel
        import pathlib

        if self._model is not None:
            model = self._model
            logger.info("Using pre-loaded faster-whisper '%s'.", _MODEL_SIZE)
        else:
            self._backend = "faster-whisper"
            local_model = pathlib.Path(__file__).parent.parent / "models" / f"whisper-{_MODEL_SIZE}"
            if local_model.exists():
                model = WhisperModel(str(local_model), device="cpu", compute_type="int8")
            else:
                model = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
            self._model = model

        # Collect audio
        chunks: list[np.ndarray] = []
        partial_sent = ""
        last_partial_time = time.time()

        while True:
            try:
                frame = self._audio_queue.get(timeout=0.5)
                if frame is None:
                    break
                if not self._active:
                    break
                chunks.append(frame)

                # Partial transcription every ~1.5s for responsive feedback
                total_samples = sum(len(c) for c in chunks)
                elapsed = time.time() - last_partial_time
                if self._speech_detected and total_samples >= SAMPLE_RATE * 1.5 and elapsed > 1.5:
                    audio_np = np.concatenate(chunks).astype(np.float32) / 32768.0
                    # Auto-gain for partials too
                    p = float(np.max(np.abs(audio_np)))
                    if p > 0.001:
                        g = min(0.8 / p, 50.0)
                        if g > 1.5:
                            audio_np = np.clip(audio_np * g, -1.0, 1.0)
                    segments, _ = model.transcribe(
                        audio_np,
                        language=self._language,
                        beam_size=1,
                        best_of=1,
                        vad_filter=False,
                        condition_on_previous_text=False,
                    )
                    text = " ".join(seg.text.strip() for seg in segments)
                    last_partial_time = time.time()
                    if text and text != partial_sent:
                        partial_sent = text
                        logger.info("STT partial: %s", text)
                        asyncio.run_coroutine_threadsafe(
                            self._result_queue.put(STTResult(text, is_final=False)),
                            loop,
                        )
            except queue.Empty:
                if not self._active:
                    break
                continue

        # ── Final transcription with fast settings ─────────────────
        if chunks:
            audio_duration = sum(len(c) for c in chunks) / SAMPLE_RATE
            audio_np = np.concatenate(chunks).astype(np.float32) / 32768.0

            # ── Normalize audio amplitude ─────────────────────────────
            peak = float(np.max(np.abs(audio_np)))
            if peak > 0.001:
                gain = min(0.8 / peak, 50.0)
                if gain > 1.5:
                    logger.info("Audio auto-gain: %.1fx (peak was %.4f)", gain, peak)
                    audio_np = np.clip(audio_np * gain, -1.0, 1.0)

            t_start = time.time()
            logger.info("Transcribing final audio (%.1fs) with beam_size=1…", audio_duration)

            segments_iter, info = model.transcribe(
                audio_np,
                language=self._language,
                beam_size=1,
                best_of=1,
                vad_filter=False,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
            )

            good_segments = []
            for seg in segments_iter:
                if seg.no_speech_prob > 0.6:
                    logger.info("Filtered hallucination (no_speech=%.2f): '%s'",
                                seg.no_speech_prob, seg.text.strip())
                    continue
                good_segments.append(seg.text.strip())

            final_text = " ".join(good_segments)

            # Detect repeated hallucination pattern
            if final_text:
                words = final_text.split()
                if len(words) >= 6:
                    half = len(words) // 2
                    first_half = " ".join(words[:half])
                    second_half = " ".join(words[half:2*half])
                    if first_half == second_half:
                        logger.info("Filtered repetitive hallucination: '%s'", final_text[:80])
                        final_text = ""

            t_elapsed = time.time() - t_start
            logger.info("STT final: '%s' (took %.1fs)", final_text or "(empty)", t_elapsed)
            asyncio.run_coroutine_threadsafe(
                self._result_queue.put(STTResult(final_text or "(no speech detected)", is_final=True)),
                loop,
            )
        else:
            asyncio.run_coroutine_threadsafe(
                self._result_queue.put(STTResult("", is_final=True)),
                loop,
            )

    def _transcribe_vosk(self, loop: asyncio.AbstractEventLoop):
        """Fallback: use Vosk for streaming STT."""
        from vosk import Model, KaldiRecognizer, SetLogLevel

        SetLogLevel(-1)
        self._backend = "vosk"

        model_path = os.getenv("VOSK_MODEL_PATH", "")
        if not model_path:
            models_dir = __import__("pathlib").Path(__file__).resolve().parent.parent / "models"
            for d in models_dir.iterdir():
                if d.is_dir() and "whisper" not in d.name.lower():
                    model_path = str(d)
                    break

        if not model_path:
            raise ImportError("No Vosk model found")

        model = Model(model_path)
        rec = KaldiRecognizer(model, SAMPLE_RATE)
        rec.SetWords(False)

        while True:
            try:
                frame = self._audio_queue.get(timeout=0.5)
                if frame is None:
                    break
                data = frame.tobytes()

                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text = result.get("text", "")
                    if text:
                        asyncio.run_coroutine_threadsafe(
                            self._result_queue.put(STTResult(text, is_final=False)),
                            loop,
                        )
                else:
                    partial = json.loads(rec.PartialResult())
                    text = partial.get("partial", "")
                    if text:
                        asyncio.run_coroutine_threadsafe(
                            self._result_queue.put(STTResult(text, is_final=False)),
                            loop,
                        )
            except queue.Empty:
                if not self._active:
                    break
                continue

        result = json.loads(rec.FinalResult())
        final_text = result.get("text", "")
        asyncio.run_coroutine_threadsafe(
            self._result_queue.put(STTResult(final_text or "(no speech)", is_final=True)),
            loop,
        )
