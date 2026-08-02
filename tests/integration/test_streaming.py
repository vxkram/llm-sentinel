import json

import httpx
import redis as sync_redis
from fastapi.testclient import TestClient

from llm_sentinel.main import app
from llm_sentinel.providers.openai_client import OpenAIClient
from mock_providers.openai_mock.main import app as openai_mock_app
from tests.redis_helpers import requires_redis

pytestmark = requires_redis


def _patch_openai_mock(client: TestClient) -> None:
    client.app.state.registry._clients["openai"] = OpenAIClient(
        base_url="http://mock-openai", transport=httpx.ASGITransport(app=openai_mock_app)
    )


def _flush_team(team_id: str) -> None:
    r = sync_redis.Redis(host="localhost", port=6379, db=0)
    keys = r.keys(f"ratelimit:{team_id}:*") + r.keys(f"budget:{team_id}:*")
    if keys:
        r.delete(*keys)
    r.close()


def _parse_sse(raw_text: str) -> list[dict]:
    events = []
    for line in raw_text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        events.append({"done": True} if payload == "[DONE]" else json.loads(payload))
    return events


def test_streaming_response_is_ordered_and_terminated() -> None:
    with TestClient(app) as client:
        _patch_openai_mock(client)
        _flush_team("team-alpha")

        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-alpha-demo-000111"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello there"}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            raw_text = "".join(resp.iter_text())

    events = _parse_sse(raw_text)

    assert events[-1] == {"done": True}
    chunk_events = events[:-1]
    assert len(chunk_events) > 1

    full_text = "".join(e["delta"] for e in chunk_events)
    assert "hello there" in full_text

    # Exactly one chunk carries a finish_reason, and it's the last one -
    # ordering matters here, not just presence.
    finish_reasons = [e["finish_reason"] for e in chunk_events if e.get("finish_reason")]
    assert finish_reasons == ["stop"]
    assert chunk_events[-1]["finish_reason"] == "stop"


def test_streaming_reconciles_budget_after_completion() -> None:
    r = sync_redis.Redis(host="localhost", port=6379, db=0)

    with TestClient(app) as client:
        _patch_openai_mock(client)
        _flush_team("team-alpha")

        pre_keys = r.keys("budget:team-alpha:daily:*")
        pre_spend = float(r.get(pre_keys[0])) if pre_keys else 0.0

        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-alpha-demo-000111"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": True},
        ) as resp:
            list(resp.iter_text())  # drain the stream fully so reconcile/charge runs

        post_keys = r.keys("budget:team-alpha:daily:*")
        post_spend = float(r.get(post_keys[0])) if post_keys else 0.0

    r.close()
    assert post_spend > pre_spend
