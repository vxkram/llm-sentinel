from llm_sentinel.core.config import PricingConfig


def compute_cost(pricing: PricingConfig, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    entry = pricing.pricing.get(model)
    if entry is None:
        return 0.0
    return (prompt_tokens / 1000) * entry.input_per_1k + (completion_tokens / 1000) * entry.output_per_1k
