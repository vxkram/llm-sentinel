import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

T = TypeVar("T")


def is_retryable(exc: Exception) -> bool:
    """Retryable: timeouts, connection errors, and 5xx/408/429 responses -
    transient failures likely to succeed on a retry or a different provider.
    Non-retryable: 4xx auth/content errors (401/403/400) - retrying or
    falling back won't fix a request the provider has actively rejected.
    """
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return False


async def with_retry(
    fn: Callable[[], Awaitable[T]], max_retries: int = 3, base_delay: float = 0.1
) -> T:
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:
            if not is_retryable(exc) or attempt >= max_retries:
                raise
            delay = base_delay * (2**attempt) + random.uniform(0, base_delay)
            await asyncio.sleep(delay)
            attempt += 1
