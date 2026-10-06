"""Hug the people a Bottle is fond of, and fend off hugs from those it is not.

RustJeeves' ``!hug <nick>`` starts a hug attempt that the target may
``!reject`` within about 30 seconds. This module reads the affinity module's
per-person warmth:

- When someone the Bottle is genuinely glad to see (warmth at or above
  ``min_warmth``) speaks, a small ``chance`` plans one hug, sent a few minutes
  later on the next channel message. A Bottle hugs at most once per
  ``cooldown_hours`` per channel and the same person at most once per
  ``person_cooldown_days``.
- When Jeeves announces a hug aimed at the Bottle, it answers ``!reject`` only
  if the hugger has been wearing on it (warmth at or below ``reject_below``);
  otherwise it lets the hug land.

There is no LLM call. Every plan lives in ``hug_plans`` and every hug given,
accepted, or rejected is logged in ``hug_events``.
"""

import logging
import random
import re
import time
from dataclasses import dataclass

from cellar.irc import irc_casefold
from cellar.module_api import ModuleCommand, ModuleContext, NightlyContext
from modules.affinity import decay_setting, decayed_warmth

logger = logging.getLogger(__name__)

DEFAULT_MIN_WARMTH = 0.55
DEFAULT_REJECT_BELOW = -0.25
DEFAULT_CHANCE = 0.05
DEFAULT_COOLDOWN_HOURS = 24.0
DEFAULT_PERSON_COOLDOWN_DAYS = 7.0
HUG_DELAY_SECONDS = (2 * 60, 10 * 60)
# A planned hug whose moment passed this long ago is dropped: the person has
# probably moved on, and a hug out of nowhere reads as a bot misfiring.
STALE_PLAN_SECONDS = 45 * 60
INVISIBLE_RE = re.compile(r"[​-‍⁠﻿]")
INCOMING_RE = re.compile(
    r"^(?P<initiator>[^\s,:]+)[\s,:].*\s(?P<target>\S+) may !reject\.?\s*$",
)


@dataclass(frozen=True)
class Settings:
    channels: frozenset[str]
    game_nick: str
    min_warmth: float
    reject_below: float
    chance: float
    cooldown_hours: float
    person_cooldown_days: float


class Module:
    async def on_message(self, ctx: ModuleContext) -> None:
        settings = _settings(ctx)
        if irc_casefold(ctx.message.target) not in settings.channels:
            return
        now = int(time.time())
        if irc_casefold(ctx.message.nick) == irc_casefold(settings.game_nick):
            await self._answer_incoming(ctx, now)
            return
        if not ctx.response_allowed or irc_casefold(ctx.message.nick) in {
            irc_casefold(name) for name in ctx.bottle.address_names
        }:
            return

        plan = await (await ctx.db.execute(
            """SELECT user_id, nick, warmth, due_at FROM hug_plans
               WHERE bot_id = ? AND network = ? AND channel = ?""",
            (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target),
        )).fetchone()
        if plan is not None:
            if now < int(plan["due_at"]) or ctx.commands:
                # Not yet, or another module already used this message's one command.
                return
            await ctx.db.execute(
                "DELETE FROM hug_plans WHERE bot_id = ? AND network = ? AND channel = ?",
                (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target),
            )
            if now - int(plan["due_at"]) <= STALE_PLAN_SECONDS:
                await self._log(ctx, "hug", plan["user_id"], plan["nick"],
                                float(plan["warmth"]), now)
                ctx.commands.append(ModuleCommand(f"!hug {plan['nick']}"))
            await ctx.db.commit()
            return

        if random.random() >= settings.chance:
            return
        if await self._hugged_since(ctx, None, now - int(settings.cooldown_hours * 3600)):
            return
        if await self._hugged_since(
            ctx, ctx.user_id, now - int(settings.person_cooldown_days * 86400),
        ):
            return
        warmth = await decayed_warmth(
            ctx.db, bot_id=ctx.bottle.id, user_id=ctx.user_id,
            decay_per_hour=decay_setting(ctx.module_settings),
        )
        if warmth < settings.min_warmth:
            return
        await ctx.db.execute(
            """INSERT INTO hug_plans(bot_id, network, channel, user_id, nick, warmth, due_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target, ctx.user_id,
             ctx.message.nick, warmth, now + random.randint(*HUG_DELAY_SECONDS)),
        )
        await ctx.db.commit()
        logger.info("planned a hug for %s (warmth %+.2f)", ctx.message.nick, warmth)

    async def _answer_incoming(self, ctx: ModuleContext, now: int) -> None:
        settings = _settings(ctx)
        match = INCOMING_RE.match(INVISIBLE_RE.sub("", ctx.message.body.strip()))
        if match is None:
            return
        names = {irc_casefold(name) for name in ctx.bottle.address_names}
        if irc_casefold(match["target"]) not in names:
            return
        nick = match["initiator"]
        if irc_casefold(nick) in names:
            return
        user_id = await self._user_for_nick(ctx, nick)
        warmth = (
            await decayed_warmth(
                ctx.db, bot_id=ctx.bottle.id, user_id=user_id,
                decay_per_hour=decay_setting(ctx.module_settings),
            )
            if user_id is not None else 0.0
        )
        reject = warmth <= settings.reject_below
        await self._log(ctx, "reject" if reject else "accept", user_id, nick, warmth, now)
        await ctx.db.commit()
        if reject:
            ctx.commands.append(ModuleCommand("!reject"))

    async def _user_for_nick(self, ctx: ModuleContext, nick: str) -> str | None:
        rows = await (await ctx.db.execute(
            """SELECT user_id, nick FROM user_identities
               WHERE network = ? AND lower(nick) = lower(?)
               ORDER BY last_seen DESC""",
            (ctx.bottle.irc.network, nick),
        )).fetchall()
        folded = irc_casefold(nick)
        return next(
            (str(row["user_id"]) for row in rows if irc_casefold(row["nick"]) == folded),
            None,
        )

    async def _hugged_since(
        self, ctx: ModuleContext, user_id: str | None, since: int,
    ) -> bool:
        """Whether this Bottle hugged anyone (or this person) in any channel since then."""
        row = await (await ctx.db.execute(
            """SELECT 1 FROM hug_events
               WHERE bot_id = ? AND network = ? AND kind = 'hug' AND created_at >= ?
                 AND (? IS NULL OR user_id = ?)
               LIMIT 1""",
            (ctx.bottle.id, ctx.bottle.irc.network, since, user_id, user_id),
        )).fetchone()
        return row is not None

    async def _log(
        self, ctx: ModuleContext, kind: str, user_id: str | None, nick: str,
        warmth: float, now: int,
    ) -> None:
        await ctx.db.execute(
            """INSERT INTO hug_events(
                   bot_id, network, channel, kind, user_id, nick, warmth, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target, kind,
             user_id, nick, warmth, now),
        )
        logger.info("hug %s: %s (warmth %+.2f)", kind, nick, warmth)

    async def before_prompt(self, ctx: ModuleContext) -> None:
        return None

    async def after_response(self, ctx: ModuleContext) -> None:
        return None

    async def nightly(self, ctx: NightlyContext) -> None:
        return None


def _number(
    raw: dict[str, object], key: str, default: float, low: float, high: float,
) -> float:
    value = raw.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"hugs {key} must be a number")
    if not low <= float(value) <= high:
        raise ValueError(f"hugs {key} must be between {low} and {high}")
    return float(value)


def _settings(ctx: ModuleContext) -> Settings:
    raw = ctx.module_settings.get("hugs", {})
    channels = raw.get("channels")
    if not isinstance(channels, list) or not channels or not all(
        isinstance(channel, str) and channel.startswith("#") for channel in channels
    ):
        raise ValueError("hugs requires a non-empty channels list")
    game_nick = raw.get("game_nick", "Jeeves")
    if not isinstance(game_nick, str) or not game_nick.strip():
        raise ValueError("hugs game_nick must be a non-empty string")
    return Settings(
        channels=frozenset(irc_casefold(channel) for channel in channels),
        game_nick=game_nick.strip(),
        min_warmth=_number(raw, "min_warmth", DEFAULT_MIN_WARMTH, 0.0, 1.0),
        reject_below=_number(raw, "reject_below", DEFAULT_REJECT_BELOW, -1.0, 0.0),
        chance=_number(raw, "chance", DEFAULT_CHANCE, 0.0, 1.0),
        cooldown_hours=_number(raw, "cooldown_hours", DEFAULT_COOLDOWN_HOURS, 1.0, 24.0 * 30),
        person_cooldown_days=_number(
            raw, "person_cooldown_days", DEFAULT_PERSON_COOLDOWN_DAYS, 0.0, 365.0,
        ),
    )
