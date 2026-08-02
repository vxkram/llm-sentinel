from collections.abc import AsyncIterator
from typing import Literal, Protocol

from pydantic import BaseModel


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    id: str
    model: str
    provider: str
    content: str
    usage: Usage
    finish_reason: str
    latency_ms: float


class StreamChunk(BaseModel):
    delta: str
    finish_reason: str | None = None


class HealthProbeResult(BaseModel):
    healthy: bool
    latency_ms: float
    error: str | None = None


class ProviderClient(Protocol):
    name: str

    async def chat(self, req: ChatRequest, wire_model: str) -> ChatResponse: ...

    def chat_stream(
        self, req: ChatRequest, wire_model: str
    ) -> AsyncIterator[StreamChunk]: ...

    async def health_check(self, wire_model: str) -> HealthProbeResult: ...
