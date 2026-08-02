from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from llm_sentinel.core.security import AuthenticatedTeam, require_team
from llm_sentinel.providers.base import (
    ChatRequest,
    ChatResponse,
    Message,
    ProviderClient,
)
from llm_sentinel.providers.registry import ModelNotFoundError

router = APIRouter()


def _inject_system_prompt(req: ChatRequest, system_prompt: str) -> ChatRequest:
    existing_system = next((m for m in req.messages if m.role == "system"), None)
    other_messages = [m for m in req.messages if m.role != "system"]
    content = f"{system_prompt}\n{existing_system.content}" if existing_system else system_prompt
    return req.model_copy(update={"messages": [Message(role="system", content=content), *other_messages]})


async def _stream_sse(
    client: ProviderClient, req: ChatRequest, wire_model: str
) -> AsyncIterator[str]:
    async for chunk in client.chat_stream(req, wire_model):
        yield f"data: {chunk.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/chat/completions", response_model=ChatResponse)
async def chat_completions(
    req: ChatRequest, request: Request, team: AuthenticatedTeam = Depends(require_team)
):
    if req.model not in team.config.allowed_models:
        raise HTTPException(
            status_code=403,
            detail=f"team '{team.team_id}' is not permitted to use model '{req.model}'",
        )

    if team.config.system_prompt:
        req = _inject_system_prompt(req, team.config.system_prompt)

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
