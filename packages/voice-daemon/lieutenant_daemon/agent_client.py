"""Agent client — calls the Agent Gateway and streams response tokens."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncIterator

import httpx

logger = logging.getLogger("lieutenant-daemon")

_GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8800"))
_GATEWAY_URL = f"http://127.0.0.1:{_GATEWAY_PORT}/v1/chat/completions"

# Module-level: which LLM backend served the last request
last_llm_backend: str = "unknown"


async def stream_agent_response(
    user_text: str,
    history: list[dict] | None = None,
) -> AsyncIterator[str]:
    """
    Send user text to the Agent Gateway and yield streaming response tokens.
    """
    messages = history or []
    messages.append({"role": "user", "content": user_text})

    payload = {
        "model": "local-agent",
        "messages": messages,
        "stream": True,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                _GATEWAY_URL,
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    logger.error("Gateway error %d: %s", response.status_code, body)
                    yield f"(Gateway error: {response.status_code})"
                    return

                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()

                        if not line:
                            continue
                        if line.startswith("event:"):
                            continue
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                return

                            try:
                                data = json.loads(data_str)
                                # Track LLM backend from final chunk
                                if "x_backend" in data:
                                    global last_llm_backend
                                    last_llm_backend = data["x_backend"]
                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                continue

    except httpx.ConnectError:
        logger.error("Cannot connect to Agent Gateway at %s", _GATEWAY_URL)
        yield "(Agent Gateway not reachable)"
    except Exception as e:
        logger.error("Agent client error: %s", e)
        yield f"(Error: {e})"
