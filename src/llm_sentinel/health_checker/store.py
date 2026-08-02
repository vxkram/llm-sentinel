import json
import time

from redis.asyncio import Redis

MAX_HISTORY = 200


class HealthStore:
    """Bounded Redis rolling window per (provider, model). Only recent
    samples are kept here for current-status computation; long-term trend
    history is Prometheus/Grafana's job, not this store's.
    """

    def __init__(self, redis_client: Redis):
        self._redis = redis_client

    @staticmethod
    def _key(provider: str, model: str) -> str:
        return f"health:{provider}:{model}:history"

    async def record(
        self, provider: str, model: str, healthy: bool, latency_ms: float, error: str | None
    ) -> None:
        key = self._key(provider, model)
        entry = json.dumps(
            {"ts": time.time(), "healthy": healthy, "latency_ms": latency_ms, "error": error}
        )
        await self._redis.lpush(key, entry)
        await self._redis.ltrim(key, 0, MAX_HISTORY - 1)
        await self._redis.expire(key, 3600)

    async def status(self, provider: str, model: str) -> dict:
        raw_entries = await self._redis.lrange(self._key(provider, model), 0, MAX_HISTORY - 1)
        entries = [json.loads(e) for e in raw_entries]
        if not entries:
            return {"status": "unknown", "sample_size": 0}

        healthy_ratio = sum(1 for e in entries if e["healthy"]) / len(entries)
        if healthy_ratio >= 0.9:
            status = "healthy"
        elif healthy_ratio >= 0.5:
            status = "degraded"
        else:
            status = "down"

        latencies = sorted(e["latency_ms"] for e in entries)
        p99_index = max(0, int(len(latencies) * 0.99) - 1)

        return {
            "status": status,
            "sample_size": len(entries),
            "healthy_ratio": healthy_ratio,
            "p99_latency_ms": latencies[p99_index],
        }
