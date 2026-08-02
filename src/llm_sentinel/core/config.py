from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gateway_port: int = 8010
    ollama_base_url: str = "http://localhost:11434"
    openai_mock_base_url: str = "http://localhost:8011"
    anthropic_mock_base_url: str = "http://localhost:8012"
    redis_url: str = "redis://localhost:6379/0"

    teams_config_path: str = "configs/teams.yaml"
    routing_config_path: str = "configs/routing.yaml"
    pricing_config_path: str = "configs/pricing.yaml"


class ModelEntry(BaseModel):
    provider: str
    wire_model: str
    tier: str


class Tier(BaseModel):
    fallback_chain: list[str]


class RoutingConfig(BaseModel):
    models: dict[str, ModelEntry]
    tiers: dict[str, Tier]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_routing_config(path: str | None = None) -> RoutingConfig:
    settings = get_settings()
    config_path = Path(path or settings.routing_config_path)
    raw = yaml.safe_load(config_path.read_text())
    return RoutingConfig.model_validate(raw)
