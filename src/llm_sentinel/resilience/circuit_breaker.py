import time
from pathlib import Path

from redis.asyncio import Redis

from llm_sentinel.observability.metrics import record_circuit_breaker_transition

_SCRIPTS_DIR = Path(__file__).parent / "scripts"

FAILURE_THRESHOLD = 3
WINDOW_MS = 30_000
COOLDOWN_MS = 15_000
TRIAL_TTL_MS = 10_000


def _decode(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


class CircuitBreaker:
    """Redis-shared CLOSED -> OPEN -> HALF_OPEN -> CLOSED state machine, keyed
    per (provider, wire_model). Shared rather than in-memory-per-process so
    it stays correct if the gateway ever runs multiple replicas.
    """

    def __init__(self, redis_client: Redis):
        self._redis = redis_client
        self._check_script = redis_client.register_script(
            (_SCRIPTS_DIR / "cb_check.lua").read_text()
        )
        self._record_failure_script = redis_client.register_script(
            (_SCRIPTS_DIR / "cb_record_failure.lua").read_text()
        )
        self._record_success_script = redis_client.register_script(
            (_SCRIPTS_DIR / "cb_record_success.lua").read_text()
        )

    @staticmethod
    def _keys(provider: str, model: str) -> tuple[str, str, str]:
        return (
            f"cb:{provider}:{model}:state",
            f"cb:{provider}:{model}:failures",
            f"cb:{provider}:{model}:trial",
        )

    async def check(self, provider: str, model: str) -> str:
        state_key, _, trial_key = self._keys(provider, model)
        now_ms = int(time.time() * 1000)
        result = await self._check_script(
            keys=[state_key, trial_key], args=[now_ms, COOLDOWN_MS, TRIAL_TTL_MS]
        )
        decision = _decode(result)
        if decision == "half_open_trial":
            # The only decision that unambiguously means "just transitioned":
            # it's returned exactly once, right after the state flips.
            record_circuit_breaker_transition(provider, model, "open", "half_open")
        return decision

    async def record_success(self, provider: str, model: str) -> None:
        state_key, failures_key, trial_key = self._keys(provider, model)
        to_state, from_state = await self._record_success_script(
            keys=[state_key, failures_key, trial_key], args=[]
        )
        to_state, from_state = _decode(to_state), _decode(from_state)
        if to_state != from_state:
            record_circuit_breaker_transition(provider, model, from_state, to_state)

    async def record_failure(self, provider: str, model: str) -> str:
        state_key, failures_key, trial_key = self._keys(provider, model)
        now_ms = int(time.time() * 1000)
        to_state, from_state = await self._record_failure_script(
            keys=[failures_key, state_key, trial_key],
            args=[now_ms, WINDOW_MS, FAILURE_THRESHOLD],
        )
        to_state, from_state = _decode(to_state), _decode(from_state)
        if to_state != from_state:
            record_circuit_breaker_transition(provider, model, from_state, to_state)
        return to_state

    async def status(self, provider: str, model: str) -> dict:
        """Read-only snapshot for admin/dashboard use. Deliberately separate
        from check() - that method can claim the half-open trial ticket as a
        side effect, which a status poll must never do.
        """
        state_key, failures_key, _ = self._keys(provider, model)
        state = await self._redis.hget(state_key, "state")
        opened_at = await self._redis.hget(state_key, "opened_at")
        failure_count = await self._redis.zcard(failures_key)
        return {
            "state": _decode(state) if state is not None else "closed",
            "opened_at": float(opened_at) if opened_at is not None else None,
            "recent_failure_count": failure_count,
        }
