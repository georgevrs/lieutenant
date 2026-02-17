"""Agent Core — local reasoning + tool execution, with optional OpenAI backend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import AsyncIterator

from app.agent.tools import TOOLS, execute_tool
from app.agent.audit import audit_log

logger = logging.getLogger("agent-gateway")

_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
_SAFE_MODE = os.getenv("SAFE_MODE", "false").lower() == "true"


# ── Greek intent patterns ─────────────────────────────────────────────
_INTENT_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("shell", "command", re.compile(r"(?:τρέξε|εκτέλεσε|run)\s+(.+)", re.I)),
    ("fs_read", "path", re.compile(r"(?:διάβασε|read|δείξε)\s+(?:(?:το\s+)?αρχείο\s+)?(.+)", re.I)),
    ("fs_write", "args", re.compile(r"(?:γράψε|write|αποθήκευσε)\s+(.+)", re.I)),
    ("http_get", "url", re.compile(r"(?:κατέβασε|φέρε|fetch|get)\s+(https?://\S+)", re.I)),
]

# ── Greeting / fallback responses ─────────────────────────────────────
_GREETINGS = {
    re.compile(r"^\s*(γεια|χαίρε|καλημέρα|καλησπέρα|hey|hello|hi)\b", re.I): "Γεια σου! Πώς μπορώ να σε βοηθήσω;",
    re.compile(r"^\s*(ευχαριστώ|thanks|thank you)\b", re.I): "Παρακαλώ! Είμαι πάντα εδώ.",
    re.compile(r"(πώς είσαι|τι κάνεις|how are you)", re.I): "Είμαι εδώ και έτοιμος να βοηθήσω! Τι θα ήθελες;",
    re.compile(r"(τι είσαι|ποιος είσαι|who are you)", re.I): "Είμαι ο Υπολοχαγός, ο προσωπικός σου βοηθός. Πώς μπορώ να σε εξυπηρετήσω;",
}

_FALLBACK = "Κατάλαβα. Δυστυχώς δεν μπορώ ακόμα να εκτελέσω αυτό που ζητάς, αλλά προσπαθώ! Μπορείς να δοκιμάσεις: «τρέξε ...», «διάβασε ...», «κατέβασε ...»."


class AgentCore:
    """Lightweight agent with tool execution and optional OpenAI backend."""

    def __init__(self):
        self._has_openai = bool(_OPENAI_KEY)
        if self._has_openai:
            logger.info("OpenAI key detected — will use LLM reasoning.")
        else:
            logger.info("No OpenAI key — using local deterministic agent.")

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
            yield "Δεν κατάλαβα κάτι. Μπορείς να επαναλάβεις;"
            return

        # Try OpenAI first if available
        if self._has_openai:
            async for token in self._openai_stream(messages):
                yield token
            return

        # Local deterministic agent
        async for token in self._local_agent(user_text):
            yield token

    # ── OpenAI backend ────────────────────────────────────────────────
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

            # Prepend system prompt
            system = {
                "role": "system",
                "content": (
                    "Είσαι ο Υπολοχαγός, ένας ευφυής φωνητικός βοηθός. "
                    "Απάντα σε φυσικά Ελληνικά, σύντομα και ακριβή. "
                    "Αν χρειαστεί, μπορείς να εκτελέσεις εργαλεία."
                ),
            }

            stream = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[system] + msgs,
                stream=True,
                max_tokens=1024,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except Exception as e:
            logger.error("OpenAI error: %s", e)
            yield f"Σφάλμα OpenAI: {e}"

    # ── Local deterministic agent ─────────────────────────────────────
    async def _local_agent(self, text: str) -> AsyncIterator[str]:
        # Check tool intents FIRST (higher priority than greetings)
        for tool_name, param_key, pattern in _INTENT_PATTERNS:
            m = pattern.search(text)
            if m:
                async for token in self._run_tool(tool_name, m.group(1).strip(), text):
                    yield token
                return

        # Check greetings
        for pattern, response in _GREETINGS.items():
            if pattern.search(text):
                for token in _simulate_tokens(response):
                    yield token
                    await asyncio.sleep(0.02)
                return

        # Fallback
        for token in _simulate_tokens(_FALLBACK):
            yield token
            await asyncio.sleep(0.02)

    async def _run_tool(self, tool_name: str, arg: str, original: str) -> AsyncIterator[str]:
        # Build args
        if tool_name == "shell":
            args = {"command": arg}
        elif tool_name == "fs_read":
            args = {"path": arg}
        elif tool_name == "fs_write":
            # Simple parse: first word is path, rest is content
            parts = arg.split(None, 1)
            args = {"path": parts[0], "content": parts[1] if len(parts) > 1 else ""}
        elif tool_name == "http_get":
            args = {"url": arg}
        else:
            yield "Δεν αναγνωρίζω αυτό το εργαλείο."
            return

        # Safe mode check
        if _SAFE_MODE and tool_name in ("shell", "fs_write"):
            warning = f"⚠️ Ασφαλής λειτουργία: Το εργαλείο «{tool_name}» απαιτεί επιβεβαίωση. "
            for token in _simulate_tokens(warning):
                yield token
                await asyncio.sleep(0.02)
            return

        # Execute
        preamble = f"Εκτελώ {tool_name}… "
        for token in _simulate_tokens(preamble):
            yield token
            await asyncio.sleep(0.02)

        result = await execute_tool(tool_name, args)

        # Audit
        audit_log(tool_name, args, result)

        # Stream result
        summary = f"\n\nΑποτέλεσμα:\n{result[:2000]}"
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
