import json
import time
from collections.abc import AsyncIterator

import httpx

from llm_sentinel.providers.base import (
    ChatRequest,
    ChatResponse,
    HealthProbeResult,
    StreamChunk,
    Usage,
)


class OllamaClient:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        # A single persistent client (not one per call) so requests reuse
        # pooled/keep-alive connections instead of paying a fresh
        # connect+teardown cost every time - this showed up as measurable
        # gateway overhead under load before the fix.
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(self, req: ChatRequest, wire_model: str) -> ChatResponse:
        start = time.monotonic()
        payload = {
            "model": wire_model,
            "messages": [m.model_dump() for m in req.messages],
            "stream": False,
            "options": {k: v for k, v in {"temperature": req.temperature}.items() if v is not None},
        }
        resp = await self._client.post(f"{self._base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

        latency_ms = (time.monotonic() - start) * 1000
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)
        return ChatResponse(
            id=f"ollama-{int(time.time() * 1000)}",
            model=req.model,
            provider=self.name,
            content=data["message"]["content"],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            finish_reason="stop" if data.get("done") else "length",
            latency_ms=latency_ms,
        )

    async def chat_stream(
        self, req: ChatRequest, wire_model: str
    ) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": wire_model,
            "messages": [m.model_dump() for m in req.messages],
            "stream": True,
        }
        async with self._client.stream("POST", f"{self._base_url}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                done = data.get("done", False)
                yield StreamChunk(
                    delta=data.get("message", {}).get("content", ""),
                    finish_reason="stop" if done else None,
                )

    async def health_check(self, wire_model: str) -> HealthProbeResult:
        start = time.monotonic()
        try:
            resp = await self._client.get(f"{self._base_url}/api/version", timeout=5.0)
            resp.raise_for_status()
            return HealthProbeResult(
                healthy=True, latency_ms=(time.monotonic() - start) * 1000
            )
        except Exception as exc:  # noqa: BLE001 - health probes must never raise
            return HealthProbeResult(
                healthy=False,
                latency_ms=(time.monotonic() - start) * 1000,
                error=str(exc),
            )
