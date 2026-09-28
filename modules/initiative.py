"""Quiet-room initiative: occasionally open a conversation instead of waiting.

Every other speaking path is a reaction. This module lets a Bottle break a
lull the way a regular does, with something from its own day, an open thread
(the followups module offers pending ones), or a question for someone.

The runtime asks once a minute per joined channel (``on_idle``). This module
offers an opening only when all of these hold:

* the room has been quiet for a randomized 45 to 180 minutes (configurable),
  re-drawn after every offer;
* a human spoke within the last ``human_recent_hours``, so nobody talks into
  a dead room. Bottles in this database, ``other_bots``, and ambient chat's
  ``utility_bot_nicks`` do not count as human;
* a human has spoken since this Bottle's last offer in the channel, so a
  Bottle never monologues and Bottles never chain openers off each other;
* fewer than ``max_per_day`` offers in the channel in the last 24 hours.

The model may decline with ``[pass]``. Every offer and its outcome is stored
in ``initiative_events``; cadence state lives in ``initiative_state``.
"""

import json
import logging
import random
from dataclasses import dataclass

import aiosqlite

from cellar.irc import irc_casefold
from cellar.module_api import IdleContext, ModuleContext, NightlyContext
from cellar.safety import PASS_SENTINEL, is_pass

logger = logging.getLogger(__name__)
SYSTEM_SPEAKERS = frozenset({"irc runtime", "irc server"})
RECENT_LINES = 200


@dataclass(frozen=True)
class Settings:
    min_quiet_minutes: int
    max_quiet_minutes: int
    max_per_day: int
    human_recent_hours: int
    channels: frozenset[str] | None
    other_bots: frozenset[str]


def _int(raw: dict[str, object], key: str, default: int, low: int, high: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"initiative {key} must be an integer from {low} to {high}")
    return value


def _settings(ctx: IdleContext | ModuleContext) -> Settings:
    raw = ctx.module_settings.get("initiative", {})
    minimum = _int(raw, "min_quiet_minutes", 45, 10, 24 * 60)
    maximum = _int(raw, "max_quiet_minutes", 180, 10, 24 * 60)
    if maximum < minimum:
        raise ValueError("initiative max_quiet_minutes must be at least min_quiet_minutes")
    other_bots: list[object] = []
    for source in (
        raw.get("other_bots", []),
        ctx.module_settings.get("ambient_chat", {}).get("utility_bot_nicks", []),
    ):
        if not isinstance(source, list):
            raise ValueError("initiative other_bots must be a list of nicks")
        other_bots.extend(source)
    if not all(isinstance(nick, str) and nick.strip() for nick in other_bots):
        raise ValueError("initiative other_bots must be a list of nicks")
    channels = raw.get("channels")
    if channels is not None and (
        not isinstance(channels, list)
        or not all(isinstance(item, str) and item.strip() for item in channels)
    ):
        raise ValueError("initiative channels must be a list of channel names")
    return Settings(
        min_quiet_minutes=minimum,
        max_quiet_minutes=maximum,
        max_per_day=_int(raw, "max_per_day", 3, 1, 24),
        human_recent_hours=_int(raw, "human_recent_hours", 12, 1, 168),
        channels=(
            frozenset(irc_casefold(item.strip()) for item in channels)
            if channels is not None else None
        ),
        other_bots=frozenset(irc_casefold(str(nick).strip()) for nick in other_bots),
    )


def quiet_text(minutes: float) -> str:
    if minutes < 90:
        return f"{round(minutes)} minutes"
    return f"{round(minutes / 60)} hours"


def initiative_note(quiet_minutes: float) -> str:
    return (
        f"This channel has been quiet for about {quiet_text(quiet_minutes)}. You may "
        "open a conversation the way a regular breaks a lull: something from your "
        "own day, a thought you have been turning over, a follow-up on an open "
        "thread, or a question for one of the regulars. Keep it short and natural. "
        "Do not remark on the silence, announce that you are here, or greet the "
        "room generically. If nothing genuinely worth saying comes to mind, reply "
        f"with exactly {PASS_SENTINEL} and nothing else, and you will stay quiet."
    )


async def _bottle_nicks(db: aiosqlite.Connection) -> set[str]:
    """Every configured Bottle nick in this database, casefolded.

    Lines from sibling Bottles are not human activity: counting them would
    let two Bottles keep a dead room going by answering each other's openers.
    """
    nicks: set[str] = set()
    for row in await (await db.execute(
        "SELECT nick, alternate_nicks FROM irc_profiles"
    )).fetchall():
        nicks.add(irc_casefold(str(row["nick"])))
        nicks.update(irc_casefold(str(nick)) for nick in json.loads(row["alternate_nicks"]))
    return nicks


async def _state(ctx: IdleContext, settings: Settings) -> tuple[int, str | None]:
    network = ctx.bottle.irc.network
    row = await (await ctx.db.execute(
        """SELECT next_quiet_minutes, last_initiative_at FROM initiative_state
           WHERE bot_id = ? AND network = ? AND channel = ?""",
        (ctx.bottle.id, network, ctx.channel),
    )).fetchone()
    if row is not None:
        return int(row["next_quiet_minutes"]), row["last_initiative_at"]
    threshold = random.randint(settings.min_quiet_minutes, settings.max_quiet_minutes)
    await ctx.db.execute(
        """INSERT INTO initiative_state(bot_id, network, channel, next_quiet_minutes)
           VALUES (?, ?, ?, ?)""",
        (ctx.bottle.id, network, ctx.channel, threshold),
    )
    await ctx.db.commit()
    return threshold, None


class Module:
    async def on_idle(self, ctx: IdleContext) -> None:
        settings = _settings(ctx)
        if settings.channels is not None and irc_casefold(ctx.channel) not in settings.channels:
            return
        threshold, last_initiative_at = await _state(ctx, settings)
        network = ctx.bottle.irc.network
        rows = await (await ctx.db.execute(
            """SELECT speaker, timestamp,
                      (julianday('now') - julianday(timestamp)) * 1440 AS age
               FROM messages WHERE bot_id = ? AND network = ? AND channel = ?
               ORDER BY id DESC LIMIT ?""",
            (ctx.bottle.id, network, ctx.channel, RECENT_LINES),
        )).fetchall()
        lines = [
            row for row in rows if irc_casefold(str(row["speaker"])) not in SYSTEM_SPEAKERS
        ]
        if not lines or float(lines[0]["age"]) < threshold:
            return
        not_human = (
            await _bottle_nicks(ctx.db) | settings.other_bots | {irc_casefold(ctx.bot_nick)}
        )
        human = next(
            (row for row in lines if irc_casefold(str(row["speaker"])) not in not_human),
            None,
        )
        if human is None or float(human["age"]) > settings.human_recent_hours * 60:
            return
        if last_initiative_at is not None and str(human["timestamp"]) <= last_initiative_at:
            return
        recent_offers = await (await ctx.db.execute(
            """SELECT COUNT(*) FROM initiative_events
               WHERE bot_id = ? AND network = ? AND channel = ?
                 AND created_at > datetime('now', '-1 day')""",
            (ctx.bottle.id, network, ctx.channel),
        )).fetchone()
        if recent_offers is not None and int(recent_offers[0]) >= settings.max_per_day:
            return
        quiet = float(lines[0]["age"])
        await ctx.db.execute(
            """INSERT INTO initiative_events(bot_id, network, channel, quiet_minutes)
               VALUES (?, ?, ?, ?)""",
            (ctx.bottle.id, network, ctx.channel, int(quiet)),
        )
        await ctx.db.execute(
            """UPDATE initiative_state
               SET next_quiet_minutes = ?, last_initiative_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
               WHERE bot_id = ? AND network = ? AND channel = ?""",
            (random.randint(settings.min_quiet_minutes, settings.max_quiet_minutes),
             ctx.bottle.id, network, ctx.channel),
        )
        await ctx.db.commit()
        logger.info(
            "Bottle %d offered an opening in %s after %d quiet minutes",
            ctx.bottle.id, ctx.channel, int(quiet),
        )
        ctx.initiative_note = initiative_note(quiet)

    async def on_message(self, _ctx: ModuleContext) -> None:
        return None

    async def before_prompt(self, _ctx: ModuleContext) -> None:
        return None

    async def after_response(self, ctx: ModuleContext) -> None:
        if ctx.response_reason != "initiative":
            return
        passed = ctx.response is None or is_pass(ctx.response)
        if passed:
            ctx.response = None
        await ctx.db.execute(
            """UPDATE initiative_events SET outcome = ?
               WHERE id = (
                   SELECT id FROM initiative_events
                   WHERE bot_id = ? AND network = ? AND channel = ?
                   ORDER BY id DESC LIMIT 1
               ) AND outcome = 'offered'""",
            ("passed" if passed else "spoke", ctx.bottle.id,
             ctx.bottle.irc.network, ctx.conversation or ctx.message.target),
        )
        await ctx.db.commit()

    async def nightly(self, _ctx: NightlyContext) -> None:
        return None
