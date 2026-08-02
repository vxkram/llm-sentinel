import pytest

from llm_sentinel.providers.base import (
    ChatRequest,
    ChatResponse,
    Message,
    StreamChunk,
    Usage,
)
from llm_sentinel.resilience.fallback import (
    AllProvidersUnavailableError,
    dispatch_stream_with_fallback,
    dispatch_with_fallback,
)


class FakeClient:
    def __init__(self, name: str, should_fail: bool = False):
        self.name = name
        self.should_fail = should_fail
        self.calls = 0

    async def chat(self, req, wire_model):
        self.calls += 1
        if self.should_fail:
            raise RuntimeError(f"{self.name} is down")
        return ChatResponse(
            id="x",
            model=req.model,
            provider=self.name,
            content="ok",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            finish_reason="stop",
            latency_ms=1.0,
        )

    async def chat_stream(self, req, wire_model):
        self.calls += 1
        if self.should_fail:
            raise RuntimeError(f"{self.name} is down")
        yield StreamChunk(delta="hi")
        yield StreamChunk(delta="", finish_reason="stop")


class FakeRegistry:
    def __init__(self, clients: dict[str, FakeClient]):
        self._clients = clients

    def resolve(self, model: str):
        return self._clients[model], model


class FakeCircuitBreaker:
    def __init__(self, open_models: set[str] | None = None):
        self.open_models = open_models or set()
        self.failures: list[str] = []
        self.successes: list[str] = []

    async def check(self, provider: str, model: str) -> str:
        return "open" if model in self.open_models else "closed"

    async def record_failure(self, provider: str, model: str) -> str:
        self.failures.append(model)
        return "open"

    async def record_success(self, provider: str, model: str) -> None:
        self.successes.append(model)


def _req(model: str) -> ChatRequest:
    return ChatRequest(model=model, messages=[Message(role="user", content="hi")])


async def test_first_candidate_succeeds_no_fallback_needed() -> None:
    registry = FakeRegistry({"a": FakeClient("provider-a")})
    breaker = FakeCircuitBreaker()

    resp, served = await dispatch_with_fallback(registry, breaker, ["a"], _req("a"))

    assert served == "a"
    assert resp.content == "ok"
    assert breaker.successes == ["a"]
    assert breaker.failures == []


async def test_falls_back_when_first_candidate_fails() -> None:
    failing = FakeClient("provider-a", should_fail=True)
    healthy = FakeClient("provider-b")
    registry = FakeRegistry({"a": failing, "b": healthy})
    breaker = FakeCircuitBreaker()

    _resp, served = await dispatch_with_fallback(registry, breaker, ["a", "b"], _req("a"))

    assert served == "b"
    assert breaker.failures == ["a"]
    assert breaker.successes == ["b"]


async def test_skips_candidate_with_open_circuit_without_calling_it() -> None:
    would_fail_but_skipped = FakeClient("provider-a", should_fail=True)
    healthy = FakeClient("provider-b")
    registry = FakeRegistry({"a": would_fail_but_skipped, "b": healthy})
    breaker = FakeCircuitBreaker(open_models={"a"})

    _resp, served = await dispatch_with_fallback(registry, breaker, ["a", "b"], _req("a"))

    assert served == "b"
    assert would_fail_but_skipped.calls == 0
    assert breaker.failures == []


async def test_raises_when_all_candidates_unavailable() -> None:
    registry = FakeRegistry({"a": FakeClient("provider-a", should_fail=True)})
    breaker = FakeCircuitBreaker()

    with pytest.raises(AllProvidersUnavailableError):
        await dispatch_with_fallback(registry, breaker, ["a"], _req("a"))


async def test_stream_falls_back_before_first_chunk_reaches_client() -> None:
    failing = FakeClient("provider-a", should_fail=True)
    healthy = FakeClient("provider-b")
    registry = FakeRegistry({"a": failing, "b": healthy})
    breaker = FakeCircuitBreaker()

    stream, served = await dispatch_stream_with_fallback(registry, breaker, ["a", "b"], _req("a"))
    chunks = [c async for c in stream]

    assert served == "b"
    assert breaker.failures == ["a"]
    assert breaker.successes == ["b"]
    assert "".join(c.delta for c in chunks) == "hi"
