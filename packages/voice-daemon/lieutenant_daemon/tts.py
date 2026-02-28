"""TTS engine — neural text-to-speech with edge-tts (primary), macOS say, Azure fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger("lieutenant-daemon")

_TTS_BACKEND = os.getenv("TTS_BACKEND", "edge")

# ── Edge-TTS voice mapping ───────────────────────────────────────────
# Microsoft Neural voices — excellent quality, free, support Greek natively
_EDGE_VOICES = {
    "el": "el-GR-AthinaNeural",      # Female Greek — natural, clear
    "el-male": "el-GR-NestorasNeural",  # Male Greek
    "en": "en-US-AriaNeural",         # Female English
    "en-male": "en-US-GuyNeural",     # Male English
}
_VOICE_GENDER = os.getenv("TTS_VOICE_GENDER", "female")  # "female" or "male"

# Log at import time so we know if .env was loaded
logger.info("TTS config: backend=%s, gender=%s", _TTS_BACKEND, _VOICE_GENDER)


class TTSEngine:
    """
    Sentence-chunked TTS with speaker RMS reporting.
    Primary: edge-tts (Microsoft Neural voices — excellent Greek).
    Fallback: macOS 'say', espeak, Azure.
    """

    def __init__(self, on_rms: Callable[[float], None] | None = None):
        self._on_rms = on_rms
        self._playing = False
        self._cancelled = False
        self._process: subprocess.Popen | None = None

        # Resolve backend name immediately for logging
        if _TTS_BACKEND == "edge":
            try:
                import edge_tts  # noqa: F401
                self._backend = "edge-tts"
            except ImportError:
                self._backend = "say" if os.path.exists("/usr/bin/afplay") else "none"
        elif _TTS_BACKEND == "say":
            self._backend = "say"
        elif _TTS_BACKEND == "azure":
            self._backend = "azure"
        elif _TTS_BACKEND == "local":
            try:
                import edge_tts  # noqa: F401
                self._backend = "edge-tts"
            except ImportError:
                self._backend = "say" if os.path.exists("/usr/bin/afplay") else "none"
        else:
            self._backend = _TTS_BACKEND

    @property
    def is_playing(self) -> bool:
        return self._playing

    def cancel(self):
        """Stop current playback immediately."""
        self._cancelled = True
        self._playing = False
        if self._process:
            try:
                self._process.kill()
            except Exception:
                pass
        logger.info("TTS cancelled.")

    async def speak(self, text: str, language: str = "el") -> bool:
        """
        Speak the text. Returns True if completed, False if cancelled.
        Splits into sentences and speaks each for low latency.
        """
        self._cancelled = False
        self._playing = True
        self._language = language

        sentences = self._split_sentences(text)
        if not sentences:
            self._playing = False
            return True

        for sentence in sentences:
            if self._cancelled:
                self._playing = False
                return False

            sentence = sentence.strip()
            if not sentence:
                continue

            logger.info("TTS speaking: %s", sentence[:60])
            success = await self._speak_one(sentence)
            if not success or self._cancelled:
                self._playing = False
                return False

        self._playing = False
        return True

    async def _speak_one(self, text: str) -> bool:
        """Speak a single sentence using the best available backend."""
        try:
            if _TTS_BACKEND == "edge":
                result = await self._speak_edge(text)
                if result:
                    return True
                # Fallback chain
                return await self._speak_say(text)
            elif _TTS_BACKEND == "say":
                return await self._speak_say(text)
            elif _TTS_BACKEND == "azure":
                return await self._speak_azure(text)
            elif _TTS_BACKEND == "local":
                result = await self._speak_edge(text)
                if result:
                    return True
                return await self._speak_say(text)
            else:
                return await self._speak_edge(text) or await self._speak_say(text)
        except Exception as e:
            logger.error("TTS error: %s", e)
            return False

    async def _speak_edge(self, text: str) -> bool:
        """Use edge-tts — Microsoft Neural voices (free, excellent Greek)."""
        try:
            import edge_tts
        except ImportError:
            logger.warning("edge-tts not installed. pip install edge-tts")
            return False

        self._backend = "edge-tts"
        lang = getattr(self, '_language', 'el')

        # Pick voice based on language + gender preference
        voice_key = f"{lang}-male" if _VOICE_GENDER == "male" else lang
        voice = _EDGE_VOICES.get(voice_key, _EDGE_VOICES.get(lang, "el-GR-AthinaNeural"))
        logger.info("edge-tts voice: %s (gender=%s, lang=%s, key=%s)", voice, _VOICE_GENDER, lang, voice_key)

        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                temp_path = f.name

            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(temp_path)

            if self._cancelled:
                self._cleanup_temp(temp_path)
                return False

            # Play back with ffplay/afplay/mpv and compute real RMS
            success = await self._play_audio_file(temp_path)
            self._cleanup_temp(temp_path)
            return success

        except Exception as e:
            logger.error("edge-tts error: %s", e)
            return False

    async def _play_audio_file(self, path: str) -> bool:
        """Play an audio file and report RMS levels. Tries afplay (macOS), then ffplay."""
        import struct

        # Try to decode to raw PCM for real RMS, then play
        try:
            # Use ffmpeg to decode to raw PCM while playing via afplay/ffplay
            # First, start playback
            if os.path.exists("/usr/bin/afplay"):
                self._process = subprocess.Popen(
                    ["afplay", path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            else:
                # Try ffplay (silent, no window)
                self._process = subprocess.Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            # While playing, report simulated RMS (real RMS would need audio capture)
            while self._process.poll() is None:
                if self._cancelled:
                    self._process.kill()
                    return False
                if self._on_rms:
                    # Slightly randomized RMS to drive waveform animation
                    self._on_rms(0.18 + 0.12 * np.random.random())
                await asyncio.sleep(0.05)

            if self._on_rms:
                self._on_rms(0.0)

            return self._process.returncode == 0

        except FileNotFoundError:
            logger.warning("No audio player found (afplay/ffplay). Trying mpv…")
            try:
                self._process = subprocess.Popen(
                    ["mpv", "--no-video", "--really-quiet", path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                while self._process.poll() is None:
                    if self._cancelled:
                        self._process.kill()
                        return False
                    if self._on_rms:
                        self._on_rms(0.15 + 0.1 * np.random.random())
                    await asyncio.sleep(0.05)
                if self._on_rms:
                    self._on_rms(0.0)
                return self._process.returncode == 0
            except FileNotFoundError:
                logger.error("No audio player available (afplay, ffplay, mpv).")
                return False

    async def _speak_say(self, text: str) -> bool:
        """Use macOS 'say' command (fallback)."""
        self._backend = "say"

        lang = getattr(self, '_language', 'el')
        voice_flag = []
        try:
            result = subprocess.run(
                ["say", "-v", "?"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if lang == "el":
                for line in result.stdout.splitlines():
                    if "el_GR" in line or "Melina" in line:
                        voice_name = line.split()[0]
                        voice_flag = ["-v", voice_name]
                        break
            elif lang == "en":
                for preferred in ("Samantha", "Alex", "Daniel"):
                    for line in result.stdout.splitlines():
                        if line.startswith(preferred) and "en_" in line:
                            voice_flag = ["-v", preferred]
                            break
                    if voice_flag:
                        break
        except Exception:
            pass

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(text)
                temp_path = f.name

            cmd = ["say"] + voice_flag + ["-f", temp_path]
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            while self._process.poll() is None:
                if self._cancelled:
                    self._process.kill()
                    return False
                if self._on_rms:
                    self._on_rms(0.15 + 0.1 * np.random.random())
                await asyncio.sleep(0.05)

            self._cleanup_temp(temp_path)
            if self._on_rms:
                self._on_rms(0.0)

            return self._process.returncode == 0

        except FileNotFoundError:
            logger.warning("'say' command not found (not macOS?)")
            return await self._speak_espeak(text)
        except Exception as e:
            logger.error("say error: %s", e)
            return False

    async def _speak_espeak(self, text: str) -> bool:
        """Fallback: use espeak for Linux."""
        self._backend = "espeak"
        lang = getattr(self, '_language', 'el')
        try:
            self._process = subprocess.Popen(
                ["espeak", "-v", lang, text],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            while self._process.poll() is None:
                if self._cancelled:
                    self._process.kill()
                    return False
                if self._on_rms:
                    self._on_rms(0.12 + 0.1 * np.random.random())
                await asyncio.sleep(0.05)

            if self._on_rms:
                self._on_rms(0.0)
            return self._process.returncode == 0
        except FileNotFoundError:
            logger.warning("espeak not found either. No TTS available.")
            self._backend = "none"
            return False

    async def _speak_azure(self, text: str) -> bool:
        """Use Azure Speech Services TTS."""
        key = os.getenv("AZURE_SPEECH_KEY", "")
        region = os.getenv("AZURE_SPEECH_REGION", "")
        if not key or not region:
            logger.warning("Azure Speech not configured. Falling back.")
            return await self._speak_edge(text) or await self._speak_say(text)

        try:
            import azure.cognitiveservices.speech as speechsdk

            self._backend = "azure"
            config = speechsdk.SpeechConfig(subscription=key, region=region)
            lang = getattr(self, '_language', 'el')
            config.speech_synthesis_voice_name = "el-GR-AthinaNeural" if lang == 'el' else "en-US-JennyNeural"
            synthesizer = speechsdk.SpeechSynthesizer(speech_config=config)

            result = synthesizer.speak_text_async(text).get()
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                return True
            else:
                logger.error("Azure TTS failed: %s", result.reason)
                return False
        except ImportError:
            logger.warning("Azure Speech SDK not installed.")
            return await self._speak_edge(text) or await self._speak_say(text)

    @staticmethod
    def _cleanup_temp(path: str):
        try:
            os.unlink(path)
        except Exception:
            pass

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences for chunked TTS."""
        import re
        sentences = re.split(r'(?<=[.!;·…\n])\s*', text)
        merged = []
        buf = ""
        for s in sentences:
            buf += s + " "
            if len(buf) > 30:
                merged.append(buf.strip())
                buf = ""
        if buf.strip():
            merged.append(buf.strip())
        return merged
