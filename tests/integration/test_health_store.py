import pytest
from redis.asyncio import Redis

from llm_sentinel.health_checker.store import HealthStore
from tests.redis_helpers import requires_redis

pytestmark = requires_redis

PROVIDER = "test-provider"
MODEL = "test-model-health"


@pytest.fixture
async def store():
    client = Redis.from_url("redis://localhost:6379/0")
    await client.delete(f"health:{PROVIDER}:{MODEL}:history")
    yield HealthStore(client)
    await client.aclose()


async def test_status_unknown_with_no_samples(store: HealthStore) -> None:
    assert await store.status(PROVIDER, MODEL) == {"status": "unknown", "sample_size": 0}


async def test_status_healthy_when_all_probes_succeed(store: HealthStore) -> None:
    for _ in range(5):
        await store.record(PROVIDER, MODEL, healthy=True, latency_ms=10.0, error=None)

    status = await store.status(PROVIDER, MODEL)

    assert status["status"] == "healthy"
    assert status["sample_size"] == 5


async def test_status_down_when_all_probes_fail(store: HealthStore) -> None:
    for _ in range(5):
        await store.record(PROVIDER, MODEL, healthy=False, latency_ms=10.0, error="boom")

    status = await store.status(PROVIDER, MODEL)

    assert status["status"] == "down"


async def test_status_degraded_with_mixed_results(store: HealthStore) -> None:
    for _ in range(3):
        await store.record(PROVIDER, MODEL, healthy=True, latency_ms=10.0, error=None)
    for _ in range(3):
        await store.record(PROVIDER, MODEL, healthy=False, latency_ms=10.0, error="boom")

    status = await store.status(PROVIDER, MODEL)

    assert status["status"] == "degraded"
