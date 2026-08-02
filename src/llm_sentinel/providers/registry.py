from llm_sentinel.core.config import RoutingConfig, Settings
from llm_sentinel.providers.anthropic_client import AnthropicClient
from llm_sentinel.providers.base import ProviderClient
from llm_sentinel.providers.ollama import OllamaClient
from llm_sentinel.providers.openai_client import OpenAIClient


class ModelNotFoundError(Exception):
    pass


class ProviderRegistry:
    def __init__(self, routing: RoutingConfig, settings: Settings):
        self._routing = routing
        self._clients: dict[str, ProviderClient] = {
            "ollama": OllamaClient(settings.ollama_base_url),
            "openai": OpenAIClient(settings.openai_mock_base_url),
            "anthropic": AnthropicClient(settings.anthropic_mock_base_url),
        }

    def resolve(self, canonical_model: str) -> tuple[ProviderClient, str]:
        entry = self._routing.models.get(canonical_model)
        if entry is None:
            raise ModelNotFoundError(f"unknown model: {canonical_model}")
        client = self._clients.get(entry.provider)
        if client is None:
            raise ModelNotFoundError(f"no client registered for provider: {entry.provider}")
        return client, entry.wire_model

    def fallback_chain(self, canonical_model: str) -> list[str]:
        entry = self._routing.models.get(canonical_model)
        if entry is None:
            raise ModelNotFoundError(f"unknown model: {canonical_model}")
        tier = self._routing.tiers.get(entry.tier)
        if tier is None:
            return [canonical_model]
        return tier.fallback_chain
