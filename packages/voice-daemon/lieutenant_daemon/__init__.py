"""Lieutenant Voice Daemon — entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from repo root (parents: [0]=lieutenant_daemon, [1]=voice-daemon, [2]=packages, [3]=Lieutenant)
_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_env_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [daemon] %(levelname)s  %(message)s",
)
logger = logging.getLogger("lieutenant-daemon")


def main():
    from lieutenant_daemon.server import run_server
    port = int(os.getenv("VOICE_DAEMON_PORT", "8765"))
    logger.info("Starting Lieutenant Voice Daemon on port %d", port)
    asyncio.run(run_server(port))


if __name__ == "__main__":
    main()
