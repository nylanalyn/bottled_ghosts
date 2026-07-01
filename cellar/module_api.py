import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal, Protocol

import aiosqlite

from cellar.models import Bottle, IncomingIRCMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModuleCommand:
    """A runtime-sanitized IRC command requested by a module."""

    body: str


@dataclass
class ModuleContext:
    db: aiosqlite.Connection
    bottle: Bottle
    message: IncomingIRCMessage
    user_id: str
    source_message_id: int
    response_allowed: bool = True
    request_response: bool = False
    monitor_when_silent: bool = False
    response_reason: Literal["addressed", "ambient"] = "addressed"
    module_settings: dict[str, dict[str, object]] = field(default_factory=dict)
    prompt_sections: list[str] = field(default_factory=list)
    commands: list[ModuleCommand] = field(default_factory=list)
    response: str | None = None


@dataclass
class NightlyContext:
    db: aiosqlite.Connection
    bottle: Bottle
    period_start: str
    period_end: str
    summary: str
    module_settings: dict[str, dict[str, object]] = field(default_factory=dict)


@dataclass
class RuntimeState:
    irc_connected: bool = False
    database_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class RuntimeContext:
    db: aiosqlite.Connection
    bottle: Bottle
    database_lock: asyncio.Lock
    state: RuntimeState
    module_settings: dict[str, dict[str, object]] = field(default_factory=dict)


class ModuleContract(Protocol):
    async def on_message(self, ctx: ModuleContext) -> None: ...
    async def before_prompt(self, ctx: ModuleContext) -> None: ...
    async def after_response(self, ctx: ModuleContext) -> None: ...
    async def nightly(self, ctx: NightlyContext) -> None: ...


class ModuleRunner:
    def __init__(
        self, modules: list[ModuleContract],
        settings: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.modules = modules
        self.settings = settings or {}

    async def on_message(self, ctx: ModuleContext) -> None:
        await self._run("on_message", ctx)

    async def before_prompt(self, ctx: ModuleContext) -> None:
        await self._run("before_prompt", ctx)

    async def after_response(self, ctx: ModuleContext) -> None:
        await self._run("after_response", ctx)

    async def nightly(self, ctx: NightlyContext) -> None:
        await self._run("nightly", ctx)

    async def start(self, ctx: RuntimeContext) -> None:
        await self._run("start", ctx)

    async def stop(self, ctx: RuntimeContext) -> None:
        await self._run("stop", ctx, reverse=True)

    async def _run(
        self, hook: str, ctx: ModuleContext | NightlyContext | RuntimeContext,
        *, reverse: bool = False,
    ) -> None:
        ctx.module_settings = self.settings
        modules = reversed(self.modules) if reverse else self.modules
        for module in modules:
            try:
                callback = getattr(module, hook, None)
                if callback is None:
                    continue
                await callback(ctx)
            except Exception:
                logger.exception("module %s failed during %s", type(module).__name__, hook)
