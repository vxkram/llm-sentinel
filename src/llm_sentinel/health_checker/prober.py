import asyncio
import logging

from llm_sentinel.health_checker.store import HealthStore
from llm_sentinel.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

PROBE_INTERVAL_SECONDS = 30


async def run_health_prober(registry: ProviderRegistry, store: HealthStore) -> None:
    """Background task: probes every configured model's provider on a fixed
    interval and records the result. Runs until cancelled at shutdown.
    """
    while True:
        for canonical_model in registry.canonical_models():
            client, wire_model = registry.resolve(canonical_model)
            try:
                result = await client.health_check(wire_model)
                await store.record(client.name, wire_model, result.healthy, result.latency_ms, result.error)
            except Exception:
                logger.exception("health probe bookkeeping failed for %s/%s", client.name, wire_model)
        await asyncio.sleep(PROBE_INTERVAL_SECONDS)
