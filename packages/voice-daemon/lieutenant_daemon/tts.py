"""TTS engine — text-to-speech with Piper, macOS say, or Azure fallback."""

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

_TTS_BACKEND = os.getenv("TTS_BACKEND", "local")


class TTSEngine:
    """
    Sentence-chunked TTS with speaker RMS reporting.
    Supports: piper (local), macOS 'say', Azure.
    """

    def __init__(self, on_rms: Callable[[float], None] | None = None):
        self._on_rms = on_rms
        self._playing = False
        self._cancelled = False
        self._backend = "none"
        self._process: subprocess.Popen | None = None

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
        Splits into sentences and speaks each.
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
        """Speak a single sentence."""
        try:
            # Try Piper first
            if _TTS_BACKEND == "local":
                return await self._speak_piper(text) or await self._speak_say(text)
            elif _TTS_BACKEND == "say":
                return await self._speak_say(text)
            elif _TTS_BACKEND == "azure":
                return await self._speak_azure(text)
            else:
                return await self._speak_say(text)
        except Exception as e:
            logger.error("TTS error: %s", e)
            return False

    async def _speak_piper(self, text: str) -> bool:
        """Use Piper TTS (local neural TTS)."""
        try:
            import piper
            self._backend = "piper"

            # This is a simplified approach — Piper integration
            # For Greek, a model must be downloaded separately
            logger.warning("Piper TTS not yet configured for Greek. Falling back to 'say'.")
            return False
        except ImportError:
            return False

    async def _speak_say(self, text: str) -> bool:
        """Use macOS 'say' command."""
        self._backend = "say"

        # Select voice based on language
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
                # Look for Greek voice
                for line in result.stdout.splitlines():
                    if "el_GR" in line or "Melina" in line:
                        voice_name = line.split()[0]
                        voice_flag = ["-v", voice_name]
                        break
            elif lang == "en":
                # Use a good English voice (Samantha, Alex, etc.)
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
            # Write to temp file for longer text
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(text)
                temp_path = f.name

            cmd = ["say"] + voice_flag + ["-f", temp_path]
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Simulate RMS while playing
            while self._process.poll() is None:
                if self._cancelled:
                    self._process.kill()
                    return False
                # Simulate speaker RMS
                if self._on_rms:
                    self._on_rms(0.15 + 0.1 * np.random.random())
                await asyncio.sleep(0.05)

            # Clean up
            try:
                os.unlink(temp_path)
            except Exception:
                pass

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
            logger.warning("Azure Speech not configured. Falling back to say.")
            return await self._speak_say(text)

        try:
            import azure.cognitiveservices.speech as speechsdk

            self._backend = "azure"
            config = speechsdk.SpeechConfig(subscription=key, region=region)
            config.speech_synthesis_voice_name = "el-GR-AthinaNeural" if getattr(self, '_language', 'el') == 'el' else "en-US-JennyNeural"
            synthesizer = speechsdk.SpeechSynthesizer(speech_config=config)

            result = synthesizer.speak_text_async(text).get()
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                return True
            else:
                logger.error("Azure TTS failed: %s", result.reason)
                return False
        except ImportError:
            logger.warning("Azure Speech SDK not installed.")
            return await self._speak_say(text)

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences for chunked TTS."""
        import re
        # Split on sentence-ending punctuation
        sentences = re.split(r'(?<=[.!;·…\n])\s*', text)
        # Merge very short fragments
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
