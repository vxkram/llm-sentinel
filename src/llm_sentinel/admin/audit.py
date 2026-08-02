import json
import time
from pathlib import Path

from redis.asyncio import Redis

STREAM_KEY = "audit:log"
MAX_STREAM_LEN = 1000


def _decode(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


class AuditLog:
    """Every admin mutation is recorded twice: a Redis Stream (bounded,
    queryable, but volatile - gone on `docker-compose down -v`) and a JSONL
    file mirror, so a portfolio reviewer can see history survive a restart
    without standing up a full database just for an audit trail.
    """

    def __init__(self, redis_client: Redis, jsonl_path: str):
        self._redis = redis_client
        self._path = Path(jsonl_path)

    async def record(self, actor: str, action: str, team_id: str, before: dict, after: dict) -> None:
        entry = {
            "ts": time.time(),
            "actor": actor,
            "action": action,
            "team_id": team_id,
            "before": before,
            "after": after,
        }
        payload = json.dumps(entry)
        await self._redis.xadd(STREAM_KEY, {"data": payload}, maxlen=MAX_STREAM_LEN, approximate=True)
        with self._path.open("a") as f:
            f.write(payload + "\n")

    async def recent(self, limit: int = 50) -> list[dict]:
        entries = await self._redis.xrevrange(STREAM_KEY, count=limit)
        return [json.loads(_decode(fields.get("data", fields.get(b"data")))) for _, fields in entries]
