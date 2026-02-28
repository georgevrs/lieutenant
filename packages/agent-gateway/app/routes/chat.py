"""POST /v1/chat/completions — OpenAI-compatible chat endpoint with SSE streaming."""

from __future__ import annotations

import json
import time
import uuid
import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field

from app.agent.core import AgentCore

logger = logging.getLogger("agent-gateway")
router = APIRouter()

agent = AgentCore()


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "local-agent"
    messages: list[Message]
    stream: bool = False
    temperature: float = 0.7
    max_tokens: int = 1024


def _make_chunk(chat_id: str, delta: dict, finish_reason: str | None = None) -> dict:
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "local-agent",
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


async def _stream_response(request: Request, chat_req: ChatRequest):
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    # First chunk: role
    yield {
        "event": "message",
        "data": json.dumps(
            _make_chunk(chat_id, {"role": "assistant"}, None)
        ),
    }

    # Stream content tokens
    async for token in agent.generate_stream(chat_req.messages):
        if await request.is_disconnected():
            break
        yield {
            "event": "message",
            "data": json.dumps(
                _make_chunk(chat_id, {"content": token}, None)
            ),
        }

    # Final chunk — include which LLM backend was used
    final_chunk = _make_chunk(chat_id, {}, "stop")
    final_chunk["x_backend"] = agent.last_backend
    yield {
        "event": "message",
        "data": json.dumps(final_chunk),
    }
    yield {"event": "message", "data": "[DONE]"}


@router.post("/chat/completions")
async def chat_completions(request: Request, body: ChatRequest):
    if body.stream:
        return EventSourceResponse(_stream_response(request, body))

    # Non-streaming
    full_text = ""
    async for token in agent.generate_stream(body.messages):
        full_text += token

    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "local-agent",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": full_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
    )
