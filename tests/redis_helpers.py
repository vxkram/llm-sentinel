import socket

import pytest


def redis_available(host: str = "localhost", port: int = 6379) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


requires_redis = pytest.mark.skipif(
    not redis_available(), reason="Redis not reachable on localhost:6379 - start it with `redis-server`"
)
