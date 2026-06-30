import pytest
import asyncio
import time

from cellar.safety import Cooldown


@pytest.mark.asyncio
async def test_cooldown_delays_repeated_send() -> None:
    cooldown = Cooldown(0.01)
    await cooldown.wait()
    started = time.monotonic()
    await cooldown.wait()
    assert time.monotonic() - started >= 0.009


@pytest.mark.asyncio
async def test_cooldown_serializes_concurrent_senders() -> None:
    cooldown = Cooldown(0.01)
    completed: list[float] = []

    async def wait() -> None:
        await cooldown.wait()
        completed.append(time.monotonic())

    await asyncio.gather(wait(), wait(), wait())
    assert completed[1] - completed[0] >= 0.009
    assert completed[2] - completed[1] >= 0.009
