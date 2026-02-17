"""Audit logging for tool calls."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("agent-gateway")

_LOG_DIR = Path(__file__).resolve().parents[4] / "logs"
_LOG_FILE = _LOG_DIR / "audit.jsonl"


def audit_log(tool: str, args: dict, result: str):
    """Append a tool call entry to the audit log."""
    _LOG_DIR.mkdir(exist_ok=True)
    entry = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool,
        "args": args,
        "result_preview": result[:500],
    }
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("Audit log write failed: %s", e)
