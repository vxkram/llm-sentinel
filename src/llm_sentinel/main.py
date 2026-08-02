import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from llm_sentinel.api.v1 import chat, health
from llm_sentinel.budget.tracker import BudgetTracker
from llm_sentinel.core.config import (
    get_settings,
    load_pricing_config,
    load_routing_config,
)
from llm_sentinel.core.security import TeamsStore
from llm_sentinel.health_checker.prober import run_health_prober
from llm_sentinel.health_checker.store import HealthStore
from llm_sentinel.providers.registry import ProviderRegistry
from llm_sentinel.ratelimit.token_bucket import TokenBucket
from llm_sentinel.resilience.circuit_breaker import CircuitBreaker


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    routing = load_routing_config()
    app.state.registry = ProviderRegistry(routing, settings)
    app.state.teams_store = TeamsStore(settings.teams_config_path)
    app.state.pricing = load_pricing_config()

    redis_client = Redis.from_url(settings.redis_url)
    app.state.redis = redis_client
    app.state.token_bucket = TokenBucket(redis_client)
    app.state.budget_tracker = BudgetTracker(redis_client)
    app.state.circuit_breaker = CircuitBreaker(redis_client)
    app.state.health_store = HealthStore(redis_client)

    prober_task = asyncio.create_task(
        run_health_prober(app.state.registry, app.state.health_store)
    )

    yield

    prober_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await prober_task
    await redis_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="llm-sentinel", lifespan=lifespan)
    app.include_router(health.router, prefix="")
    app.include_router(chat.router, prefix="/v1")
    return app


app = create_app()
