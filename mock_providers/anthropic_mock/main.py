import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from mock_providers.common.app_factory import build_mock_app

router = APIRouter()


def _reply_text(messages: list[dict]) -> str:
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return f"[mock-anthropic] You said: {last_user}"


def _count_tokens(text: str) -> int:
    return max(1, len(text.split()))


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_events(
    reply: str, model: str, msg_id: str, input_tokens: int, output_tokens: int
) -> AsyncIterator[str]:
    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "usage": {"input_tokens": input_tokens, "output_tokens": 0},
            },
        },
    )
    yield _sse(
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    )

    words = reply.split(" ")
    for i, word in enumerate(words):
        piece = word if i == len(words) - 1 else f"{word} "
        yield _sse(
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": piece}},
        )
        await asyncio.sleep(0.01)

    yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": output_tokens},
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})


@router.post("/v1/messages")
async def messages(request: Request):
    await request.app.state.fault.maybe_raise_or_delay()
    body = await request.json()
    msgs = body["messages"]
    model = body["model"]
    system = body.get("system", "")
    reply = _reply_text(msgs)
    input_tokens = sum(_count_tokens(m["content"]) for m in msgs) + (
        _count_tokens(system) if system else 0
    )
    output_tokens = _count_tokens(reply)
    msg_id = f"msg_{uuid.uuid4().hex[:12]}"

    if body.get("stream"):
        return StreamingResponse(
            _stream_events(reply, model, msg_id, input_tokens, output_tokens),
            media_type="text/event-stream",
        )

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": reply}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


app = build_mock_app("anthropic", router)
