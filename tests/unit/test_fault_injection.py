import pytest
from fastapi import HTTPException

from mock_providers.fault_injection import FaultConfig, FaultState


async def test_no_fault_by_default() -> None:
    state = FaultState()
    await state.maybe_raise_or_delay()  # should not raise
    assert not state.should_reset()


async def test_error_mode_raises_configured_status() -> None:
    state = FaultState()
    state.config = FaultConfig(mode="error", status_code=503, rate=1.0)
    with pytest.raises(HTTPException) as exc_info:
        await state.maybe_raise_or_delay()
    assert exc_info.value.status_code == 503


async def test_error_mode_rate_zero_never_triggers() -> None:
    state = FaultState()
    state.config = FaultConfig(mode="error", status_code=503, rate=0.0)
    await state.maybe_raise_or_delay()  # should not raise


def test_should_reset_only_in_connection_reset_mode() -> None:
    state = FaultState()
    state.config = FaultConfig(mode="connection_reset", rate=1.0)
    assert state.should_reset()

    state.config = FaultConfig(mode="error", rate=1.0)
    assert not state.should_reset()
