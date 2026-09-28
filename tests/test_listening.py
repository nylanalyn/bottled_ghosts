import asyncio

import pytest

from cellar.listening import ListeningWindowManager


@pytest.mark.asyncio
async def test_window_resets_and_fires_once_with_accumulated_items() -> None:
    fired: list[tuple[str, ...]] = []

    async def callback(items: tuple[str, ...]) -> None:
        fired.append(items)

    manager = ListeningWindowManager(0.02, callback)
    try:
        manager.add(("#test", "alice"), "first")
        await asyncio.sleep(0.01)
        manager.add(("#test", "alice"), "second")
        await asyncio.sleep(0.03)
        assert fired == [("first", "second")]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_windows_are_isolated_and_close_cancels_pending_work() -> None:
    fired: list[tuple[str, ...]] = []

    async def callback(items: tuple[str, ...]) -> None:
        fired.append(items)

    manager = ListeningWindowManager(0.01, callback)
    manager.add(("#test", "alice"), "alice")
    manager.add(("#test", "bob"), "bob")
    await asyncio.sleep(0.02)
    assert sorted(fired) == [("alice",), ("bob",)]

    manager.add(("#test", "carol"), "carol")
    await manager.close()
    await asyncio.sleep(0.02)
    assert ("carol",) not in fired


@pytest.mark.asyncio
async def test_constant_chatter_cannot_hold_a_window_open_forever() -> None:
    fired: list[tuple[str, ...]] = []

    async def callback(items: tuple[str, ...]) -> None:
        fired.append(items)

    manager = ListeningWindowManager(0.05, callback, max_delay=0.12)
    try:
        # A new line every 0.02s would reset a plain 0.05s timer forever.
        for index in range(10):
            manager.add(("#test", "room"), str(index))
            await asyncio.sleep(0.02)
        assert fired, "window should fire once max_delay elapses"
        assert fired[0][0] == "0"
    finally:
        await manager.close()
