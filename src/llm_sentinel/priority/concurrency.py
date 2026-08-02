import asyncio

REALTIME_CONCURRENCY = 50
BATCH_CONCURRENCY = 5

# In-memory, per-process. A real multi-replica deployment would need this
# shared (e.g. Redis-based), but per-process is the right amount of
# complexity for what this is: a soft concurrency cap keeping batch traffic
# from starving realtime traffic, not a durable scheduler or queue.
_semaphores: dict[str, asyncio.Semaphore] = {
    "realtime": asyncio.Semaphore(REALTIME_CONCURRENCY),
    "batch": asyncio.Semaphore(BATCH_CONCURRENCY),
}


def semaphore_for(priority: str) -> asyncio.Semaphore:
    return _semaphores.get(priority, _semaphores["realtime"])
