import asyncio

import pytest

from cellar.models import Bottle, IRCProfile, LLMProfile
from cellar.runtime import run_bottle


@pytest.mark.asyncio
async def test_bottle_reconnects_with_backoff(monkeypatch, tmp_path) -> None:
    bottle = Bottle(
        id=1,
        name="test",
        soul_prompt_path=tmp_path / "soul.md",
        irc=IRCProfile(network="test", host="localhost", nick="ghost",
                       username="ghost", realname="Ghost", channels=["#test"]),
        llm=LLMProfile(endpoint="http://localhost/chat", model="test"),
    )
    attempts = 0
    delays: list[float] = []

    async def fake_run_once(_db, _bottle) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            raise asyncio.CancelledError
        raise ConnectionError("offline")

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("cellar.runtime.run_bottle_once", fake_run_once)
    monkeypatch.setattr("cellar.runtime.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_bottle(object(), bottle)  # type: ignore[arg-type]
    assert delays == [1.0, 2.0]
