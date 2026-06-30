import logging
from collections.abc import Callable

import aiosqlite

from cellar.module_api import ModuleContract, ModuleRunner
from modules.channel_context import Module as ChannelContextModule

logger = logging.getLogger(__name__)
ModuleFactory = Callable[[], ModuleContract]

REGISTRY: tuple[tuple[str, ModuleFactory], ...] = (
    ("channel_context", ChannelContextModule),
)


def available_modules() -> tuple[str, ...]:
    return tuple(name for name, _factory in REGISTRY)


def module_factory(name: str) -> ModuleFactory | None:
    return next((factory for registered, factory in REGISTRY if registered == name), None)


async def load_modules(db: aiosqlite.Connection, *, bottle_id: int) -> ModuleRunner:
    cursor = await db.execute(
        """SELECT module_name FROM bot_modules
           WHERE bot_id = ? AND enabled = 1 ORDER BY module_name""", (bottle_id,),
    )
    loaded: list[ModuleContract] = []
    for row in await cursor.fetchall():
        name = str(row["module_name"])
        factory = module_factory(name)
        if factory is None:
            logger.error("Bottle %d enables unknown module %s; skipping", bottle_id, name)
            continue
        try:
            loaded.append(factory())
        except Exception:
            logger.exception("failed to initialize module %s for Bottle %d", name, bottle_id)
    return ModuleRunner(loaded)
