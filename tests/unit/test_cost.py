import pytest

from llm_sentinel.budget.cost import compute_cost
from llm_sentinel.core.config import PricingConfig, PricingEntry


def test_compute_cost_known_model() -> None:
    pricing = PricingConfig(
        pricing={"gpt-4o-mini": PricingEntry(input_per_1k=0.00015, output_per_1k=0.0006)}
    )

    cost = compute_cost(pricing, "gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000)

    assert cost == pytest.approx(0.00015 + 0.0006)


def test_compute_cost_unknown_model_is_free() -> None:
    pricing = PricingConfig(pricing={})

    cost = compute_cost(pricing, "mystery-model", prompt_tokens=1000, completion_tokens=1000)

    assert cost == 0.0


def test_compute_cost_scales_with_partial_thousands() -> None:
    pricing = PricingConfig(
        pricing={"claude-3-5-sonnet": PricingEntry(input_per_1k=0.003, output_per_1k=0.015)}
    )

    cost = compute_cost(pricing, "claude-3-5-sonnet", prompt_tokens=500, completion_tokens=100)

    assert cost == pytest.approx(0.5 * 0.003 + 0.1 * 0.015)
