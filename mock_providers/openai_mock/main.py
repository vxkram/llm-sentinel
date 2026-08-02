import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from mock_providers.common.app_factory import build_mock_app

router = APIRouter()


def _reply_text(messages: list[dict]) -> str:
    system_texts = [m["content"] for m in messages if m["role"] == "system"]
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    prefix = f"[system={' | '.join(system_texts)}] " if system_texts else ""
    return f"[mock-openai] {prefix}You said: {last_user}"


def _count_tokens(text: str) -> int:
    return max(1, len(text.split()))


async def _stream_chunks(
    reply: str, model: str, chunk_id: str, created: int
) -> AsyncIterator[str]:
    words = reply.split(" ")
    for i, word in enumerate(words):
        piece = word if i == 0 else f" {word}"
        chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        await asyncio.sleep(0.01)

    final_chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final_chunk)}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    await request.app.state.fault.maybe_raise_or_delay()
    body = await request.json()
    messages = body["messages"]
    model = body["model"]
    reply = _reply_text(messages)
    prompt_tokens = sum(_count_tokens(m["content"]) for m in messages)
    completion_tokens = _count_tokens(reply)
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if body.get("stream"):
        return StreamingResponse(
            _stream_chunks(reply, model, chunk_id, created), media_type="text/event-stream"
        )

    return {
        "id": chunk_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


app = build_mock_app("openai", router)
