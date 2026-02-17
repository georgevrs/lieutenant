"""GET /v1/models — list available models."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "local-agent",
                "object": "model",
                "created": 1700000000,
                "owned_by": "lieutenant",
                "permission": [],
            }
        ],
    }
