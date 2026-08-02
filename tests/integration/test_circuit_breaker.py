import time

import pytest
from redis.asyncio import Redis

from llm_sentinel.resilience.circuit_breaker import (
    COOLDOWN_MS,
    FAILURE_THRESHOLD,
    CircuitBreaker,
)
from tests.redis_helpers import requires_redis

pytestmark = requires_redis

PROVIDER = "test-provider"
MODEL = "test-model-cb"


@pytest.fixture
async def breaker():
    client = Redis.from_url("redis://localhost:6379/0")
    keys = await client.keys(f"cb:{PROVIDER}:{MODEL}:*")
    if keys:
        await client.delete(*keys)
    yield CircuitBreaker(client)
    await client.aclose()


async def _force_open_past_cooldown(breaker: CircuitBreaker) -> None:
    state_key = f"cb:{PROVIDER}:{MODEL}:state"
    now_ms = int(time.time() * 1000)
    await breaker._redis.hset(
        state_key, mapping={"state": "open", "opened_at": now_ms - COOLDOWN_MS - 1000}
    )


async def test_closed_by_default(breaker: CircuitBreaker) -> None:
    assert await breaker.check(PROVIDER, MODEL) == "closed"


async def test_opens_after_threshold_failures(breaker: CircuitBreaker) -> None:
    for _ in range(FAILURE_THRESHOLD):
        await breaker.record_failure(PROVIDER, MODEL)

    assert await breaker.check(PROVIDER, MODEL) == "open"


async def test_stays_closed_below_threshold(breaker: CircuitBreaker) -> None:
    for _ in range(FAILURE_THRESHOLD - 1):
        await breaker.record_failure(PROVIDER, MODEL)

    assert await breaker.check(PROVIDER, MODEL) == "closed"


async def test_stays_open_within_cooldown(breaker: CircuitBreaker) -> None:
    for _ in range(FAILURE_THRESHOLD):
        await breaker.record_failure(PROVIDER, MODEL)

    assert await breaker.check(PROVIDER, MODEL) == "open"
    assert await breaker.check(PROVIDER, MODEL) == "open"


async def test_transitions_to_half_open_after_cooldown_elapsed(breaker: CircuitBreaker) -> None:
    await _force_open_past_cooldown(breaker)

    assert await breaker.check(PROVIDER, MODEL) == "half_open_trial"


async def test_only_one_caller_wins_the_half_open_trial(breaker: CircuitBreaker) -> None:
    await _force_open_past_cooldown(breaker)

    results = [await breaker.check(PROVIDER, MODEL) for _ in range(3)]

    assert results.count("half_open_trial") == 1
    assert results.count("open") == 2


async def test_half_open_success_closes_the_circuit(breaker: CircuitBreaker) -> None:
    await _force_open_past_cooldown(breaker)
    assert await breaker.check(PROVIDER, MODEL) == "half_open_trial"

    await breaker.record_success(PROVIDER, MODEL)

    assert await breaker.check(PROVIDER, MODEL) == "closed"


async def test_half_open_failure_reopens_the_circuit(breaker: CircuitBreaker) -> None:
    await _force_open_past_cooldown(breaker)
    assert await breaker.check(PROVIDER, MODEL) == "half_open_trial"

    await breaker.record_failure(PROVIDER, MODEL)

    assert await breaker.check(PROVIDER, MODEL) == "open"
