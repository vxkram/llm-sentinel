import httpx
import redis as sync_redis
from fastapi.testclient import TestClient

from llm_sentinel.main import app
from llm_sentinel.providers.anthropic_client import AnthropicClient
from llm_sentinel.providers.openai_client import OpenAIClient
from mock_providers.anthropic_mock.main import app as anthropic_mock_app
from mock_providers.openai_mock.main import app as openai_mock_app
from tests.redis_helpers import requires_redis

pytestmark = requires_redis


def _patch_mocks(client: TestClient) -> None:
    client.app.state.registry._clients["openai"] = OpenAIClient(
        base_url="http://mock-openai", transport=httpx.ASGITransport(app=openai_mock_app)
    )
    client.app.state.registry._clients["anthropic"] = AnthropicClient(
        base_url="http://mock-anthropic", transport=httpx.ASGITransport(app=anthropic_mock_app)
    )


def _flush_team(team_id: str) -> None:
    # A plain synchronous client, deliberately independent of app.state.redis
    # (which is bound to the TestClient's own event loop) - crossing event
    # loops with the async client raises "Event loop is closed".
    r = sync_redis.Redis(host="localhost", port=6379, db=0)
    keys = r.keys(f"ratelimit:{team_id}:*") + r.keys(f"budget:{team_id}:*")
    if keys:
        r.delete(*keys)
    r.close()


def test_allowed_model_injects_team_system_prompt() -> None:
    with TestClient(app) as client:
        _patch_mocks(client)
        _flush_team("team-alpha")

        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-alpha-demo-000111"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 200
    assert "Team Alpha" in resp.json()["content"]


def test_rpm_burst_returns_429_with_retry_after() -> None:
    # team-alpha's configured rpm is 3.
    with TestClient(app) as client:
        _patch_mocks(client)
        _flush_team("team-alpha")

        headers = {"Authorization": "Bearer sk-alpha-demo-000111"}
        payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}

        responses = [client.post("/v1/chat/completions", headers=headers, json=payload) for _ in range(4)]

    statuses = [r.status_code for r in responses]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429
    assert "retry-after" in responses[3].headers
    assert float(responses[3].headers["retry-after"]) >= 1


def test_budget_exhaustion_returns_402_on_second_call() -> None:
    # team-beta's configured daily budget ($0.00005) is smaller than the cost
    # of a single real call, so the first call succeeds (and gets charged)
    # but the next one is blocked before it reaches the provider.
    with TestClient(app) as client:
        _patch_mocks(client)
        _flush_team("team-beta")

        headers = {"Authorization": "Bearer sk-beta-demo-222333"}
        payload = {"model": "claude-3-5-sonnet", "messages": [{"role": "user", "content": "hi"}]}

        first = client.post("/v1/chat/completions", headers=headers, json=payload)
        second = client.post("/v1/chat/completions", headers=headers, json=payload)

    assert first.status_code == 200
    assert second.status_code == 402
