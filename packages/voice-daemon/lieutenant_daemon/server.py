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

logger = logging.getLogger("lieutenant-daemon")

# ── Language ──────────────────────────────────────────────────────────
_current_language = os.getenv("LANGUAGE", "el")  # "el" or "en"
_GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8800"))

_ACK_PHRASES = {"el": "Διατάξτε", "en": "At your command"}
_WAKE_PHRASES = {"el": "υπολοχαγέ", "en": "lieutenant"}


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
    # Send current state + log history
    await websocket.send_json({"type": "state", "value": sm.state.value, "ts": time.time()})
    await hub.send_log_history(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            logger.debug("WS received: %s", data)
    except WebSocketDisconnect:
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
        wake.set_wake_phrase(_WAKE_PHRASES.get(lang, "υπολοχαγέ"))

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
#  Core lifecycle
# ═══════════════════════════════════════════════════════════════════════
async def _on_wake():
    """Called when wake phrase is detected."""
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

    # Speak acknowledgment before listening (like Siri's chime)
    ack = _ACK_PHRASES.get(_current_language, "Διατάξτε")
    if tts:
        logger.info("Speaking acknowledgment: %s", ack)
        await sm.transition(State.SPEAKING)
        await hub.send_state(State.SPEAKING.value)
        if wake:
            wake.enabled = False
        await tts.speak(ack, language=_current_language)

    await sm.transition(State.LISTENING)
    await hub.send_state(State.LISTENING.value)
    await _start_listening()


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
    MAX_LISTEN_SECONDS = 30  # hard timeout — generous for Siri-like flow

    try:
        async for result in stt.results():
            if sm.state != State.LISTENING:
                break

            # Hard timeout
            if time.time() - listen_start > MAX_LISTEN_SECONDS:
                logger.info("Max listen time reached (%ds), ending.", MAX_LISTEN_SECONDS)
                stt.stop_utterance()
                # Don't continue — fall through to drain the final result below
                break

            # Check silence after speech
            if stt.silence_detected:
                logger.info("Silence detected, ending utterance.")
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

    # ── Drain remaining results to catch the final transcription ──────
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
            pass  # timeout is fine

    # If no speech energy was ever detected, discard STT output (likely hallucination)
    if not stt.speech_was_detected and final_text:
        logger.info("No speech energy detected — discarding STT output as hallucination: '%s'", final_text[:60])
        final_text = ""

    if not final_text or final_text.strip() in ("", "(no speech detected)", "(no speech)", "(STT unavailable)"):
        logger.info("No speech detected, returning to IDLE.")
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
                first_chunk = False

            if _should_flush_tts(tts_buffer):
                sentence = tts_buffer.strip()
                tts_buffer = ""
                if tts and sentence:
                    if tts_task and not tts_task.done():
                        await tts_task
                    tts_task = asyncio.create_task(_speak_sentence(sentence))

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

    if full_response:
        _conversation_history.append({"role": "assistant", "content": full_response})
        if len(_conversation_history) > 20:
            _conversation_history = _conversation_history[-10:]

    if wake:
        wake.enabled = True
    await sm.transition(State.IDLE)
    await hub.send_state(State.IDLE.value)


async def _speak_sentence(text: str):
    if tts and sm.state == State.SPEAKING:
        await tts.speak(text, language=_current_language)


def _should_flush_tts(buffer: str) -> bool:
    for char in ".!;·…\n":
        if char in buffer and len(buffer) > 5:
            return True
    if len(buffer) > 80:
        return True
    return False


async def _kill_switch():
    logger.info("Kill switch activated!")
    if tts:
        tts.cancel()
    stt.stop_utterance()
    if wake:
        wake.enabled = True
    await sm.transition(State.IDLE)
    await hub.send_state(State.IDLE.value)


# ── Mic level broadcasting ────────────────────────────────────────────
async def _broadcast_mic_levels():
    while True:
        try:
            if hub.client_count > 0:
                rms = capture.rms
                if sm.state in (State.IDLE, State.LISTENING):
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

    # Wire audio capture → wake + STT
    def _audio_frame_handler(audio):
        wake.feed_audio(audio)
        stt.feed_audio(audio)

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