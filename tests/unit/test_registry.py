import pytest

from llm_sentinel.core.config import ModelEntry, RoutingConfig, Settings, Tier
from llm_sentinel.providers.registry import ModelNotFoundError, ProviderRegistry


@pytest.fixture
def registry() -> ProviderRegistry:
    routing = RoutingConfig(
        models={
            "llama3.2": ModelEntry(provider="ollama", wire_model="llama3.2:1b", tier="fast"),
        },
        tiers={"fast": Tier(fallback_chain=["llama3.2"])},
    )
    return ProviderRegistry(routing, Settings())


def test_resolve_known_model(registry: ProviderRegistry) -> None:
    client, wire_model = registry.resolve("llama3.2")
    assert client.name == "ollama"
    assert wire_model == "llama3.2:1b"


def test_resolve_unknown_model_raises(registry: ProviderRegistry) -> None:
    with pytest.raises(ModelNotFoundError):
        registry.resolve("gpt-5-nonexistent")


def test_fallback_chain(registry: ProviderRegistry) -> None:
    assert registry.fallback_chain("llama3.2") == ["llama3.2"]


def test_fallback_chain_unknown_model_raises(registry: ProviderRegistry) -> None:
    with pytest.raises(ModelNotFoundError):
        registry.fallback_chain("gpt-5-nonexistent")
