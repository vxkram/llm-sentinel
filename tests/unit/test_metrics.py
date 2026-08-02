from llm_sentinel.observability.metrics import (
    CIRCUIT_BREAKER_STATE,
    CIRCUIT_BREAKER_TRANSITIONS_TOTAL,
    metrics_payload,
    record_circuit_breaker_transition,
)


def test_record_transition_increments_counter_and_sets_state_gauge() -> None:
    record_circuit_breaker_transition("test-provider", "test-model-a", "closed", "open")

    counter_value = CIRCUIT_BREAKER_TRANSITIONS_TOTAL.labels(
        provider="test-provider", model="test-model-a", from_state="closed", to_state="open"
    )._value.get()
    gauge_value = CIRCUIT_BREAKER_STATE.labels(provider="test-provider", model="test-model-a")._value.get()

    assert counter_value == 1
    assert gauge_value == 2  # open == 2 per _STATE_TO_NUMERIC


def test_metrics_payload_is_valid_prometheus_text_format() -> None:
    record_circuit_breaker_transition("test-provider", "test-model-b", "closed", "open")

    payload, content_type = metrics_payload()

    assert "text/plain" in content_type
    body = payload.decode()
    assert "llm_sentinel_circuit_breaker_transitions_total" in body
    assert 'provider="test-provider"' in body
