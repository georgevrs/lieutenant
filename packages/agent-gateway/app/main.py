"""Agent Gateway — FastAPI application."""

from __future__ import annotations

import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env from repo root
_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_env_path)

from app.routes.chat import router as chat_router
from app.routes.models import router as models_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [gateway] %(levelname)s  %(message)s",
)
logger = logging.getLogger("agent-gateway")

# Ensure logs dir
(Path(__file__).resolve().parents[2] / "logs").mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(application: FastAPI):
    logger.info("Agent Gateway starting on port %s", os.getenv("GATEWAY_PORT", 8800))
    yield
    logger.info("Agent Gateway shutting down.")


app = FastAPI(
    title="Lieutenant Agent Gateway",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(models_router, prefix="/v1")
app.include_router(chat_router, prefix="/v1")
