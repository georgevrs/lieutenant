"""Agent Core — OpenClaw WS + Gemini fallback, bilingual (EL/EN)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from app.agent.tools import TOOLS, execute_tool
from app.agent.audit import audit_log

logger = logging.getLogger("agent-gateway")

_GOOGLE_KEY = os.getenv("GOOGLE_API_KEY", "")
_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
_SAFE_MODE = os.getenv("SAFE_MODE", "false").lower() == "true"

# ── OpenClaw config ───────────────────────────────────────────────────
_OPENCLAW_WS_URL = os.getenv("OPENCLAW_WS_URL", "ws://127.0.0.1:18789/ws")
_OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "")

# ── Language / personality ────────────────────────────────────────────
_current_language = os.getenv("LANGUAGE", "el")  # "el" or "en"

_SYSTEM_PROMPTS = {
    "el": (
        "Είσαι ο Υπολοχαγός (Lieutenant), ένας ευφυής φωνητικός βοηθός σε Ελληνικά. "
        "Απάντα σύντομα, φυσικά και ακριβώς. Δεν χρειάζεται να χρησιμοποιείς markdown, "
        "emoji ή μορφοποίηση — η απάντησή σου θα διαβαστεί μεγαλόφωνα με TTS. "
        "Να είσαι χρήσιμος, άμεσος και φιλικός."
    ),
    "en": (
        "You are Lieutenant, a smart voice assistant. "
        "Answer briefly, naturally and precisely. Do not use markdown, "
        "emoji or formatting — your answer will be read aloud via TTS. "
        "Be helpful, direct and friendly."
    ),
}

# ── Greek intent patterns for tool dispatch ───────────────────────────
_INTENT_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("shell", "command", re.compile(r"(?:τρέξε|εκτέλεσε|run|execute)\s+(.+)", re.I)),
    ("fs_read", "path", re.compile(r"(?:διάβασε|read|δείξε|show)\s+(?:(?:the\s+)?(?:file\s+)?)?(.+)", re.I)),
    ("fs_write", "args", re.compile(r"(?:γράψε|write|αποθήκευσε|save)\s+(.+)", re.I)),
    ("http_get", "url", re.compile(r"(?:κατέβασε|φέρε|fetch|get)\s+(https?://\S+)", re.I)),
]


def set_language(lang: str):
    """Switch language globally."""
    global _current_language
    _current_language = lang if lang in ("el", "en") else "el"
    logger.info("Language set to: %s", _current_language)


def get_language() -> str:
    return _current_language


class AgentCore:
    """Agent with OpenClaw WS streaming, Gemini fallback, and local tool dispatch."""

    def __init__(self):
        self._has_openclaw = bool(_OPENCLAW_TOKEN)
        self._has_google = bool(_GOOGLE_KEY)
        self._has_openai = bool(_OPENAI_KEY)
        self._openclaw_ws = None
        self._openclaw_connected = False

        if self._has_openclaw:
            logger.info("OpenClaw token detected — will use OpenClaw WS gateway as primary.")
        if self._has_google:
            logger.info("Google Gemini key detected — will use as fallback.")
        if not self._has_openclaw and not self._has_google and not self._has_openai:
            logger.info("No LLM keys — using local deterministic agent.")

    # ── Main streaming generator ──────────────────────────────────────
    async def generate_stream(self, messages: list) -> AsyncIterator[str]:
        user_text = ""
        for m in reversed(messages):
            if hasattr(m, "role"):
                if m.role == "user":
                    user_text = m.content
                    break
            elif isinstance(m, dict) and m.get("role") == "user":
                user_text = m["content"]
                break

        if not user_text:
            yield "Δεν κατάλαβα κάτι." if _current_language == "el" else "I didn't catch that."
            return

        # Check for explicit tool intents first
        for tool_name, param_key, pattern in _INTENT_PATTERNS:
            m = pattern.search(user_text)
            if m:
                async for token in self._run_tool(tool_name, m.group(1).strip(), user_text):
                    yield token
                return

        # Stream from OpenClaw (primary)
        if self._has_openclaw:
            try:
                async for token in self._openclaw_stream(user_text):
                    yield token
                return
            except Exception as e:
                logger.error("OpenClaw failed: %s — falling back", e)

        # Stream from Gemini (fallback)
        if self._has_google:
            async for token in self._gemini_stream(messages):
                yield token
            return

        if self._has_openai:
            async for token in self._openai_stream(messages):
                yield token
            return

        # Fallback local
        async for token in self._local_fallback(user_text):
            yield token

    # ── OpenClaw WS streaming ─────────────────────────────────────────
    async def _openclaw_stream(self, user_text: str) -> AsyncIterator[str]:
        """Connect to OpenClaw WS gateway, send agent request, yield streaming tokens."""
        import websockets

        t0 = time.time()
        first_token = True
        idem_key = uuid.uuid4().hex

        logger.info("OpenClaw: connecting to %s", _OPENCLAW_WS_URL)

        async with websockets.connect(_OPENCLAW_WS_URL, close_timeout=5) as ws:
            # Step 1: Wait for challenge
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            challenge = json.loads(raw)
            if challenge.get("type") != "event" or challenge.get("event") != "connect.challenge":
                raise RuntimeError(f"Unexpected challenge: {challenge}")

            # Step 2: Send connect
            connect_req = {
                "type": "req",
                "id": uuid.uuid4().hex,
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "client": {
                        "id": "gateway-client",
                        "displayName": "Lieutenant",
                        "version": "0.1.0",
                        "platform": "darwin",
                        "mode": "backend",
                    },
                    "caps": [],
                    "auth": {"token": _OPENCLAW_TOKEN},
                    "role": "operator",
                    "scopes": ["operator.admin"],
                },
            }
            await ws.send(json.dumps(connect_req))

            # Wait for hello-ok
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            hello = json.loads(raw)
            if hello.get("ok") is not True:
                err = hello.get("error", {}).get("message", "unknown")
                raise RuntimeError(f"Connect failed: {err}")

            logger.info("OpenClaw: connected in %.2fs", time.time() - t0)

            # Step 3: Send agent request
            agent_req_id = uuid.uuid4().hex
            extra_prompt = _SYSTEM_PROMPTS.get(_current_language, _SYSTEM_PROMPTS["el"])

            agent_req = {
                "type": "req",
                "id": agent_req_id,
                "method": "agent",
                "params": {
                    "message": user_text,
                    "agentId": "main",
                    "idempotencyKey": idem_key,
                    "extraSystemPrompt": extra_prompt,
                },
            }
            await ws.send(json.dumps(agent_req))
            logger.info("OpenClaw: agent request sent (%d chars)", len(user_text))

            # Step 4: Read events until final response
            seen_text = ""
            accepted = False
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    logger.warning("OpenClaw: timeout waiting for response")
                    break

                msg = json.loads(raw)
                msg_type = msg.get("type", "?")
                msg_event = msg.get("event", "")

                # Streaming delta from assistant
                if (msg_type == "event" and msg_event == "agent"
                        and msg.get("payload", {}).get("stream") == "assistant"):
                    delta = msg["payload"].get("data", {}).get("delta", "")
                    if delta:
                        if first_token:
                            logger.info("OpenClaw: first token in %.2fs", time.time() - t0)
                            first_token = False
                        yield delta
                        seen_text += delta

                # Acceptance response (not the final result)
                elif msg_type == "res" and msg.get("id") == agent_req_id:
                    payload = msg.get("payload", {})
                    if payload.get("status") == "accepted":
                        accepted = True
                        logger.info("OpenClaw: agent request accepted (runId=%s)", payload.get("runId", "?")[:16])
                        continue
                    # Non-accepted res — might be an error
                    if not msg.get("ok"):
                        err = msg.get("error", {}).get("message", "unknown error")
                        logger.error("OpenClaw agent error: %s", err)
                        yield f"(OpenClaw error: {err})"
                        break
                    # Unexpected ok res — log and continue
                    logger.info("OpenClaw: unexpected res payload: %s", json.dumps(payload, ensure_ascii=False)[:200])
                    continue

                # Final chat event
                elif msg_type == "event" and msg_event == "chat":
                    state = msg.get("payload", {}).get("state", "")
                    if state == "final":
                        # Extract full text from final event if no streaming was received
                        if not seen_text:
                            payloads = msg.get("payload", {}).get("result", {}).get("payloads", [])
                            if payloads:
                                full_text = payloads[0].get("text", "")
                                if full_text:
                                    yield full_text
                                    seen_text = full_text
                        meta = msg.get("payload", {}).get("result", {}).get("meta", {})
                        agent_meta = meta.get("agentMeta", {})
                        model = agent_meta.get("model", "")
                        dur = meta.get("durationMs", 0)
                        logger.info("OpenClaw: complete in %.2fs (model=%s, agent_dur=%dms, text_len=%d)",
                                    time.time() - t0, model, dur, len(seen_text))
                        break

                # Skip health/tick events
                elif msg_type == "event" and msg_event in ("health", "tick"):
                    continue

    # ── Google Gemini streaming ───────────────────────────────────────
    async def _gemini_stream(self, messages: list) -> AsyncIterator[str]:
        try:
            import google.genai as genai

            client = genai.Client(api_key=_GOOGLE_KEY)

            contents = []
            for m in messages:
                role_str = m.role if hasattr(m, "role") else m.get("role", "user")
                content_str = m.content if hasattr(m, "content") else m.get("content", "")
                gemini_role = "model" if role_str == "assistant" else "user"
                contents.append({"role": gemini_role, "parts": [{"text": content_str}]})

            system_prompt = _SYSTEM_PROMPTS.get(_current_language, _SYSTEM_PROMPTS["el"])

            logger.info("Gemini streaming request (%d messages)…", len(contents))
            t0 = time.time()
            first_token = True

            response = client.models.generate_content_stream(
                model="gemini-2.5-flash",
                contents=contents,
                config={
                    "system_instruction": system_prompt,
                    "max_output_tokens": 800,
                    "temperature": 0.7,
                },
            )

            for chunk in response:
                if chunk.text:
                    if first_token:
                        logger.info("Gemini first token in %.2fs", time.time() - t0)
                        first_token = False
                    yield chunk.text
                await asyncio.sleep(0)

            logger.info("Gemini complete in %.2fs", time.time() - t0)

        except Exception as e:
            logger.error("Gemini error: %s", e)
            if self._has_openai:
                logger.info("Falling back to OpenAI…")
                async for token in self._openai_stream(messages):
                    yield token
            else:
                yield f"Σφάλμα Gemini: {e}" if _current_language == "el" else f"Gemini error: {e}"

    # ── OpenAI backend (fallback) ─────────────────────────────────────
    async def _openai_stream(self, messages: list) -> AsyncIterator[str]:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=_OPENAI_KEY)
            msgs = []
            for m in messages:
                if hasattr(m, "role"):
                    msgs.append({"role": m.role, "content": m.content})
                else:
                    msgs.append(m)

            system_prompt = _SYSTEM_PROMPTS.get(_current_language, _SYSTEM_PROMPTS["el"])
            system = {"role": "system", "content": system_prompt}

            stream = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[system] + msgs,
                stream=True,
                max_tokens=800,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except Exception as e:
            logger.error("OpenAI error: %s", e)
            yield f"Σφάλμα OpenAI: {e}" if _current_language == "el" else f"OpenAI error: {e}"

    # ── Local fallback (no LLM) ───────────────────────────────────────
    async def _local_fallback(self, text: str) -> AsyncIterator[str]:
        _GREETINGS = {
            re.compile(r"^\s*(γεια|χαίρε|καλημέρα|καλησπέρα|hey|hello|hi)\b", re.I):
                ("Γεια σου! Πώς μπορώ να σε βοηθήσω;", "Hello! How can I help you?"),
            re.compile(r"^\s*(ευχαριστώ|thanks|thank you)\b", re.I):
                ("Παρακαλώ! Είμαι πάντα εδώ.", "You're welcome! I'm always here."),
            re.compile(r"(πώς είσαι|τι κάνεις|how are you)", re.I):
                ("Είμαι εδώ και έτοιμος να βοηθήσω!", "I'm here and ready to help!"),
            re.compile(r"(τι είσαι|ποιος είσαι|who are you)", re.I):
                ("Είμαι ο Υπολοχαγός, ο προσωπικός σου βοηθός.", "I'm Lieutenant, your personal assistant."),
        }
        for pattern, (el, en) in _GREETINGS.items():
            if pattern.search(text):
                response = el if _current_language == "el" else en
                for token in _simulate_tokens(response):
                    yield token
                    await asyncio.sleep(0.02)
                return

        fallback_el = "Κατάλαβα. Χρειάζομαι σύνδεση με LLM για να απαντήσω. Ρύθμισε OPENCLAW_TOKEN ή GOOGLE_API_KEY."
        fallback_en = "I understand. I need an LLM connection to answer. Configure OPENCLAW_TOKEN or GOOGLE_API_KEY."
        fallback = fallback_el if _current_language == "el" else fallback_en
        for token in _simulate_tokens(fallback):
            yield token
            await asyncio.sleep(0.02)

    # ── Tool execution ────────────────────────────────────────────────
    async def _run_tool(self, tool_name: str, arg: str, original: str) -> AsyncIterator[str]:
        if tool_name == "shell":
            args = {"command": arg}
        elif tool_name == "fs_read":
            args = {"path": arg}
        elif tool_name == "fs_write":
            parts = arg.split(None, 1)
            args = {"path": parts[0], "content": parts[1] if len(parts) > 1 else ""}
        elif tool_name == "http_get":
            args = {"url": arg}
        else:
            yield "Δεν αναγνωρίζω αυτό το εργαλείο." if _current_language == "el" else "Unknown tool."
            return

        if _SAFE_MODE and tool_name in ("shell", "fs_write"):
            warning = f"Ασφαλής λειτουργία: Το εργαλείο {tool_name} απαιτεί επιβεβαίωση." if _current_language == "el" else f"Safe mode: Tool {tool_name} requires confirmation."
            for token in _simulate_tokens(warning):
                yield token
                await asyncio.sleep(0.02)
            return

        preamble = f"Εκτελώ {tool_name}… " if _current_language == "el" else f"Running {tool_name}… "
        for token in _simulate_tokens(preamble):
            yield token
            await asyncio.sleep(0.02)

        result = await execute_tool(tool_name, args)
        audit_log(tool_name, args, result)

        summary = f"\n\nΑποτέλεσμα:\n{result[:2000]}" if _current_language == "el" else f"\n\nResult:\n{result[:2000]}"
        for token in _simulate_tokens(summary):
            yield token
            await asyncio.sleep(0.015)


def _simulate_tokens(text: str) -> list[str]:
    """Split text into small chunks to simulate token streaming."""
    tokens = []
    words = text.split(" ")
    for i, word in enumerate(words):
        tokens.append(word + (" " if i < len(words) - 1 else ""))
    return tokens
