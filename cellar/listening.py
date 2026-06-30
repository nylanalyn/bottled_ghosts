import asyncio
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

Item = TypeVar("Item")
WindowKey = Hashable
WindowCallback = Callable[[tuple[Item, ...]], Awaitable[None]]


@dataclass
class _Window(Generic[Item]):
    items: list[Item] = field(default_factory=list)
    task: asyncio.Task[None] | None = None


class ListeningWindowManager(Generic[Item]):
    def __init__(self, delay: float, callback: WindowCallback[Item]) -> None:
        if delay <= 0:
            raise ValueError("listening window delay must be positive")
        self.delay = delay
        self.callback = callback
        self._windows: dict[WindowKey, _Window[Item]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def contains(self, key: WindowKey) -> bool:
        return key in self._windows

    def add(self, key: WindowKey, item: Item) -> None:
        window = self._windows.get(key)
        if window is None:
            window = _Window()
            self._windows[key] = window
        elif window.task is not None:
            window.task.cancel()
        window.items.append(item)
        task = asyncio.create_task(self._expire(key, window))
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

    async def _expire(self, key: WindowKey, window: _Window[Item]) -> None:
        await asyncio.sleep(self.delay)
        if self._windows.get(key) is not window:
            return
        del self._windows[key]
        await self.callback(tuple(window.items))
