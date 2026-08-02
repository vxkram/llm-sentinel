import httpx
import pytest

from llm_sentinel.resilience.retry import is_retryable, with_retry


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://example.test")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
def test_retryable_status_codes(status_code: int) -> None:
    assert is_retryable(_status_error(status_code))


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_non_retryable_status_codes(status_code: int) -> None:
    assert not is_retryable(_status_error(status_code))


def test_timeout_is_retryable() -> None:
    assert is_retryable(httpx.ConnectTimeout("timed out"))


def test_transport_error_is_retryable() -> None:
    assert is_retryable(httpx.ConnectError("connection refused"))


def test_generic_exception_is_not_retryable() -> None:
    assert not is_retryable(ValueError("not an http error"))


async def test_with_retry_succeeds_after_transient_failures() -> None:
    attempts = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _status_error(503)
        return "ok"

    result = await with_retry(flaky, max_retries=3, base_delay=0.001)

    assert result == "ok"
    assert attempts == 3


async def test_with_retry_raises_after_exhausting_retries() -> None:
    async def always_fails():
        raise _status_error(503)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(always_fails, max_retries=2, base_delay=0.001)


async def test_with_retry_does_not_retry_non_retryable_errors() -> None:
    attempts = 0

    async def fails_with_403():
        nonlocal attempts
        attempts += 1
        raise _status_error(403)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(fails_with_403, max_retries=3, base_delay=0.001)

    assert attempts == 1
