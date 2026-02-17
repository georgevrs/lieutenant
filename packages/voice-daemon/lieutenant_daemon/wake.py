"""Wake word detection using Vosk phrase spotting."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Awaitable

import numpy as np

logger = logging.getLogger("lieutenant-daemon")

_WAKE_PHRASE = os.getenv("WAKE_PHRASE", "υπολοχαγέ").lower()
_COOLDOWN = 2.0  # seconds between triggers


class WakeDetector:
    """
    Offline wake phrase detector using Vosk Greek small model.
    Continuously feeds audio; when transcript contains the wake phrase, triggers callback.
    """

    def __init__(self, on_wake: Callable[[], Awaitable[None]], loop: asyncio.AbstractEventLoop):
        self._on_wake = on_wake
        self._loop = loop
        self._enabled = True
        self._last_trigger = 0.0
        self._recognizer = None
        self._audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, val: bool):
        self._enabled = val
        if val:
            logger.info("Wake detection ENABLED")
        else:
            logger.info("Wake detection DISABLED (echo suppression)")

    def start(self):
        """Initialize Vosk and start processing thread."""
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def feed_audio(self, audio: np.ndarray):
        """Feed int16 audio frames from the capture callback."""
        if not self._enabled:
            return
        try:
            self._audio_queue.put_nowait(audio.tobytes())
        except queue.Full:
            pass  # Drop frames if queue is full

    def _process_loop(self):
        """Background thread: initialize Vosk and process audio."""
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel

            SetLogLevel(-1)  # Suppress Vosk logs

            model_path = os.getenv("VOSK_MODEL_PATH", "")
            if not model_path:
                # Try to find or download model
                model_path = self._ensure_model()

            if not model_path:
                logger.error("No Vosk model available. Wake detection disabled.")
                return

            logger.info("Loading Vosk model from %s", model_path)
            model = Model(model_path)
            self._recognizer = KaldiRecognizer(model, 16000)
            self._recognizer.SetWords(False)
            logger.info("Wake detector ready. Listening for '%s'", _WAKE_PHRASE)

            while self._running:
                try:
                    data = self._audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                if not self._enabled:
                    continue

                if self._recognizer.AcceptWaveform(data):
                    result = json.loads(self._recognizer.Result())
                    text = result.get("text", "").lower()
                    if text:
                        logger.debug("Vosk heard: %s", text)
                    if _WAKE_PHRASE in text:
                        now = time.time()
                        if now - self._last_trigger > _COOLDOWN:
                            self._last_trigger = now
                            logger.info("🎤 Wake phrase detected!")
                            asyncio.run_coroutine_threadsafe(self._on_wake(), self._loop)
                else:
                    partial = json.loads(self._recognizer.PartialResult())
                    partial_text = partial.get("partial", "").lower()
                    if _WAKE_PHRASE in partial_text:
                        now = time.time()
                        if now - self._last_trigger > _COOLDOWN:
                            self._last_trigger = now
                            logger.info("🎤 Wake phrase detected (partial)!")
                            # Reset recognizer to avoid re-triggering
                            self._recognizer.Reset()
                            asyncio.run_coroutine_threadsafe(self._on_wake(), self._loop)

        except ImportError:
            logger.error("Vosk not installed. Wake detection unavailable.")
        except Exception as e:
            logger.error("Wake detector error: %s", e)

    def _ensure_model(self) -> str:
        """Check for local Vosk Greek model, provide instructions if missing."""
        models_dir = Path(__file__).resolve().parent.parent / "models"
        models_dir.mkdir(exist_ok=True)

        # Check for existing model (skip whisper models)
        for d in models_dir.iterdir():
            if d.is_dir() and "gr" in d.name.lower() and "whisper" not in d.name.lower():
                return str(d)

        # Check common model name
        expected = models_dir / "vosk-model-small-el-gr-0.15"
        if expected.exists():
            return str(expected)

        logger.warning(
            "No Vosk Greek model found. Please download from:\n"
            "  https://alphacephei.com/vosk/models\n"
            "  Extract to: %s/vosk-model-small-el-gr-0.15/\n"
            "Falling back to any available model...",
            models_dir,
        )

        # Try any Vosk model directory (skip whisper)
        for d in models_dir.iterdir():
            if d.is_dir() and "whisper" not in d.name.lower():
                return str(d)

        return ""
