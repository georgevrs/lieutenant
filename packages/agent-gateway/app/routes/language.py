"""Language control endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.agent.core import set_language, get_language

router = APIRouter()


class LanguageRequest(BaseModel):
    language: str  # "el" or "en"


@router.get("/language")
async def get_lang():
    return {"language": get_language()}


@router.post("/language")
async def set_lang(body: LanguageRequest):
    set_language(body.language)
    return {"language": get_language()}
