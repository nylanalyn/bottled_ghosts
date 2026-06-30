import asyncio
import time

import pytest

from cellar.models import Bottle, IRCProfile, LLMProfile
from cellar.runtime import run_bottle, run_bottles


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


@pytest.mark.asyncio
async def test_bottle_resets_backoff_after_stable_session(monkeypatch, tmp_path) -> None:
    bottle = Bottle(
        id=1, name="test", soul_prompt_path=tmp_path / "soul.md",
        irc=IRCProfile(network="test", host="localhost", nick="ghost",
                       username="ghost", realname="Ghost", channels=["#test"]),
        llm=LLMProfile(endpoint="http://localhost/chat", model="test"),
    )
    attempts = 0
    delays: list[float] = []

    async def fake_run_once(_db, _bottle) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 4:
            raise asyncio.CancelledError
        raise ConnectionError("offline")

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("cellar.runtime.run_bottle_once", fake_run_once)
    monkeypatch.setattr("cellar.runtime.asyncio.sleep", fake_sleep)
    real_monotonic = time.monotonic

    def fake_monotonic() -> float:
        if attempts == 3:
            return 33.0
        if attempts <= 3:
            return float(attempts)
        return real_monotonic()

    monkeypatch.setattr("cellar.runtime.time.monotonic", fake_monotonic)

    with pytest.raises(asyncio.CancelledError):
        await run_bottle(object(), bottle)  # type: ignore[arg-type]
    assert delays == [1.0, 2.0, 1.0]


@pytest.mark.asyncio
async def test_run_bottles_opens_and_closes_one_connection_each(monkeypatch, tmp_path) -> None:
    bottles = [
        Bottle(
            id=bottle_id, name=f"test-{bottle_id}", soul_prompt_path=tmp_path / "soul.md",
            irc=IRCProfile(network="test", host="localhost", nick=f"ghost{bottle_id}",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost/chat", model="test"),
        )
        for bottle_id in (1, 2)
    ]
    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    connections: list[FakeConnection] = []
    seen: list[tuple[FakeConnection, int]] = []

    async def fake_open_database(_path):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    async def fake_run_bottle(connection, bottle) -> None:
        seen.append((connection, bottle.id))

    monkeypatch.setattr("cellar.runtime.open_database", fake_open_database)
    monkeypatch.setattr("cellar.runtime.run_bottle", fake_run_bottle)

    await run_bottles(tmp_path / "spirits.db", bottles)

    assert len(connections) == 2
    assert connections[0] is not connections[1]
    assert sorted(bottle_id for _connection, bottle_id in seen) == [1, 2]
    assert all(connection.closed for connection in connections)
