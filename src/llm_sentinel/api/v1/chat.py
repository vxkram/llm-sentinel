from fastapi import APIRouter, HTTPException, Request

from llm_sentinel.providers.base import ChatRequest, ChatResponse
from llm_sentinel.providers.registry import ModelNotFoundError

router = APIRouter()


@router.post("/chat/completions", response_model=ChatResponse)
async def chat_completions(req: ChatRequest, request: Request) -> ChatResponse:
    registry = request.app.state.registry
    try:
        client, wire_model = registry.resolve(req.model)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return await client.chat(req, wire_model)
