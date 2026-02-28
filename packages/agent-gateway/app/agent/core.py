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
        import shutil
        self._has_openclaw = bool(shutil.which("openclaw"))
        self._has_google = bool(_GOOGLE_KEY)
        self._has_openai = bool(_OPENAI_KEY)
        self.last_backend = "none"  # Track which LLM backend served the last request

        if self._has_openclaw:
            logger.info("OpenClaw CLI detected — will use as primary LLM backend.")
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
                self.last_backend = "openclaw"
                async for token in self._openclaw_stream(user_text):
                    yield token
                return
            except Exception as e:
                logger.error("OpenClaw failed: %s — falling back", e)

        # Stream from Gemini (fallback)
        if self._has_google:
            self.last_backend = "gemini"
            async for token in self._gemini_stream(messages):
                yield token
            return

        if self._has_openai:
            self.last_backend = "openai"
            async for token in self._openai_stream(messages):
                yield token
            return

        # Fallback local
        self.last_backend = "local"
        async for token in self._local_fallback(user_text):
            yield token

    # ── OpenClaw via CLI subprocess (reliable, uses CLI auth) ───────────
    async def _openclaw_stream(self, user_text: str) -> AsyncIterator[str]:
        """Call OpenClaw agent via the CLI subprocess.

        The OpenClaw WS gateway requires device-key auth for operator.write scope,
        which is only available through the official CLI. Using the CLI as a subprocess
        is reliable and avoids the scope issue entirely.
        """
        import shutil
        import subprocess

        openclaw_bin = shutil.which("openclaw")
        if not openclaw_bin:
            raise RuntimeError("openclaw CLI not found in PATH")

        t0 = time.time()
        idem_key = uuid.uuid4().hex

        extra_prompt = _SYSTEM_PROMPTS.get(_current_language, _SYSTEM_PROMPTS["el"])
        # Explicitly instruct the language in the message to override any agent defaults
        if _current_language == "en":
            augmented_text = f"[Respond in English] {user_text}"
        else:
            augmented_text = user_text

        params_json = json.dumps({
            "message": augmented_text,
            "agentId": "main",
            "idempotencyKey": idem_key,
            "extraSystemPrompt": extra_prompt,
        })

        cmd = [
            openclaw_bin, "gateway", "call", "agent",
            "--params", params_json,
            "--expect-final",
            "--timeout", "60000",
            "--json",
        ]

        logger.info("OpenClaw: calling agent via CLI (%d chars)", len(user_text))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=65
        )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            err_msg = stderr or stdout or f"exit code {proc.returncode}"
            raise RuntimeError(f"OpenClaw CLI error: {err_msg}")

        # Parse the JSON output
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            # Sometimes CLI prepends banner text; try to find JSON
            json_start = stdout.find("{")
            if json_start >= 0:
                result = json.loads(stdout[json_start:])
            else:
                raise RuntimeError(f"OpenClaw: invalid JSON response: {stdout[:200]}")

        # Extract text from result
        text = ""
        payloads = result.get("result", {}).get("payloads", [])
        if payloads:
            text = payloads[0].get("text", "")

        if not text:
            # Try alternate paths
            text = result.get("text", "") or result.get("message", "")

        elapsed = time.time() - t0
        logger.info("OpenClaw: complete in %.2fs (text_len=%d)", elapsed, len(text))

        if text:
            # Yield in sentence-sized chunks for faster TTS start
            for chunk in self._chunk_text(text):
                yield chunk
        else:
            raise RuntimeError("OpenClaw returned empty response")

    @staticmethod
    def _chunk_text(text: str, max_chunk: int = 80) -> list[str]:
        """Split text into sentence-sized chunks for progressive TTS."""
        import re as _re
        # Split on sentence boundaries
        parts = _re.split(r'(?<=[.!;·…\n])\s+', text)
        chunks = []
        buf = ""
        for p in parts:
            if buf and len(buf) + len(p) > max_chunk:
                chunks.append(buf)
                buf = p
            else:
                buf = (buf + " " + p).strip() if buf else p
        if buf:
            chunks.append(buf)
        return chunks if chunks else [text]

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
