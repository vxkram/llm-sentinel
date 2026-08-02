from fastapi import APIRouter, FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from mock_providers.fault_injection import FaultConfig, FaultState


class ConnectionResetMiddleware:
    """Simulates a mid-response connection reset: sends valid headers, then a
    truncated body with more_body=False, so the client sees an incomplete/
    invalid message instead of a clean response or a clean error.
    """

    def __init__(self, app: ASGIApp, fault_state: FaultState) -> None:
        self.app = app
        self.fault_state = fault_state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        is_admin_path = scope.get("type") == "http" and scope["path"].startswith("/_admin")
        if scope["type"] != "http" or is_admin_path or not self.fault_state.should_reset():
            await self.app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"id": "trunc', "more_body": False})


def build_mock_app(provider_name: str, router: APIRouter) -> FastAPI:
    app = FastAPI(title=f"llm-sentinel-mock-{provider_name}")
    app.state.fault = FaultState()
    app.add_middleware(ConnectionResetMiddleware, fault_state=app.state.fault)
    app.include_router(router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "provider": provider_name}

    @app.get("/_admin/fault")
    async def get_fault() -> FaultConfig:
        return app.state.fault.config

    @app.post("/_admin/fault")
    async def set_fault(config: FaultConfig) -> FaultConfig:
        app.state.fault.config = config
        return app.state.fault.config

    return app
