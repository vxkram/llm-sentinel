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


class OpenAIClient:
    """Speaks OpenAI's actual /v1/chat/completions wire format. base_url is
    configurable so it can point at the bundled mock server in dev/CI, or at
    api.openai.com for an opt-in real-API smoke test.
    """

    name = "openai"

    def __init__(
        self,
        base_url: str,
        api_key: str = "mock-key",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _build_payload(self, req: ChatRequest, wire_model: str, stream: bool) -> dict:
        payload: dict = {
            "model": wire_model,
            "messages": [m.model_dump() for m in req.messages],
            "stream": stream,
        }
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        return payload

    async def chat(self, req: ChatRequest, wire_model: str) -> ChatResponse:
        start = time.monotonic()
        payload = self._build_payload(req, wire_model, stream=False)
        async with self._client(self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()

        latency_ms = (time.monotonic() - start) * 1000
        choice = data["choices"][0]
        usage = data.get("usage", {})
        return ChatResponse(
            id=data.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            model=req.model,
            provider=self.name,
            content=choice["message"]["content"],
            usage=Usage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
            finish_reason=choice.get("finish_reason") or "stop",
            latency_ms=latency_ms,
        )

    async def chat_stream(
        self, req: ChatRequest, wire_model: str
    ) -> AsyncIterator[StreamChunk]:
        payload = self._build_payload(req, wire_model, stream=True)
        async with (
            self._client(self._timeout) as client,
            client.stream(
                "POST", f"{self._base_url}/v1/chat/completions", json=payload, headers=self._headers()
            ) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:") :].strip()
                if data_str == "[DONE]":
                    break
                data = json.loads(data_str)
                choice = data["choices"][0]
                delta = choice.get("delta", {})
                yield StreamChunk(
                    delta=delta.get("content", ""),
                    finish_reason=choice.get("finish_reason"),
                )

    async def health_check(self, wire_model: str) -> HealthProbeResult:
        start = time.monotonic()
        try:
            async with self._client(5.0) as client:
                resp = await client.get(f"{self._base_url}/healthz")
                resp.raise_for_status()
            return HealthProbeResult(healthy=True, latency_ms=(time.monotonic() - start) * 1000)
        except Exception as exc:  # noqa: BLE001 - health probes must never raise
            return HealthProbeResult(
                healthy=False, latency_ms=(time.monotonic() - start) * 1000, error=str(exc)
            )
