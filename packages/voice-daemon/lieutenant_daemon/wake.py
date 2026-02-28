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
_COOLDOWN = 1.2  # seconds between triggers — fast re-triggering

# ── Vosk model directories by language ────────────────────────────────
_MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
_MODEL_PATHS: dict[str, str] = {}  # populated in _discover_models()


def _discover_models():
    """Scan models/ directory and map language codes to model paths."""
    if not _MODELS_DIR.exists():
        return
    for d in _MODELS_DIR.iterdir():
        if not d.is_dir():
            continue
        name = d.name.lower()
        if "el-gr" in name or "el_gr" in name:
            _MODEL_PATHS["el"] = str(d)
        elif "en-us" in name or "en_us" in name or ("en" in name and "el" not in name):
            _MODEL_PATHS["en"] = str(d)
    logger.info("Vosk models discovered: %s", {k: Path(v).name for k, v in _MODEL_PATHS.items()})


_discover_models()


class WakeDetector:
    """
    Offline wake phrase detector using Vosk models.
    Supports dual-language model switching (Greek/English).
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
        self._wake_phrase = _WAKE_PHRASE
        self._current_lang = os.getenv("LANGUAGE", "el")
        self._models: dict[str, object] = {}  # loaded Vosk Model objects by lang
        self._model_lock = threading.Lock()
        self._need_reload = False  # flag to reload recognizer in process loop

    def set_wake_phrase(self, phrase: str, language: str | None = None):
        """Change the wake phrase and optionally switch Vosk model for the new language."""
        self._wake_phrase = phrase.lower()
        logger.info("Wake phrase changed to: '%s'", self._wake_phrase)
        if language and language != self._current_lang:
            self._current_lang = language
            if language in _MODEL_PATHS:
                logger.info("Switching Vosk model to language: %s", language)
                self._need_reload = True
            else:
                logger.warning("No Vosk model for language '%s' — wake detection may not work", language)

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

    # ── Phonetic variant map for robust matching ──────────────────────
    # Vosk small models may segment words differently; check alternatives too.
    _PHONETIC_VARIANTS: dict[str, list[str]] = {
        "lieutenant": ["lieutenant", "left tenant", "left ten ant",
                        "lew tenant", "loo tenant", "lef tenant"],
    }

    def _matches_wake(self, text: str) -> bool:
        """Check if text contains the wake phrase or a known phonetic variant."""
        if not text:
            return False
        if self._wake_phrase in text:
            return True
        # Check phonetic variants
        variants = self._PHONETIC_VARIANTS.get(self._wake_phrase, [])
        return any(v in text for v in variants)

    def _process_loop(self):
        """Background thread: initialize Vosk and process audio."""
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel

            SetLogLevel(-1)  # Suppress Vosk logs

            # Load initial model for current language
            self._recognizer = self._load_recognizer(self._current_lang, Model, KaldiRecognizer)
            if not self._recognizer:
                logger.error("No Vosk model available for '%s'. Wake detection disabled.", self._current_lang)
                return

            logger.info("Wake detector ready. Listening for '%s' (lang=%s)", self._wake_phrase, self._current_lang)

            while self._running:
                # Hot-reload model if language changed
                if self._need_reload:
                    self._need_reload = False
                    new_rec = self._load_recognizer(self._current_lang, Model, KaldiRecognizer)
                    if new_rec:
                        self._recognizer = new_rec
                        logger.info("Vosk model reloaded for '%s'. Listening for '%s'",
                                    self._current_lang, self._wake_phrase)
                    else:
                        logger.warning("Could not load Vosk model for '%s', keeping previous.", self._current_lang)

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
                    if self._matches_wake(text):
                        now = time.time()
                        if now - self._last_trigger > _COOLDOWN:
                            self._last_trigger = now
                            logger.info("🎤 Wake phrase detected!")
                            asyncio.run_coroutine_threadsafe(self._on_wake(), self._loop)
                else:
                    partial = json.loads(self._recognizer.PartialResult())
                    partial_text = partial.get("partial", "").lower()
                    if self._matches_wake(partial_text):
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

    def _load_recognizer(self, lang: str, ModelClass, RecognizerClass):
        """Load (or reuse cached) Vosk model for the given language and create a recognizer."""
        # Check if model already loaded
        if lang not in self._models:
            model_path = _MODEL_PATHS.get(lang) or self._ensure_model(lang)
            if not model_path:
                return None
            logger.info("Loading Vosk model from %s", model_path)
            self._models[lang] = ModelClass(model_path)

        model = self._models[lang]

        # Try grammar-based recognition (much more reliable for wake word detection).
        # Grammar constrains the model to only output the wake phrase or [unk].
        try:
            grammar = json.dumps([self._wake_phrase, "[unk]"])
            recognizer = RecognizerClass(model, 16000, grammar)
            logger.info("Vosk grammar-based recognizer created for '%s' (lang=%s)",
                        self._wake_phrase, lang)
        except Exception:
            # Fallback to open vocabulary if grammar not supported by this model
            recognizer = RecognizerClass(model, 16000)
            logger.info("Vosk open-vocabulary recognizer created (lang=%s)", lang)

        recognizer.SetWords(False)
        return recognizer

    def _ensure_model(self, lang: str = "el") -> str:
        """Check for local Vosk model for the given language."""
        if lang in _MODEL_PATHS:
            return _MODEL_PATHS[lang]

        models_dir = _MODELS_DIR
        models_dir.mkdir(exist_ok=True)

        # Scan for any matching model
        lang_markers = {"el": ["el", "gr"], "en": ["en", "us"]}
        markers = lang_markers.get(lang, [lang])

        for d in models_dir.iterdir():
            if d.is_dir() and "whisper" not in d.name.lower():
                name_lower = d.name.lower()
                if any(m in name_lower for m in markers):
                    _MODEL_PATHS[lang] = str(d)
                    return str(d)

        logger.warning(
            "No Vosk model found for '%s'. Please download from:\n"
            "  https://alphacephei.com/vosk/models\n"
            "  Extract to: %s/",
            lang, models_dir,
        )
        return ""
