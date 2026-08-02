import httpx
import pytest

from llm_sentinel.providers.base import ChatRequest, Message
from llm_sentinel.providers.openai_client import OpenAIClient
from mock_providers.openai_mock.main import app as openai_mock_app


@pytest.fixture
def client() -> OpenAIClient:
    transport = httpx.ASGITransport(app=openai_mock_app)
    return OpenAIClient(base_url="http://mock-openai", transport=transport)


async def test_chat_non_streaming(client: OpenAIClient) -> None:
    req = ChatRequest(model="gpt-4o-mini", messages=[Message(role="user", content="hello")])
    resp = await client.chat(req, wire_model="gpt-4o-mini")

    assert resp.provider == "openai"
    assert "hello" in resp.content
    assert resp.usage.total_tokens > 0
    assert resp.finish_reason == "stop"


async def test_chat_stream(client: OpenAIClient) -> None:
    req = ChatRequest(
        model="gpt-4o-mini", messages=[Message(role="user", content="hello")], stream=True
    )
    chunks = [c async for c in client.chat_stream(req, wire_model="gpt-4o-mini")]

    assert len(chunks) > 1
    assert chunks[-1].finish_reason == "stop"
    assert "hello" in "".join(c.delta for c in chunks)
