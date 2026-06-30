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
