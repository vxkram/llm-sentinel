from contextlib import asynccontextmanager

from fastapi import FastAPI

from llm_sentinel.api.v1 import chat, health
from llm_sentinel.core.config import get_settings, load_routing_config
from llm_sentinel.providers.registry import ProviderRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    routing = load_routing_config()
    app.state.registry = ProviderRegistry(routing, settings)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="llm-sentinel", lifespan=lifespan)
    app.include_router(health.router, prefix="")
    app.include_router(chat.router, prefix="/v1")
    return app


app = create_app()
