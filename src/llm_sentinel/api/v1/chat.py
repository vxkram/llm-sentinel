from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from llm_sentinel.providers.base import ChatRequest, ChatResponse, ProviderClient
from llm_sentinel.providers.registry import ModelNotFoundError

router = APIRouter()


async def _stream_sse(
    client: ProviderClient, req: ChatRequest, wire_model: str
) -> AsyncIterator[str]:
    async for chunk in client.chat_stream(req, wire_model):
        yield f"data: {chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/chat/completions", response_model=ChatResponse)
async def chat_completions(req: ChatRequest, request: Request):
    registry = request.app.state.registry
    try:
        client, wire_model = registry.resolve(req.model)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if req.stream:
        return StreamingResponse(
            _stream_sse(client, req, wire_model), media_type="text/event-stream"
        )

    return await client.chat(req, wire_model)
