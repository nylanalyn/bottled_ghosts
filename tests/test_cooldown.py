import pytest
import time

from cellar.safety import Cooldown


@pytest.mark.asyncio
async def test_cooldown_delays_repeated_send() -> None:
    cooldown = Cooldown(0.01)
    await cooldown.wait()
    started = time.monotonic()
    await cooldown.wait()
    assert time.monotonic() - started >= 0.009
