import asyncio
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

Item = TypeVar("Item")
WindowKey = Hashable
WindowCallback = Callable[[tuple[Item, ...]], Awaitable[None]]


# A busy room can keep adding lines to an open window forever. Each line
# restarts the quiet timer, but no window stays open longer than this multiple
# of the delay, so an addressed reply cannot be postponed indefinitely.
MAX_WINDOW_DELAY_FACTOR = 3.0


@dataclass
class _Window(Generic[Item]):
    opened_at: float
    items: list[Item] = field(default_factory=list)
    task: asyncio.Task[None] | None = None


class ListeningWindowManager(Generic[Item]):
    def __init__(
        self, delay: float, callback: WindowCallback[Item], *,
        max_delay: float | None = None,
    ) -> None:
        if delay <= 0:
            raise ValueError("listening window delay must be positive")
        self.delay = delay
        self.max_delay = delay * MAX_WINDOW_DELAY_FACTOR if max_delay is None else max_delay
        if self.max_delay < delay:
            raise ValueError("listening window max_delay cannot be shorter than delay")
        self.callback = callback
        self._windows: dict[WindowKey, _Window[Item]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def contains(self, key: WindowKey) -> bool:
        return key in self._windows

    def add(self, key: WindowKey, item: Item) -> None:
        now = asyncio.get_running_loop().time()
        window = self._windows.get(key)
        if window is None:
            window = _Window(opened_at=now)
            self._windows[key] = window
        elif window.task is not None:
            window.task.cancel()
        window.items.append(item)
        wait = max(0.0, min(self.delay, window.opened_at + self.max_delay - now))
        task = asyncio.create_task(self._expire(key, window, wait))
        window.task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        self._windows.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _expire(self, key: WindowKey, window: _Window[Item], wait: float) -> None:
        await asyncio.sleep(wait)
        if self._windows.get(key) is not window:
            return
        del self._windows[key]
        await self.callback(tuple(window.items))
