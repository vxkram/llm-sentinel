import asyncio

from llm_sentinel.priority.concurrency import (
    BATCH_CONCURRENCY,
    REALTIME_CONCURRENCY,
    semaphore_for,
)


def test_realtime_and_batch_get_distinct_semaphores() -> None:
    assert semaphore_for("realtime") is not semaphore_for("batch")


def test_unknown_priority_falls_back_to_realtime() -> None:
    assert semaphore_for("something-unexpected") is semaphore_for("realtime")


def test_same_priority_returns_the_same_semaphore_instance() -> None:
    # Callers across requests must share one semaphore per priority tier,
    # not get a fresh one each call - otherwise the cap wouldn't be enforced.
    assert semaphore_for("batch") is semaphore_for("batch")


async def test_batch_semaphore_caps_concurrent_holders() -> None:
    sem = semaphore_for("batch")
    concurrent = 0
    max_seen = 0

    async def hold():
        nonlocal concurrent, max_seen
        async with sem:
            concurrent += 1
            max_seen = max(max_seen, concurrent)
            await asyncio.sleep(0.05)  # force real overlap between holders
            concurrent -= 1

    await asyncio.gather(*(hold() for _ in range(BATCH_CONCURRENCY + 5)))

    # With more callers than capacity and a shared sleep window, the cap
    # should actually be reached, not just never exceeded by coincidence.
    assert max_seen == BATCH_CONCURRENCY


def test_realtime_capacity_is_larger_than_batch() -> None:
    assert REALTIME_CONCURRENCY > BATCH_CONCURRENCY
