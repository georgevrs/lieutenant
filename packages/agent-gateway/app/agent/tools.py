"""Agent tools — fs_read, fs_write, shell, http_get."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger("agent-gateway")

TOOLS = {
    "fs_read": "Read a file from the filesystem.",
    "fs_write": "Write content to a file on the filesystem.",
    "shell": "Execute a shell command.",
    "http_get": "Fetch a URL via HTTP GET.",
}

_TIMEOUT = 30  # seconds
_MAX_OUTPUT = 4000  # chars


async def execute_tool(name: str, args: dict) -> str:
    """Execute a tool and return the result string."""
    try:
        if name == "fs_read":
            return await _fs_read(args["path"])
        elif name == "fs_write":
            return await _fs_write(args["path"], args.get("content", ""))
        elif name == "shell":
            return await _shell(args["command"])
        elif name == "http_get":
            return await _http_get(args["url"])
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return f"Error: {e}"


async def _fs_read(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"File not found: {path}"
    if not p.is_file():
        return f"Not a file: {path}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > _MAX_OUTPUT:
            return content[:_MAX_OUTPUT] + f"\n\n… [truncated, {len(content)} chars total]"
        return content
    except Exception as e:
        return f"Read error: {e}"


async def _fs_write(path: str, content: str) -> str:
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"Write error: {e}"


async def _shell(command: str) -> str:
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(Path.home()),
            ),
            timeout=_TIMEOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + f"\n… [truncated]"
        return f"Exit code: {proc.returncode}\n{output}"
    except asyncio.TimeoutError:
        return "Command timed out."
    except Exception as e:
        return f"Shell error: {e}"


async def _http_get(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            text = resp.text
            if len(text) > _MAX_OUTPUT:
                text = text[:_MAX_OUTPUT] + f"\n… [truncated]"
            return f"HTTP {resp.status_code}\n{text}"
    except Exception as e:
        return f"HTTP error: {e}"
