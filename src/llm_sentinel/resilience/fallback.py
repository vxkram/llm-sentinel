import logging
from collections.abc import AsyncIterator

from llm_sentinel.providers.base import ChatRequest, ChatResponse, StreamChunk
from llm_sentinel.providers.registry import ProviderRegistry
from llm_sentinel.resilience.circuit_breaker import CircuitBreaker
from llm_sentinel.resilience.retry import with_retry

logger = logging.getLogger(__name__)


class AllProvidersUnavailableError(Exception):
    pass


async def dispatch_with_fallback(
    registry: ProviderRegistry,
    breaker: CircuitBreaker,
    candidates: list[str],
    req: ChatRequest,
) -> tuple[ChatResponse, str]:
    """Tries each candidate model in order, skipping any whose circuit
    breaker is open, retrying transient failures within a candidate before
    moving to the next one. Returns the response plus the canonical model
    that actually served it, which may differ from candidates[0] if that one
    was unavailable.
    """
    last_error: Exception | None = None

    for candidate in candidates:
        client, wire_model = registry.resolve(candidate)
        if await breaker.check(client.name, wire_model) == "open":
            logger.info("skipping %s/%s: circuit breaker open", client.name, wire_model)
            continue

        try:
            resp = await with_retry(lambda c=client, w=wire_model: c.chat(req, w))
        except Exception as exc:  # noqa: BLE001 - any failure means try the next candidate
            await breaker.record_failure(client.name, wire_model)
            logger.warning(
                "provider %s/%s failed, trying next fallback candidate: %s",
                client.name,
                wire_model,
                exc,
            )
            last_error = exc
            continue

        await breaker.record_success(client.name, wire_model)
        return resp, candidate

    raise AllProvidersUnavailableError(
        f"all providers in fallback chain {candidates} are unavailable"
    ) from last_error


async def dispatch_stream_with_fallback(
    registry: ProviderRegistry,
    breaker: CircuitBreaker,
    candidates: list[str],
    req: ChatRequest,
) -> tuple[AsyncIterator[StreamChunk], str]:
    """Streaming counterpart to dispatch_with_fallback. Fallback only applies
    before the first chunk reaches the client: once real bytes are in
    flight, a mid-stream failure can't be silently retried on a different
    provider without corrupting the response the client already started
    receiving, so from that point on a failure is only recorded against the
    circuit breaker, not retried within this request.
    """
    last_error: Exception | None = None

    for candidate in candidates:
        client, wire_model = registry.resolve(candidate)
        if await breaker.check(client.name, wire_model) == "open":
            logger.info("skipping %s/%s: circuit breaker open", client.name, wire_model)
            continue

        gen = client.chat_stream(req, wire_model)
        try:
            first_chunk = await gen.__anext__()
        except Exception as exc:  # noqa: BLE001 - any failure means try the next candidate
            await breaker.record_failure(client.name, wire_model)
            logger.warning(
                "provider %s/%s failed before first chunk, trying next fallback candidate: %s",
                client.name,
                wire_model,
                exc,
            )
            last_error = exc
            continue

        async def _rest(
            first=first_chunk, remaining=gen, provider_name=client.name, model_name=wire_model
        ) -> AsyncIterator[StreamChunk]:
            yield first
            try:
                async for chunk in remaining:
                    yield chunk
            except Exception:
                await breaker.record_failure(provider_name, model_name)
                raise
            else:
                await breaker.record_success(provider_name, model_name)

        return _rest(), candidate

    raise AllProvidersUnavailableError(
        f"all providers in fallback chain {candidates} are unavailable"
    ) from last_error
