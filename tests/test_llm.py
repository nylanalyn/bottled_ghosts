import json

import httpx
import pytest

import cellar.llm as llm_module
from cellar.llm import complete
from cellar.models import LLMProfile


def _profile(**overrides) -> LLMProfile:
    fields = {"endpoint": "http://localhost/chat", "model": "test-model",
              "api_key": "secret"}
    fields.update(overrides)
    return LLMProfile(**fields)


def _patch_client(monkeypatch, handler):
    """Replace httpx.AsyncClient so the request can be inspected/returned by handler."""
    real_async_client = llm_module.httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(llm_module.httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_complete_omits_penalties_when_zero(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    _patch_client(monkeypatch, handler)
    await complete(_profile(), [{"role": "user", "content": "hi"}])
    assert "frequency_penalty" not in captured["payload"]
    assert "presence_penalty" not in captured["payload"]
    assert captured["payload"]["temperature"] == 0.7


@pytest.mark.asyncio
async def test_complete_sends_penalties_when_nonzero(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    _patch_client(monkeypatch, handler)
    await complete(
        _profile(frequency_penalty=0.5, presence_penalty=0.3),
        [{"role": "user", "content": "hi"}],
    )
    assert captured["payload"]["frequency_penalty"] == 0.5
    assert captured["payload"]["presence_penalty"] == 0.3


@pytest.mark.asyncio
async def test_complete_retries_on_429_then_succeeds(monkeypatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    # Avoid real sleeping during backoff.
    async def _no_sleep(*_a, **_k) -> None:
        return None

    monkeypatch.setattr("cellar.llm.asyncio.sleep", _no_sleep)
    _patch_client(monkeypatch, handler)
    result = await complete(_profile(), [{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_complete_raises_on_null_content(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    _patch_client(monkeypatch, handler)
    with pytest.raises(ValueError):
        await complete(_profile(), [{"role": "user", "content": "hi"}])
