from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry()

REQUESTS_TOTAL = Counter(
    "llm_sentinel_requests_total",
    "Total chat completion requests",
    ["team", "model", "provider"],
    registry=REGISTRY,
)
ERRORS_TOTAL = Counter(
    "llm_sentinel_errors_total",
    "Total failed requests",
    ["team", "model", "provider", "error_type"],
    registry=REGISTRY,
)
REQUEST_LATENCY_SECONDS = Histogram(
    "llm_sentinel_request_latency_seconds",
    "End-to-end provider dispatch latency",
    ["team", "model", "provider"],
    registry=REGISTRY,
)
TOKENS_TOTAL = Counter(
    "llm_sentinel_tokens_total",
    "Tokens processed",
    ["team", "model", "direction"],  # direction: prompt | completion
    registry=REGISTRY,
)
COST_USD_TOTAL = Counter(
    "llm_sentinel_cost_usd_total",
    "Cost charged in USD",
    ["team"],
    registry=REGISTRY,
)
FALLBACK_TRIGGERED_TOTAL = Counter(
    "llm_sentinel_fallback_triggered_total",
    "Times a request was served by a non-primary fallback model",
    ["from_model", "to_model"],
    registry=REGISTRY,
)
CIRCUIT_BREAKER_TRANSITIONS_TOTAL = Counter(
    "llm_sentinel_circuit_breaker_transitions_total",
    "Circuit breaker state transitions",
    ["provider", "model", "from_state", "to_state"],
    registry=REGISTRY,
)
CIRCUIT_BREAKER_STATE = Gauge(
    "llm_sentinel_circuit_breaker_state",
    "Current circuit breaker state (0=closed, 1=half_open, 2=open)",
    ["provider", "model"],
    registry=REGISTRY,
)
PROVIDER_HEALTHY = Gauge(
    "llm_sentinel_provider_healthy",
    "1 if the last health probe succeeded, else 0",
    ["provider", "model"],
    registry=REGISTRY,
)
BUDGET_UTILIZATION_RATIO = Gauge(
    "llm_sentinel_budget_utilization_ratio",
    "Fraction of budget used",
    ["team", "period"],  # period: daily | monthly
    registry=REGISTRY,
)

_STATE_TO_NUMERIC = {"closed": 0, "half_open": 1, "open": 2}


def record_circuit_breaker_transition(provider: str, model: str, from_state: str, to_state: str) -> None:
    CIRCUIT_BREAKER_TRANSITIONS_TOTAL.labels(
        provider=provider, model=model, from_state=from_state, to_state=to_state
    ).inc()
    if to_state in _STATE_TO_NUMERIC:
        CIRCUIT_BREAKER_STATE.labels(provider=provider, model=model).set(_STATE_TO_NUMERIC[to_state])


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
