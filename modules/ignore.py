"""Configurable content filtering for high-volume IRC utility traffic."""

import re

from cellar.irc import irc_casefold, mentions_any_nick
from cellar.module_api import ModuleContext, NightlyContext


def _patterns(ctx: ModuleContext) -> tuple[re.Pattern[str], ...]:
    raw = ctx.module_settings.get("ignore", {})
    values = raw.get("patterns", [])
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("ignore patterns must be a JSON array of strings")
    try:
        return tuple(re.compile(value) for value in values)
    except re.error as error:
        raise ValueError(f"invalid ignore pattern: {error}") from error


def _allows_addressed(ctx: ModuleContext) -> bool:
    raw = ctx.module_settings.get("ignore", {})
    value = raw.get("allow_addressed", True)
    if not isinstance(value, bool):
        raise ValueError("ignore allow_addressed must be a boolean")
    return value


def _is_addressed(ctx: ModuleContext) -> bool:
    nick = ctx.bot_nick or ctx.bottle.irc.nick
    return (
        irc_casefold(ctx.message.target) == irc_casefold(nick)
        or mentions_any_nick(ctx.message.body, (nick, *ctx.bottle.address_names))
    )


class Module:
    async def on_message(self, ctx: ModuleContext) -> None:
        if _allows_addressed(ctx) and _is_addressed(ctx):
            return
        if any(pattern.search(ctx.message.body) for pattern in _patterns(ctx)):
            ctx.drop_message = True

    async def before_prompt(self, _ctx: ModuleContext) -> None:
        return None

    async def after_response(self, _ctx: ModuleContext) -> None:
        return None

    async def nightly(self, _ctx: NightlyContext) -> None:
        return None
