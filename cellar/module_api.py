import logging
from dataclasses import dataclass, field
from typing import Protocol

import aiosqlite

from cellar.models import Bottle, IncomingIRCMessage

logger = logging.getLogger(__name__)


@dataclass
class ModuleContext:
    db: aiosqlite.Connection
    bottle: Bottle
    message: IncomingIRCMessage
    user_id: str
    source_message_id: int
    prompt_sections: list[str] = field(default_factory=list)
    response: str | None = None


@dataclass
class NightlyContext:
    db: aiosqlite.Connection
    bottle: Bottle
    period_start: str
    period_end: str
    summary: str


class ModuleContract(Protocol):
    async def on_message(self, ctx: ModuleContext) -> None: ...
    async def before_prompt(self, ctx: ModuleContext) -> None: ...
    async def after_response(self, ctx: ModuleContext) -> None: ...
    async def nightly(self, ctx: NightlyContext) -> None: ...


class ModuleRunner:
    def __init__(self, modules: list[ModuleContract]) -> None:
        self.modules = modules

    async def on_message(self, ctx: ModuleContext) -> None:
        await self._run("on_message", ctx)

    async def before_prompt(self, ctx: ModuleContext) -> None:
        await self._run("before_prompt", ctx)

    async def after_response(self, ctx: ModuleContext) -> None:
        await self._run("after_response", ctx)

    async def nightly(self, ctx: NightlyContext) -> None:
        await self._run("nightly", ctx)

    async def _run(self, hook: str, ctx: ModuleContext | NightlyContext) -> None:
        for module in self.modules:
            try:
                callback = getattr(module, hook)
                await callback(ctx)
            except Exception:
                logger.exception("module %s failed during %s", type(module).__name__, hook)
