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

        # ── Adaptive silence detection ────────────────────────────────
        self._silence_frames = 0
        self._max_silence = 25  # ~1.6s of silence at 16kHz/1024 blocks
        self._total_frames = 0
        self._max_total_frames = 240  # ~15s max utterance
        self._noise_floor: float = 0.0
        self._noise_samples: list[float] = []
        self._noise_calibrated = False
        self._NOISE_CALIBRATION_FRAMES = 8  # first ~0.5s
        self._SILENCE_FACTOR = 1.3  # silence threshold = noise_floor * factor
        self._MIN_SILENCE_THRESHOLD = 0.015  # absolute minimum

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

    def start_utterance(self, loop: asyncio.AbstractEventLoop):
        """Begin capturing an utterance."""
        self._active = True
        self._audio_buffer.clear()
        self._silence_frames = 0
        self._total_frames = 0
        self._noise_samples.clear()
        self._noise_calibrated = False
        self._noise_floor = 0.0
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
        """Signal end of utterance."""
        self._active = False
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
        if rms < threshold:
            self._silence_frames += 1
        else:
            self._silence_frames = 0

    @property
    def silence_detected(self) -> bool:
        """True if extended silence detected (end of utterance)."""
        return self._noise_calibrated and self._silence_frames > self._max_silence

    @property
    def max_duration_reached(self) -> bool:
        """True if we've been listening for too long."""
        return self._total_frames > self._max_total_frames

    async def results(self) -> AsyncIterator[STTResult]:
        """Async generator yielding partial and final results."""
        while True:
            try:
                result = await asyncio.wait_for(self._result_queue.get(), timeout=0.1)
                yield result
                if result.is_final:
                    return
            except asyncio.TimeoutError:
                if not self._active and self._result_queue.empty():
                    return
                continue

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
                chunks.append(frame)

                # Only do partial transcription every ~4s to avoid CPU overload
                total_samples = sum(len(c) for c in chunks)
                elapsed = time.time() - last_partial_time
                if total_samples >= SAMPLE_RATE * 4 and elapsed > 3:  # 4s of audio, 3s since last partial
                    audio_np = np.concatenate(chunks).astype(np.float32) / 32768.0
                    segments, _ = model.transcribe(
                        audio_np,
                        language="el",
                        beam_size=1,
                        best_of=1,
                        vad_filter=False,  # Disable VAD - it's too aggressive
                    )
                    text = " ".join(seg.text.strip() for seg in segments)
                    if text and text != partial_sent:
                        partial_sent = text
                        logger.info("STT partial: %s", text)
                        asyncio.run_coroutine_threadsafe(
                            self._result_queue.put(STTResult(text, is_final=False)),
                            loop,
                        )
                        last_partial_time = time.time()
            except queue.Empty:
                if not self._active:
                    break
                continue

        # Final transcription on all audio
        if chunks:
            logger.info("Transcribing final audio (%.1fs)...", sum(len(c) for c in chunks) / SAMPLE_RATE)
            audio_np = np.concatenate(chunks).astype(np.float32) / 32768.0
            segments, _ = model.transcribe(
                audio_np,
                language="el",
                beam_size=3,  # Reduce beam size for speed
                vad_filter=False,  # Disable VAD
            )
            final_text = " ".join(seg.text.strip() for seg in segments)
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
