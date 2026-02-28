"""Voice Daemon Server — orchestrates all components and streams logs to UI."""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import uvicorn

from lieutenant_daemon.state import StateMachine, State
from lieutenant_daemon.ws_hub import WSHub
from lieutenant_daemon.audio_capture import AudioCapture
from lieutenant_daemon.wake import WakeDetector
from lieutenant_daemon.stt import STTEngine
from lieutenant_daemon.tts import TTSEngine
from lieutenant_daemon.agent_client import stream_agent_response
import lieutenant_daemon.agent_client as _agent_client
import re as _re

logger = logging.getLogger("lieutenant-daemon")

# ── Text cleaning for TTS (markdown + emojis → natural speech) ───────
_EMOJI_RE = _re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001FA00-\U0001FAFF"  # extended symbols
    "\U00002702-\U000027B0"  # dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero-width joiner
    "\U00002600-\U000026FF"  # misc symbols
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "]+", flags=_re.UNICODE
)

# Ordered list of regex substitutions to strip markdown → plain speech
_MD_RULES: list[tuple[_re.Pattern, str]] = [
    (_re.compile(r"```[\s\S]*?```"),   ""),          # fenced code blocks
    (_re.compile(r"`([^`]+)`"),        r"\1"),       # inline code → bare text
    (_re.compile(r"!\[([^\]]*)\]\([^)]+\)"), r"\1"),  # images → alt text
    (_re.compile(r"\[([^\]]*)\]\([^)]+\)"),  r"\1"),  # links → link text
    (_re.compile(r"^#{1,6}\s+",  _re.M), ""),        # headings
    (_re.compile(r"\*\*\*(.+?)\*\*\*"), r"\1"),      # bold-italic
    (_re.compile(r"\*\*(.+?)\*\*"),     r"\1"),      # bold
    (_re.compile(r"__(.+?)__"),         r"\1"),      # bold alt
    (_re.compile(r"\*(.+?)\*"),         r"\1"),      # italic
    (_re.compile(r"_(.+?)_"),           r"\1"),      # italic alt
    (_re.compile(r"~~(.+?)~~"),         r"\1"),      # strikethrough
    (_re.compile(r"^\s*>+\s?",  _re.M), ""),         # blockquotes
    (_re.compile(r"^\s*[-*+]\s+", _re.M), ""),       # unordered list bullets
    (_re.compile(r"^\s*\d+[.)]\s*", _re.M), ""),     # ordered list numbers
    (_re.compile(r"^-{3,}$",   _re.M), ""),          # horizontal rules
    (_re.compile(r"\\([\\`*_{}\[\]()#+\-.!~])"), r"\1"),  # escaped chars
    (_re.compile(r"[ \t]{2,}"),         " "),         # collapse whitespace
    (_re.compile(r"\n{3,}"),            "\n\n"),      # collapse blank lines
]


def _clean_for_tts(text: str) -> str:
    """Strip emojis and markdown formatting → natural spoken text."""
    text = _EMOJI_RE.sub("", text)
    for pattern, repl in _MD_RULES:
        text = pattern.sub(repl, text)
    return text.strip()

# ── Language ──────────────────────────────────────────────────────────
_current_language = os.getenv("LANGUAGE", "el")  # "el" or "en"
_GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8800"))

_ACK_PHRASES = {"el": "Διατάξτε", "en": "At your command"}
_WAKE_PHRASES = {
    "el": os.getenv("WAKE_PHRASE_EL", os.getenv("WAKE_PHRASE", "υπολοχαγέ")).lower(),
    "en": os.getenv("WAKE_PHRASE_EN", "lieutenant").lower(),
}
_DISPLAY_NAME = os.getenv("DISPLAY_NAME", "Lieutenant")

# ── Conversation mode config ─────────────────────────────────────────
_CONVERSE_TIMEOUT = float(os.getenv("CONVERSE_TIMEOUT", "5.0"))  # seconds to wait for follow-up
_CONVERSE_ENABLED = os.getenv("CONVERSE_MODE", "true").lower() == "true"
_MAX_HISTORY = int(os.getenv("MAX_HISTORY", "30"))  # conversation memory depth

# ── Barge-in (speech interruption) config ─────────────────────────────
_BARGEIN_RMS_THRESHOLD = float(os.getenv("BARGEIN_RMS_THRESHOLD", "0.035"))
_BARGEIN_FRAMES_NEEDED = int(os.getenv("BARGEIN_FRAMES_NEEDED", "8"))  # consecutive high-energy frames (~512 ms)
_BARGEIN_COOLDOWN_S = float(os.getenv("BARGEIN_COOLDOWN_S", "1.5"))  # ignore barge-in for N s after TTS starts
_BARGEIN_POST_TTS_GUARD_S = float(os.getenv("BARGEIN_POST_TTS_GUARD_S", "1.2"))  # guard after each TTS chunk ends


# ── Globals ───────────────────────────────────────────────────────────
sm = StateMachine()
hub = WSHub()
capture = AudioCapture()
stt = STTEngine()
tts: TTSEngine | None = None
wake: WakeDetector | None = None

_loop: asyncio.AbstractEventLoop | None = None
_conversation_history: list[dict] = []
_mic_broadcast_task: asyncio.Task | None = None
_converse_task: asyncio.Task | None = None

# ── TTS echo suppression for STT ──────────────────────────────────────
_TTS_ECHO_GUARD_S = float(os.getenv("TTS_ECHO_GUARD_S", "0.5"))  # suppress STT for N s after TTS stops
_tts_echo_suppress_until: float = 0.0  # time.time() until which STT feeding is suppressed

# ── Barge-in state ────────────────────────────────────────────────────
_bargein_high_frames: int = 0          # consecutive frames above threshold
_bargein_speaking_since: float = 0.0   # time.time() when SPEAKING started
_bargein_triggered: bool = False       # prevent re-trigger in same SPEAKING session
_bargein_tts_was_playing: bool = False  # track TTS playing → stopped transitions
_bargein_tts_stopped_at: float = 0.0   # when the last TTS chunk stopped


# ═══════════════════════════════════════════════════════════════════════
#  Logging handler that forwards log records → WS hub
# ═══════════════════════════════════════════════════════════════════════
class _WSLogHandler(logging.Handler):
    """Captures Python log records and pushes them to the WebSocket hub."""

    def __init__(self, hub_ref: WSHub):
        super().__init__(level=logging.DEBUG)
        self._hub = hub_ref
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def emit(self, record: logging.LogRecord):
        if self._loop is None or self._loop.is_closed():
            return
        # Avoid feedback loops for the WS hub's own logger
        if "WS client" in record.getMessage():
            return
        try:
            msg = self.format(record)
            asyncio.run_coroutine_threadsafe(
                self._hub.send_log(record.levelname, msg, source=record.name),
                self._loop,
            )
        except Exception:
            pass  # never block the logger


_ws_log_handler = _WSLogHandler(hub)
_ws_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s  %(message)s", datefmt="%H:%M:%S"))


# ═══════════════════════════════════════════════════════════════════════
#  FastAPI application
# ═══════════════════════════════════════════════════════════════════════
app = FastAPI(title="Lieutenant Voice Daemon")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── WebSocket endpoint ────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await hub.connect(websocket)
    try:
        # Send current state + log history
        await websocket.send_json({"type": "state", "value": sm.state.value, "ts": time.time()})
        await hub.send_log_history(websocket)
        while True:
            data = await websocket.receive_text()
            logger.debug("WS received: %s", data)
    except (WebSocketDisconnect, RuntimeError):
        # Client disconnected (possibly mid-send during reconnect)
        pass
    finally:
        await hub.disconnect(websocket)


# ── Control endpoints ─────────────────────────────────────────────────
@app.post("/control/wake")
async def ctrl_wake():
    """Simulate wake word trigger."""
    logger.info("Manual wake trigger via /control/wake")
    await _on_wake()
    return JSONResponse({"ok": True, "state": sm.state.value})


@app.post("/control/stop")
async def ctrl_stop():
    """Kill switch — stop everything and return to IDLE."""
    await _kill_switch()
    return JSONResponse({"ok": True, "state": "IDLE"})


@app.post("/control/push_to_talk/start")
async def ctrl_ptt_start():
    if sm.state == State.IDLE:
        await _on_wake()
    return JSONResponse({"ok": True, "state": sm.state.value})


@app.post("/control/push_to_talk/stop")
async def ctrl_ptt_stop():
    if sm.state == State.LISTENING:
        stt.stop_utterance()
    return JSONResponse({"ok": True, "state": sm.state.value})


class LanguageRequest(BaseModel):
    language: str  # "el" or "en"


@app.get("/control/language")
async def get_language():
    return JSONResponse({"language": _current_language})


@app.post("/control/language")
async def set_language(body: LanguageRequest):
    global _current_language
    lang = body.language if body.language in ("el", "en") else "el"
    _current_language = lang

    # Update wake detector phrase
    if wake:
        wake.set_wake_phrase(_WAKE_PHRASES.get(lang, "υπολοχαγέ"), language=lang)

    # Propagate to agent-gateway
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"http://127.0.0.1:{_GATEWAY_PORT}/v1/language",
                json={"language": lang},
            )
    except Exception as e:
        logger.warning("Could not propagate language to gateway: %s", e)

    logger.info("Language switched to: %s", lang)
    await hub.broadcast({"type": "language", "value": lang, "ts": time.time()})
    return JSONResponse({"ok": True, "language": lang})


# ── Settings endpoints (wake words + display name) ────────────────────
class SettingsRequest(BaseModel):
    wake_phrase_el: str | None = None
    wake_phrase_en: str | None = None
    display_name: str | None = None


@app.get("/control/settings")
async def get_settings():
    return JSONResponse({
        "wake_phrase_el": _WAKE_PHRASES["el"],
        "wake_phrase_en": _WAKE_PHRASES["en"],
        "display_name": _DISPLAY_NAME,
    })


@app.post("/control/settings")
async def set_settings(body: SettingsRequest):
    global _DISPLAY_NAME
    changed: dict[str, str] = {}

    if body.wake_phrase_el is not None and body.wake_phrase_el.strip():
        phrase = body.wake_phrase_el.strip().lower()
        _WAKE_PHRASES["el"] = phrase
        changed["WAKE_PHRASE_EL"] = phrase
        changed["WAKE_PHRASE"] = phrase  # legacy compat
        if _current_language == "el" and wake:
            wake.set_wake_phrase(phrase, language="el")

    if body.wake_phrase_en is not None and body.wake_phrase_en.strip():
        phrase = body.wake_phrase_en.strip().lower()
        _WAKE_PHRASES["en"] = phrase
        changed["WAKE_PHRASE_EN"] = phrase
        if _current_language == "en" and wake:
            wake.set_wake_phrase(phrase, language="en")

    if body.display_name is not None and body.display_name.strip():
        _DISPLAY_NAME = body.display_name.strip()
        changed["DISPLAY_NAME"] = _DISPLAY_NAME

    # Persist to .env
    if changed:
        _persist_env(changed)
        logger.info("Settings updated: %s", changed)

    # Broadcast to all clients
    await hub.broadcast({
        "type": "settings",
        "wake_phrase_el": _WAKE_PHRASES["el"],
        "wake_phrase_en": _WAKE_PHRASES["en"],
        "display_name": _DISPLAY_NAME,
        "ts": time.time(),
    })

    return JSONResponse({
        "ok": True,
        "wake_phrase_el": _WAKE_PHRASES["el"],
        "wake_phrase_en": _WAKE_PHRASES["en"],
        "display_name": _DISPLAY_NAME,
    })


def _persist_env(updates: dict[str, str]):
    """Write key=value pairs into the .env file, updating existing keys or appending."""
    from pathlib import Path
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if not env_path.exists():
        env_path.touch()

    lines = env_path.read_text(encoding="utf-8").splitlines()
    keys_written: set[str] = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                lines[i] = f"{key}={updates[key]}"
                keys_written.add(key)

    # Append any keys not already in the file
    for key, val in updates.items():
        if key not in keys_written:
            lines.append(f"{key}={val}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Persisted settings to %s", env_path)


@app.get("/status")
async def status():
    return {
        "state": sm.state.value,
        "stt_backend": stt.backend,
        "tts_backend": tts._backend if tts else "none",
        "ws_clients": hub.client_count,
        "mic": {
            "device": capture.device_name,
            "frames": capture.frames_received,
            "rms": round(capture.rms, 5),
            "healthy": capture.is_healthy,
            "errors": capture.errors,
        },
    }


# ═══════════════════════════════════════════════════════════════════════
#  Core lifecycle — with conversation mode
# ═══════════════════════════════════════════════════════════════════════
async def _on_wake():
    """Called when wake phrase is detected or conversation triggers re-listen."""
    global _converse_task

    # Cancel any pending converse timeout
    if _converse_task and not _converse_task.done():
        _converse_task.cancel()
        _converse_task = None

    if sm.state == State.CONVERSING:
        # Already in conversation mode — go straight to listening
        logger.info("Conversation mode: continuing to listen (no wake needed).")
        await sm.transition(State.LISTENING)
        await hub.send_state(State.LISTENING.value)
        await _start_listening()
        return

    if sm.state != State.IDLE:
        if sm.state == State.SPEAKING:
            logger.info("Barge-in detected! Stopping TTS.")
            if tts:
                tts.cancel()
            await sm.transition(State.LISTENING)
            await hub.send_state(State.LISTENING.value)
            await _start_listening()
            return
        return

    # Speak acknowledgment, then start listening once TTS is done.
    # UI transitions immediately so the user sees LISTENING right away.
    if wake:
        wake.enabled = False

    await sm.transition(State.LISTENING)
    await hub.send_state(State.LISTENING.value)

    if tts:
        ack = _ACK_PHRASES.get(_current_language, "Διατάξτε")
        logger.info("Speaking acknowledgment: %s", ack)
        asyncio.create_task(_speak_ack_then_listen(ack))
    else:
        await _start_listening()


async def _speak_ack_then_listen(ack: str):
    """Play the ack phrase, wait for echo to dissipate, then start STT."""
    try:
        await tts.speak(ack, language=_current_language)
    except Exception as e:
        logger.warning("Ack TTS failed: %s", e)
    # Small buffer for speaker echo / reverberation to die out
    await asyncio.sleep(0.3)
    # Only start listening if we're still in LISTENING state
    if sm.state == State.LISTENING:
        await _start_listening()
    else:
        logger.info("State changed during ack — skipping _start_listening")


async def _start_listening():
    global _loop
    _loop = asyncio.get_event_loop()

    if wake:
        wake.enabled = False

    stt.start_utterance(_loop, language=_current_language)
    asyncio.create_task(_process_stt())


async def _process_stt():
    final_text = ""
    listen_start = time.time()
    MAX_LISTEN_SECONDS = 30

    try:
        async for result in stt.results():
            if sm.state != State.LISTENING:
                break

            if time.time() - listen_start > MAX_LISTEN_SECONDS:
                logger.info("Max listen time reached (%ds), ending.", MAX_LISTEN_SECONDS)
                stt.stop_utterance()
                break

            if stt.silence_detected:
                logger.info("Silence detected (VAD), ending utterance.")
                stt.stop_utterance()
                break
            if stt.max_duration_reached:
                logger.info("Max utterance frames reached, ending.")
                stt.stop_utterance()
                break

            if result.is_final:
                final_text = result.text
                logger.info("STT final: %s", final_text)
                await hub.send_stt_final(final_text)
                break
            else:
                if result.text:
                    logger.debug("STT partial: %s", result.text)
                    await hub.send_stt_partial(result.text)
    except Exception as e:
        logger.error("STT processing error: %s", e)
        await hub.send_error(str(e))
        await _kill_switch()
        return

    # ── Drain remaining results for final transcription ───────────────
    if not final_text:
        logger.info("Draining STT queue for final result…")
        try:
            async for result in stt.results():
                if result.is_final:
                    final_text = result.text
                    logger.info("STT final (drained): %s", final_text)
                    await hub.send_stt_final(final_text)
                    break
        except Exception:
            pass

    # Discard hallucinations from silence — but only if we're confident
    # Whisper's own VAD already filters silence in the final pass, so only
    # discard if both our VAD AND the text looks hallucinatory (repeated).
    if not stt.speech_was_detected and final_text:
        words = final_text.split()
        is_repetitive = False
        if len(words) >= 4:
            half = len(words) // 2
            if " ".join(words[:half]) == " ".join(words[half:2*half]):
                is_repetitive = True
        if is_repetitive:
            logger.info("No speech energy + repetitive pattern — discarding hallucination: '%s'", final_text[:60])
            final_text = ""
        else:
            logger.info("No VAD speech detected but Whisper produced text — trusting Whisper: '%s'", final_text[:60])

    if not final_text or final_text.strip() in ("", "(no speech detected)", "(no speech)", "(STT unavailable)"):
        logger.info("No speech detected.")
        # If we were in conversation mode, return to IDLE (user stayed silent)
        if wake:
            wake.enabled = True
        await sm.transition(State.IDLE)
        await hub.send_state(State.IDLE.value)
        return

    await sm.transition(State.THINKING)
    await hub.send_state(State.THINKING.value)
    await _query_agent(final_text)


async def _query_agent(text: str):
    global _conversation_history

    _conversation_history.append({"role": "user", "content": text})

    full_response = ""
    tts_buffer = ""
    first_chunk = True
    tts_task: asyncio.Task | None = None

    try:
        async for token in stream_agent_response(text, _conversation_history.copy()):
            if sm.state not in (State.THINKING, State.SPEAKING):
                break

            full_response += token
            tts_buffer += token
            await hub.send_agent_chunk(token)

            if first_chunk:
                await sm.transition(State.SPEAKING)
                await hub.send_state(State.SPEAKING.value)
                _reset_bargein()
                first_chunk = False

            flushed = _should_flush_tts(tts_buffer)
            if flushed is not None:
                tts_buffer = ""
                if tts and flushed:
                    if tts_task and not tts_task.done():
                        await tts_task
                    tts_task = asyncio.create_task(_speak_sentence(flushed))

        if tts_buffer.strip() and tts and sm.state == State.SPEAKING:
            if tts_task and not tts_task.done():
                await tts_task
            tts_task = asyncio.create_task(_speak_sentence(tts_buffer.strip()))

        if tts_task and not tts_task.done():
            await tts_task

    except Exception as e:
        logger.error("Agent query error: %s", e)
        await hub.send_error(str(e))

    await hub.send_agent_done()

    # ── Broadcast which LLM backend was used ──────────────────────────
    await hub.send_llm_backend(_agent_client.last_llm_backend)

    # ── Update conversation memory ────────────────────────────────────
    if full_response:
        _conversation_history.append({"role": "assistant", "content": full_response})
        # Smart trimming: keep system context + recent exchanges
        if len(_conversation_history) > _MAX_HISTORY:
            _conversation_history = _conversation_history[-_MAX_HISTORY:]

    # ── Enter conversation mode or return to IDLE ─────────────────────
    if _CONVERSE_ENABLED and full_response:
        await _enter_converse_mode()
    else:
        if wake:
            wake.enabled = True
        await sm.transition(State.IDLE)
        await hub.send_state(State.IDLE.value)


async def _enter_converse_mode():
    """
    Enter CONVERSING state: keep listening for a follow-up without wake word.
    If user speaks within CONVERSE_TIMEOUT, process it as a continuation.
    If silence, return to IDLE.
    """
    global _converse_task

    logger.info("Entering conversation mode (%.1fs timeout)…", _CONVERSE_TIMEOUT)
    await sm.transition(State.CONVERSING)
    await hub.send_state(State.CONVERSING.value)

    # Keep wake word disabled during conversation window
    if wake:
        wake.enabled = False

    _converse_task = asyncio.create_task(_converse_listen())


async def _converse_listen():
    """Listen for follow-up speech during CONVERSING state."""
    global _loop
    _loop = asyncio.get_event_loop()

    stt.start_utterance(_loop, language=_current_language)

    # Wait for either speech or timeout
    deadline = time.time() + _CONVERSE_TIMEOUT
    got_speech = False

    try:
        async for result in stt.results():
            if sm.state != State.CONVERSING:
                break

            # Check if timeout exceeded without speech
            if not stt.speech_was_detected and time.time() > deadline:
                logger.info("Conversation timeout — no follow-up speech. Returning to IDLE.")
                stt.stop_utterance()
                break

            # If silence detected after speech, end listening
            if stt.silence_detected:
                logger.info("Silence after follow-up speech, processing…")
                stt.stop_utterance()
                got_speech = True
                break

            if stt.max_duration_reached:
                stt.stop_utterance()
                got_speech = stt.speech_was_detected
                break

            if result.is_final and result.text.strip():
                got_speech = True
                logger.info("Conversation follow-up STT: %s", result.text)
                await hub.send_stt_final(result.text)
                break
            elif result.text:
                # Show partial — user is speaking, extend timeout
                await hub.send_stt_partial(result.text)
                if stt.speech_was_detected:
                    got_speech = True
                    # Once speech starts, switch to full LISTENING mode
                    await sm.transition(State.LISTENING)
                    await hub.send_state(State.LISTENING.value)
                    # Continue processing in the normal STT flow
                    await _process_stt_continuation()
                    return

    except Exception as e:
        logger.error("Converse listen error: %s", e)

    # Drain final
    if got_speech:
        final_text = ""
        try:
            async for result in stt.results():
                if result.is_final:
                    final_text = result.text
                    await hub.send_stt_final(final_text)
                    break
        except Exception:
            pass

        if final_text and final_text.strip() not in ("", "(no speech detected)", "(no speech)"):
            await sm.transition(State.THINKING)
            await hub.send_state(State.THINKING.value)
            await _query_agent(final_text)
            return

    # No speech or empty — back to IDLE
    if wake:
        wake.enabled = True
    await sm.transition(State.IDLE)
    await hub.send_state(State.IDLE.value)


async def _process_stt_continuation():
    """Continue processing STT after detecting speech in CONVERSING mode."""
    final_text = ""
    listen_start = time.time()
    MAX_LISTEN_SECONDS = 30

    try:
        async for result in stt.results():
            if sm.state != State.LISTENING:
                break

            if time.time() - listen_start > MAX_LISTEN_SECONDS:
                stt.stop_utterance()
                break

            if stt.silence_detected:
                stt.stop_utterance()
                break
            if stt.max_duration_reached:
                stt.stop_utterance()
                break

            if result.is_final:
                final_text = result.text
                await hub.send_stt_final(final_text)
                break
            elif result.text:
                await hub.send_stt_partial(result.text)
    except Exception as e:
        logger.error("STT continuation error: %s", e)

    if not final_text:
        try:
            async for result in stt.results():
                if result.is_final:
                    final_text = result.text
                    await hub.send_stt_final(final_text)
                    break
        except Exception:
            pass

    if not stt.speech_was_detected and final_text:
        final_text = ""

    if not final_text or final_text.strip() in ("", "(no speech detected)", "(no speech)", "(STT unavailable)"):
        if wake:
            wake.enabled = True
        await sm.transition(State.IDLE)
        await hub.send_state(State.IDLE.value)
        return

    await sm.transition(State.THINKING)
    await hub.send_state(State.THINKING.value)
    await _query_agent(final_text)


async def _speak_sentence(text: str):
    if tts and sm.state == State.SPEAKING:
        clean = _clean_for_tts(text)
        if clean:
            await tts.speak(clean, language=_current_language)


def _should_flush_tts(buffer: str) -> str | None:
    """
    Return the text to flush (cleaned) if a sentence boundary is reached,
    or None if we should keep buffering.
    """
    stripped = buffer.strip()
    # Never flush a bare list marker like "1." or "2)" with no real sentence
    if _re.match(r'^\d+[.)\s]*$', stripped):
        return None
    # Need some minimum content before flushing on punctuation
    if len(stripped) < 20:
        if len(stripped) < 80:
            return None
    for char in ".!?;·…\n":
        if char in buffer:
            return stripped
    if len(buffer) > 120:
        return stripped
    return None


def _reset_bargein():
    """Reset barge-in state when entering SPEAKING."""
    global _bargein_high_frames, _bargein_speaking_since, _bargein_triggered
    global _bargein_tts_was_playing, _bargein_tts_stopped_at
    _bargein_high_frames = 0
    _bargein_speaking_since = time.time()
    _bargein_triggered = False
    _bargein_tts_was_playing = False
    _bargein_tts_stopped_at = 0.0


def _check_bargein(audio):
    """
    Detect user speech during SPEAKING state and trigger barge-in.

    Strategy: We CANNOT detect speech while TTS is actively playing because
    the mic picks up the TTS echo. Instead we detect speech in the gaps
    *between* TTS sentences (when tts.is_playing == False).
    After TTS stops, we wait a short guard period for echo to decay, then
    check mic RMS for sustained speech energy.

    Runs in the audio capture thread — must schedule async work on _loop.
    """
    import numpy as _np

    global _bargein_high_frames, _bargein_triggered
    global _bargein_tts_was_playing, _bargein_tts_stopped_at

    if _bargein_triggered:
        return

    # Cooldown: ignore the very start of SPEAKING
    if time.time() - _bargein_speaking_since < _BARGEIN_COOLDOWN_S:
        _bargein_high_frames = 0
        return

    tts_playing = tts.is_playing if tts else False

    # Track TTS playing → stopped transitions
    if tts_playing:
        _bargein_tts_was_playing = True
        _bargein_high_frames = 0  # reset during TTS playback
        return  # Can't distinguish user speech from echo while TTS plays

    # TTS just stopped — record when and measure ambient noise for baseline
    if _bargein_tts_was_playing and not tts_playing:
        _bargein_tts_was_playing = False
        _bargein_tts_stopped_at = time.time()
        _bargein_high_frames = 0

    # Guard period after TTS stops — let echo fully decay from speakers + room
    elapsed_since_tts = time.time() - _bargein_tts_stopped_at if _bargein_tts_stopped_at > 0 else 999.0
    if elapsed_since_tts < _BARGEIN_POST_TTS_GUARD_S:
        _bargein_high_frames = 0
        return

    # Now TTS is silent and guard has passed — check for user speech
    float_data = audio.astype(_np.float32) / 32768.0
    rms = float(_np.sqrt(_np.mean(float_data ** 2)))

    if rms > _BARGEIN_RMS_THRESHOLD:
        _bargein_high_frames += 1
    else:
        _bargein_high_frames = max(0, _bargein_high_frames - 1)  # decay slowly

    if _bargein_high_frames >= _BARGEIN_FRAMES_NEEDED:
        _bargein_triggered = True
        _bargein_high_frames = 0
        logger.info("Barge-in detected! (speech RMS=%.4f, threshold=%.4f, frames=%d)",
                     rms, _BARGEIN_RMS_THRESHOLD, _BARGEIN_FRAMES_NEEDED)
        if _loop and not _loop.is_closed():
            asyncio.run_coroutine_threadsafe(_on_wake(), _loop)


async def _kill_switch():
    global _converse_task
    logger.info("Kill switch activated!")
    # Cancel any pending conversation follow-up
    if _converse_task and not _converse_task.done():
        _converse_task.cancel()
        _converse_task = None
    if tts:
        tts.cancel()
    stt.stop_utterance()
    if wake:
        wake.enabled = True
    _conversation_history.clear()
    await sm.transition(State.IDLE)
    await hub.send_state(State.IDLE.value)


# ── Mic level broadcasting ────────────────────────────────────────────
async def _broadcast_mic_levels():
    while True:
        try:
            if hub.client_count > 0:
                rms = capture.rms
                # Always broadcast mic RMS — the waveform uses it in all states
                await hub.send_mic_level(rms)
            await asyncio.sleep(0.05)
        except Exception:
            await asyncio.sleep(0.1)


def _tts_rms_callback(rms: float):
    if _loop and hub.client_count > 0:
        asyncio.run_coroutine_threadsafe(hub.send_tts_level(rms), _loop)


# ═══════════════════════════════════════════════════════════════════════
#  Main entry
# ═══════════════════════════════════════════════════════════════════════
async def run_server(port: int = 8765):
    global tts, wake, _loop, _mic_broadcast_task

    _loop = asyncio.get_event_loop()

    # ── Wire the WS log handler into Python logging ───────────────────
    _ws_log_handler.set_loop(_loop)
    root_logger = logging.getLogger()
    # Remove any existing handlers to prevent duplicates on restart
    if _ws_log_handler not in root_logger.handlers:
        root_logger.addHandler(_ws_log_handler)

    logger.info("Lieutenant Voice Daemon starting on port %d …", port)

    # Initialize TTS
    tts = TTSEngine(on_rms=_tts_rms_callback)
    logger.info("TTS engine initialized (backend=%s)", tts._backend)

    # Pre-load STT model so first wake is instant
    stt.preload()

    # Initialize wake detector
    wake = WakeDetector(on_wake=_on_wake, loop=_loop)

    # Wire audio capture → wake + STT + barge-in
    def _audio_frame_handler(audio):
        global _tts_echo_suppress_until
        wake.feed_audio(audio)

        # ── TTS echo suppression ────────────────────────────────────
        # Don't feed mic audio to STT while TTS is playing (or just
        # finished) — prevents the ack / response from being
        # transcribed as user speech.
        if tts and tts.is_playing:
            _tts_echo_suppress_until = time.time() + _TTS_ECHO_GUARD_S
        elif time.time() < _tts_echo_suppress_until:
            pass  # still in post-TTS echo guard — skip STT feed
        else:
            stt.feed_audio(audio)

        # ── Speech-based barge-in during SPEAKING ───────────────────
        if sm.state == State.SPEAKING:
            _check_bargein(audio)

    capture.on_frame(_audio_frame_handler)

    # State change listener
    async def _on_state_change(state: State):
        await hub.send_state(state.value)

    sm.on_change(_on_state_change)

    # Start components
    capture.start()
    logger.info("Audio capture thread launched")
    wake.start()
    logger.info("Wake detector thread launched")

    # Give capture 1 s to initialise, then report health
    await asyncio.sleep(1.5)
    if capture.is_healthy:
        logger.info("Mic healthy — device=%s, frames=%d, rms=%.4f",
                     capture.device_name, capture.frames_received, capture.rms)
    else:
        logger.error("Mic NOT healthy after 1.5 s — errors: %s", capture.errors)

    _mic_broadcast_task = asyncio.create_task(_broadcast_mic_levels())

    logger.info("Voice daemon ready on port %d", port)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    await server.serve()