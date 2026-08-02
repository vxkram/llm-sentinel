import time
from pathlib import Path

from redis.asyncio import Redis

_SCRIPTS_DIR = Path(__file__).parent / "scripts"


class RateLimitResult:
    def __init__(self, allowed: bool, remaining: float, retry_after: float):
        self.allowed = allowed
        self.remaining = remaining
        self.retry_after = retry_after


class TokenBucket:
    def __init__(self, redis_client: Redis):
        self._redis = redis_client
        self._check_script = redis_client.register_script(
            (_SCRIPTS_DIR / "token_bucket.lua").read_text()
        )
        self._reconcile_script = redis_client.register_script(
            (_SCRIPTS_DIR / "token_bucket_reconcile.lua").read_text()
        )

    async def check_and_consume(
        self, key: str, capacity: float, refill_rate_per_sec: float, requested: float
    ) -> RateLimitResult:
        now_ms = int(time.time() * 1000)
        allowed, tokens, retry_after = await self._check_script(
            keys=[key], args=[capacity, refill_rate_per_sec, requested, now_ms]
        )
        return RateLimitResult(
            allowed=bool(int(allowed)),
            remaining=float(tokens),
            retry_after=float(retry_after),
        )

    async def reconcile(
        self,
        key: str,
        capacity: float,
        refill_rate_per_sec: float,
        estimated: float,
        actual: float,
    ) -> None:
        now_ms = int(time.time() * 1000)
        await self._reconcile_script(
            keys=[key], args=[capacity, refill_rate_per_sec, estimated, actual, now_ms]
        )

    async def peek(self, key: str) -> float | None:
        """Read-only: current token count without consuming or refilling.
        For admin/dashboard use, distinct from check_and_consume.
        """
        tokens = await self._redis.hget(key, "tokens")
        return float(tokens) if tokens is not None else None
