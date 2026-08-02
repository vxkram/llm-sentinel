from fastapi.testclient import TestClient

from llm_sentinel.api.v1.chat import _inject_system_prompt
from llm_sentinel.main import app
from llm_sentinel.providers.base import ChatRequest, Message


def test_inject_system_prompt_with_no_existing_system_message() -> None:
    req = ChatRequest(model="x", messages=[Message(role="user", content="hi")])

    updated = _inject_system_prompt(req, "be terse")

    assert updated.messages[0] == Message(role="system", content="be terse")
    assert updated.messages[1] == Message(role="user", content="hi")


def test_inject_system_prompt_merges_with_existing_system_message() -> None:
    req = ChatRequest(
        model="x",
        messages=[
            Message(role="system", content="user-provided"),
            Message(role="user", content="hi"),
        ],
    )

    updated = _inject_system_prompt(req, "team-level")

    assert updated.messages[0].content == "team-level\nuser-provided"
    assert len(updated.messages) == 2


def test_missing_auth_header_returns_401() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "llama3.2", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 401


def test_invalid_api_key_returns_401() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer not-a-real-key"},
            json={"model": "llama3.2", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 401


def test_disallowed_model_returns_403() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-beta-demo-222333"},
            json={"model": "llama3.2", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 403
