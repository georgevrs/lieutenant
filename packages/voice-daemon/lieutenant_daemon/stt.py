"""STT engine — streaming speech-to-text with faster-whisper or Vosk."""

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
_MODEL_SIZE = os.getenv("STT_MODEL_SIZE", "base")


class STTResult:
    """Represents a partial or final STT result."""
    def __init__(self, text: str, is_final: bool = False):
        self.text = text
        self.is_final = is_final


class STTEngine:
    """
    Streaming STT using faster-whisper for best quality.
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

        # ── Adaptive silence detection (Siri-like) ─────────────────────
        self._silence_frames = 0
        self._max_silence = 25  # ~1.6s of silence after speech ends
        self._total_frames = 0
        self._max_total_frames = 250  # ~16s max utterance
        self._noise_floor: float = 0.0
        self._noise_samples: list[float] = []
        self._noise_calibrated = False
        self._speech_detected = False  # Siri-like: must hear speech first
        self._speech_frames = 0  # count frames with speech energy
        self._peak_rms: float = 0.0  # track loudest frame seen
        self._MIN_SPEECH_FRAMES = 3  # require at least ~0.2s of speech
        self._NOISE_CALIBRATION_FRAMES = 8  # first ~0.5s for calibration
        self._SILENCE_FACTOR = 4.0  # silence threshold = noise_floor * factor
        self._MIN_SILENCE_THRESHOLD = 0.002  # absolute minimum (was 0.015 — way too high)
        self._SPEECH_THRESHOLD_FACTOR = 6.0  # speech = noise_floor * this
        self._language = "el"  # current language for transcription

    @property
    def backend(self) -> str:
        return self._backend

    def preload(self):
        """Pre-load the whisper model so first wake is instant."""
        try:
            from faster_whisper import WhisperModel
            import pathlib
            
            # Try local model first
            local_model = pathlib.Path(__file__).parent.parent / "models" / "whisper-base"
            if local_model.exists():
                logger.info("Pre-loading local whisper model from %s…", local_model)
                self._model = WhisperModel(str(local_model), device="cpu", compute_type="int8")
                self._backend = "faster-whisper"
                logger.info("faster-whisper model ready (local).")
            else:
                logger.info("Pre-loading faster-whisper model '%s'…", _MODEL_SIZE)
                self._model = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
                self._backend = "faster-whisper"
                logger.info("faster-whisper model ready.")
        except ImportError:
            logger.warning("faster-whisper not installed, will try Vosk at runtime.")
        except Exception as e:
            logger.warning("Failed to pre-load whisper model: %s", e)

    def start_utterance(self, loop: asyncio.AbstractEventLoop, language: str = "el"):
        """Begin capturing an utterance."""
        self._active = True
        self._language = language
        self._audio_buffer.clear()
        self._silence_frames = 0
        self._total_frames = 0
        self._noise_samples.clear()
        self._noise_calibrated = False
        self._noise_floor = 0.0
        self._speech_detected = False
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
        # Drain pending audio frames so the thread doesn't process stale data
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

        # ── Adaptive silence detection ────────────────────────────────
        rms = float(np.sqrt(np.mean((audio.astype(np.float32) / 32768.0) ** 2)))

        # Calibrate noise floor from first N frames
        if not self._noise_calibrated:
            self._noise_samples.append(rms)
            if len(self._noise_samples) >= self._NOISE_CALIBRATION_FRAMES:
                self._noise_floor = float(np.median(self._noise_samples))
                self._noise_calibrated = True
                threshold = max(self._noise_floor * self._SILENCE_FACTOR, self._MIN_SILENCE_THRESHOLD)
                logger.info("Noise floor calibrated: %.4f (silence threshold: %.4f)", self._noise_floor, threshold)
            return  # don't check silence during calibration

        threshold = max(self._noise_floor * self._SILENCE_FACTOR, self._MIN_SILENCE_THRESHOLD)
        speech_threshold = max(self._noise_floor * self._SPEECH_THRESHOLD_FACTOR, self._MIN_SILENCE_THRESHOLD * 2)

        # Track peak RMS for adaptive thresholds
        if rms > self._peak_rms:
            self._peak_rms = rms

        # Track whether user has started speaking
        if rms >= speech_threshold:
            self._speech_frames += 1
            if self._speech_frames >= self._MIN_SPEECH_FRAMES and not self._speech_detected:
                self._speech_detected = True
                logger.info("Speech energy detected (rms=%.4f, threshold=%.4f, peak=%.4f)",
                            rms, speech_threshold, self._peak_rms)

        # Only count silence AFTER speech has been detected (Siri-like)
        if self._speech_detected:
            if rms < threshold:
                self._silence_frames += 1
            else:
                self._silence_frames = 0

    @property
    def silence_detected(self) -> bool:
        """True if speech was heard and then extended silence followed (Siri-like)."""
        return self._noise_calibrated and self._speech_detected and self._silence_frames > self._max_silence

    @property
    def max_duration_reached(self) -> bool:
        """True if we've been listening for too long."""
        return self._total_frames > self._max_total_frames

    @property
    def speech_was_detected(self) -> bool:
        """True if speech energy was detected during the current utterance."""
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
                    # If transcription thread is still running, wait for it
                    if self._thread and self._thread.is_alive():
                        continue  # keep waiting for the final result
                    return
                # Yield heartbeat so caller can check silence/timeout
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
        """Use faster-whisper for transcription."""
        from faster_whisper import WhisperModel
        import pathlib

        if self._model is not None:
            model = self._model
            logger.info("Using pre-loaded faster-whisper model.")
        else:
            self._backend = "faster-whisper"
            # Try local model first
            local_model = pathlib.Path(__file__).parent.parent / "models" / "whisper-base"
            if local_model.exists():
                logger.info("Loading local whisper model from %s…", local_model)
                model = WhisperModel(str(local_model), device="cpu", compute_type="int8")
            else:
                logger.info("Loading faster-whisper model '%s'…", _MODEL_SIZE)
                model = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
            self._model = model
            logger.info("faster-whisper model loaded.")

        # Collect all audio
        chunks: list[np.ndarray] = []
        partial_sent = ""
        last_partial_time = time.time()

        while True:
            try:
                frame = self._audio_queue.get(timeout=0.5)
                if frame is None:
                    break
                if not self._active:
                    break  # Bail out quickly if stopped
                chunks.append(frame)

                # Partial transcription every ~2s for faster feedback
                # Only transcribe if speech energy has been detected (avoid Whisper hallucinations on silence)
                total_samples = sum(len(c) for c in chunks)
                elapsed = time.time() - last_partial_time
                if self._speech_detected and total_samples >= SAMPLE_RATE * 2 and elapsed > 2:
                    audio_np = np.concatenate(chunks).astype(np.float32) / 32768.0
                    segments, _ = model.transcribe(
                        audio_np,
                        language=self._language,
                        beam_size=1,
                        best_of=1,
                        vad_filter=False,  # Disable VAD - it's too aggressive
                    )
                    text = " ".join(seg.text.strip() for seg in segments)
                    # Always update timer to prevent re-transcribing every frame
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

        # Final transcription on all audio
        if chunks:
            logger.info("Transcribing final audio (%.1fs)...", sum(len(c) for c in chunks) / SAMPLE_RATE)
            audio_np = np.concatenate(chunks).astype(np.float32) / 32768.0
            segments_iter, info = model.transcribe(
                audio_np,
                language=self._language,
                beam_size=2,  # Minimal beam for fastest transcription
                vad_filter=False,  # Disable VAD
            )
            # Filter out segments with high no-speech probability (hallucinations)
            good_segments = []
            for seg in segments_iter:
                if seg.no_speech_prob > 0.7:
                    logger.info("Filtered hallucinated segment (no_speech_prob=%.2f): '%s'", seg.no_speech_prob, seg.text.strip())
                    continue
                good_segments.append(seg.text.strip())
            final_text = " ".join(good_segments)

            # Detect repeated hallucination pattern (same phrase 3+ times)
            if final_text:
                words = final_text.split()
                if len(words) >= 6:
                    # Check if the text is just the same short phrase repeated
                    half = len(words) // 2
                    first_half = " ".join(words[:half])
                    second_half = " ".join(words[half:2*half])
                    if first_half == second_half:
                        logger.info("Filtered repetitive hallucination: '%s'", final_text[:80])
                        final_text = ""

            logger.info("STT final result: '%s'", final_text or "(empty)")
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
            from lieutenant_daemon.wake import WakeDetector
            # Reuse model discovery
            models_dir = __import__("pathlib").Path(__file__).resolve().parent.parent / "models"
            for d in models_dir.iterdir():
                if d.is_dir():
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

        # Final
        result = json.loads(rec.FinalResult())
        final_text = result.get("text", "")
        asyncio.run_coroutine_threadsafe(
            self._result_queue.put(STTResult(final_text or "(no speech)", is_final=True)),
            loop,
        )
