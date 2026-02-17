"""Audio capture — continuous microphone input with RMS computation."""

from __future__ import annotations

import logging
import threading
import time
import numpy as np
from typing import Callable, Any

logger = logging.getLogger("lieutenant-daemon")

# Audio params
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SIZE = 1024  # ~64ms at 16kHz
DTYPE = "int16"


class AudioCapture:
    """Captures microphone audio in a background thread, distributes frames."""

    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None
        self._frame_callbacks: list[Callable[[np.ndarray], None]] = []
        self._rms: float = 0.0
        self._stream: Any = None
        self._frames_received: int = 0
        self._errors: list[str] = []
        self._device_name: str = "unknown"
        self._started_at: float = 0.0

    @property
    def rms(self) -> float:
        return self._rms

    @property
    def frames_received(self) -> int:
        return self._frames_received

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def is_healthy(self) -> bool:
        if not self._running:
            return False
        if self._started_at and time.time() - self._started_at > 3 and self._frames_received == 0:
            return False
        return True

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    def on_frame(self, callback: Callable[[np.ndarray], None]):
        """Register a callback that receives int16 numpy arrays."""
        self._frame_callbacks.append(callback)

    def start(self):
        if self._running:
            return
        self._running = True
        self._frames_received = 0
        self._errors.clear()
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True, name="audio-capture")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("Audio capture stopped.")

    def _capture_loop(self):
        try:
            import sounddevice as sd
        except ImportError as e:
            msg = f"sounddevice import failed: {e}. Install: pip install sounddevice + apt install portaudio19-dev"
            logger.error(msg)
            self._errors.append(msg)
            return
        except OSError as e:
            msg = f"PortAudio not found: {e}. Install: apt install portaudio19-dev (or brew install portaudio)"
            logger.error(msg)
            self._errors.append(msg)
            return

        # ── Pick best input device ────────────────────────────────────
        device_index = None
        try:
            devs = sd.query_devices()
            default_in = sd.default.device[0]
            logger.info("Audio devices (default input=%s):", default_in)
            for i, d in enumerate(devs):
                if d["max_input_channels"] > 0:
                    marker = " <<<" if i == default_in else ""
                    logger.info("  [%d] %s  in=%d  sr=%.0f%s", i, d["name"], d["max_input_channels"], d["default_samplerate"], marker)
            if isinstance(default_in, (int, float)) and default_in >= 0:
                device_index = int(default_in)
                dev_info = sd.query_devices(device_index)
                self._device_name = str(dev_info["name"])
                logger.info("Using input device [%d]: %s", device_index, self._device_name)
            else:
                logger.warning("No default input device. Letting PortAudio choose.")
        except Exception as e:
            logger.warning("Could not enumerate devices: %s", e)

        # ── Callback ──────────────────────────────────────────────────
        def _callback(indata: np.ndarray, frames: int, time_info: Any, status: Any):
            if status:
                logger.warning("Audio callback status: %s", status)
            audio = indata[:, 0].copy()
            self._frames_received += 1
            float_data = audio.astype(np.float32) / 32768.0
            self._rms = float(np.sqrt(np.mean(float_data ** 2)))
            for cb in self._frame_callbacks:
                try:
                    cb(audio)
                except Exception as e:
                    logger.error("Frame callback error: %s", e)

        # ── Open stream ───────────────────────────────────────────────
        try:
            kwargs: dict = dict(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                blocksize=BLOCK_SIZE,
                dtype=DTYPE,
                callback=_callback,
            )
            if device_index is not None:
                kwargs["device"] = device_index

            self._stream = sd.InputStream(**kwargs)
            self._stream.start()
            logger.info("Audio capture STARTED (rate=%d, block=%d, device=%s)", SAMPLE_RATE, BLOCK_SIZE, self._device_name)

            while self._running:
                time.sleep(0.1)
                if self._frames_received == 0 and time.time() - self._started_at > 5:
                    msg = "No audio frames received after 5 s — mic may be blocked"
                    if msg not in self._errors:
                        logger.error(msg)
                        self._errors.append(msg)
        except Exception as e:
            msg = f"Audio capture error: {e}"
            logger.error(msg)
            self._errors.append(msg)
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            logger.info("Audio capture thread exiting (frames=%d)", self._frames_received)