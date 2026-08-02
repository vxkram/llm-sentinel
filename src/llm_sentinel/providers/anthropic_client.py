import json
import time
import uuid
from collections.abc import AsyncIterator

import httpx

from llm_sentinel.providers.base import (
    ChatRequest,
    ChatResponse,
    HealthProbeResult,
    StreamChunk,
    Usage,
)

_STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
}


class AnthropicClient:
    """Speaks Anthropic's actual /v1/messages wire format: system prompt is a
    top-level field (not a message), content is a list of typed blocks, and
    streaming is a sequence of named SSE events rather than uniform deltas.
    """

    name = "anthropic"

    def __init__(
        self,
        base_url: str,
        api_key: str = "mock-key",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        # A single persistent client (not one per call) so requests reuse
        # pooled/keep-alive connections instead of paying a fresh
        # connect+teardown cost every time - this showed up as measurable
        # gateway overhead under load before the fix.
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "anthropic-version": "2023-06-01"}

    def _build_payload(self, req: ChatRequest, wire_model: str, stream: bool) -> dict:
        system_parts = [m.content for m in req.messages if m.role == "system"]
        messages = [
            {"role": m.role, "content": m.content} for m in req.messages if m.role != "system"
        ]
        payload: dict = {
            "model": wire_model,
            "messages": messages,
            "max_tokens": req.max_tokens or 1024,
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        return payload

    async def chat(self, req: ChatRequest, wire_model: str) -> ChatResponse:
        start = time.monotonic()
        payload = self._build_payload(req, wire_model, stream=False)
        resp = await self._client.post(
            f"{self._base_url}/v1/messages", json=payload, headers=self._headers()
        )
        resp.raise_for_status()
        data = resp.json()

        latency_ms = (time.monotonic() - start) * 1000
        content = "".join(block["text"] for block in data["content"] if block["type"] == "text")
        usage = data.get("usage", {})
        stop_reason = data.get("stop_reason") or "end_turn"
        return ChatResponse(
            id=data.get("id", f"msg_{uuid.uuid4().hex[:12]}"),
            model=req.model,
            provider=self.name,
            content=content,
            usage=Usage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
                total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            ),
            finish_reason=_STOP_REASON_MAP.get(stop_reason, stop_reason),
            latency_ms=latency_ms,
        )

    async def chat_stream(
        self, req: ChatRequest, wire_model: str
    ) -> AsyncIterator[StreamChunk]:
        payload = self._build_payload(req, wire_model, stream=True)
        async with self._client.stream(
            "POST", f"{self._base_url}/v1/messages", json=payload, headers=self._headers()
        ) as resp:
            resp.raise_for_status()
            current_event: str | None = None
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    current_event = line[len("event:") :].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = json.loads(line[len("data:") :].strip())

                if current_event == "content_block_delta":
                    yield StreamChunk(delta=data["delta"].get("text", ""))
                elif current_event == "message_delta":
                    stop_reason = data.get("delta", {}).get("stop_reason")
                    if stop_reason is not None:
                        yield StreamChunk(
                            delta="", finish_reason=_STOP_REASON_MAP.get(stop_reason, stop_reason)
                        )
                elif current_event == "message_stop":
                    break

    async def health_check(self, wire_model: str) -> HealthProbeResult:
        start = time.monotonic()
        try:
            resp = await self._client.get(f"{self._base_url}/healthz", timeout=5.0)
            resp.raise_for_status()
            return HealthProbeResult(healthy=True, latency_ms=(time.monotonic() - start) * 1000)
        except Exception as exc:  # noqa: BLE001 - health probes must never raise
            return HealthProbeResult(
                healthy=False, latency_ms=(time.monotonic() - start) * 1000, error=str(exc)
            )
