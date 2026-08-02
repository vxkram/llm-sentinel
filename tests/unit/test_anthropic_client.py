import httpx
import pytest

from llm_sentinel.providers.anthropic_client import AnthropicClient
from llm_sentinel.providers.base import ChatRequest, Message
from mock_providers.anthropic_mock.main import app as anthropic_mock_app


@pytest.fixture
def client() -> AnthropicClient:
    transport = httpx.ASGITransport(app=anthropic_mock_app)
    return AnthropicClient(base_url="http://mock-anthropic", transport=transport)


async def test_chat_non_streaming(client: AnthropicClient) -> None:
    req = ChatRequest(
        model="claude-3-5-sonnet",
        messages=[
            Message(role="system", content="be terse"),
            Message(role="user", content="hello"),
        ],
    )
    resp = await client.chat(req, wire_model="claude-3-5-sonnet-20241022")

    assert resp.provider == "anthropic"
    assert "hello" in resp.content
    assert resp.usage.total_tokens > 0
    assert resp.finish_reason == "stop"


async def test_chat_stream(client: AnthropicClient) -> None:
    req = ChatRequest(
        model="claude-3-5-sonnet", messages=[Message(role="user", content="hello")], stream=True
    )
    chunks = [c async for c in client.chat_stream(req, wire_model="claude-3-5-sonnet-20241022")]

    assert len(chunks) > 1
    assert chunks[-1].finish_reason == "stop"
    assert "hello" in "".join(c.delta for c in chunks)


def test_system_prompt_extracted_to_top_level(client: AnthropicClient) -> None:
    req = ChatRequest(
        model="claude-3-5-sonnet",
        messages=[
            Message(role="system", content="be terse"),
            Message(role="user", content="hi"),
        ],
    )
    payload = client._build_payload(req, wire_model="claude-3-5-sonnet-20241022", stream=False)

    assert payload["system"] == "be terse"
    assert all(m["role"] != "system" for m in payload["messages"])
