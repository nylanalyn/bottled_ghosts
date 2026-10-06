"""Play Jeeves' fishing game once a day with a single !recast.

``!recast`` reels in the line that is out and casts straight back, so one
command a day keeps a Bottle fishing. Shortly before each recast is due, a
small background LLM call lets the Bottle choose, in character, whether to
spend XP on a lure or chum and whether to light some dynamite afterwards.
The runtime still decides *when* anything is sent and which choices are
allowed (ADR-004): the model only picks from the offered options, and a
failed or missing choice falls back to a plain ``!recast``.

Everything lives in ``fishing_schedule``: the next due time, the pending
choice, the last command, and Jeeves' last reply about it.
"""

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass

from cellar.irc import irc_casefold, mentions_any_nick
from cellar.llm import complete
from cellar.memory import FENCE_RE
from cellar.module_api import ModuleCommand, ModuleContext, NightlyContext, RuntimeContext
from cellar.prompt import read_soul

logger = logging.getLogger(__name__)

DEFAULT_MIN_RECAST_HOURS = 20.0
DEFAULT_MAX_RECAST_HOURS = 23.5
# Jeeves regrows a hand lost to dynamite in 7 days; a longer cooldown means a
# Bottle never loses its second hand and the week-long ban that comes with it.
DEFAULT_DYNAMITE_COOLDOWN_DAYS = 14
MIN_DYNAMITE_COOLDOWN_DAYS = 8
FIRST_RECAST_MAX_SECONDS = 3 * 60 * 60
# Plan this long before the recast is due, so the choice can see the last outcome.
PLAN_LEAD_SECONDS = 2 * 60 * 60
# How long a due recast waits for an in-flight choice before going plain.
PLAN_GRACE_SECONDS = 15 * 60
DYNAMITE_DELAY_SECONDS = (10 * 60, 90 * 60)
# Jeeves replies within seconds; anything later is about someone else's command.
OUTCOME_WINDOW_SECONDS = 5 * 60
TOO_SOON_RETRY_SECONDS = 90 * 60
BAN_SECONDS = 7 * 24 * 60 * 60
MAX_OUTCOME_CHARS = 600
OUTCOME_PROMPT_SECONDS = 24 * 60 * 60

RECASTS = {
    "plain": "!recast",
    "lure": "!recast lure",
    "chum": "!recast chum",
    "lure chum": "!recast lure chum",
}
BAN_RE = re.compile(r"\bbann?ed\b|\bban\b|no hands")
BAN_DAYS_RE = re.compile(r"(\d+)[ -]day")
FISHING_TALK_RE = re.compile(
    r"\b(fish\w*|reel\w*|recast\w*|lure\w*|chum\w*|dynamite|catch\w*|caught)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Settings:
    channels: frozenset[str]
    game_nick: str
    min_recast_hours: float
    max_recast_hours: float
    ai_choices: bool
    allow_dynamite: bool
    dynamite_cooldown_days: int


@dataclass(frozen=True)
class Choice:
    recast: str
    dynamite: bool


class Module:
    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._planning: dict[str, asyncio.Task[None]] = {}

    async def start(self, ctx: RuntimeContext) -> None:
        # Choices are made off the message path, so they need the runtime's
        # lock to write back. Without a runtime there are no choices.
        self._lock = ctx.database_lock

    async def stop(self, ctx: RuntimeContext) -> None:
        for task in self._planning.values():
            task.cancel()
        await asyncio.gather(*self._planning.values(), return_exceptions=True)
        self._planning.clear()

    async def on_message(self, ctx: ModuleContext) -> None:
        settings = self._settings(ctx)
        if irc_casefold(ctx.message.target) not in settings.channels:
            return
        now = int(time.time())
        if irc_casefold(ctx.message.nick) == irc_casefold(settings.game_nick):
            await self._record_reply(ctx, now)
            return
        if not ctx.response_allowed or irc_casefold(ctx.message.nick) == irc_casefold(
            ctx.bottle.irc.nick
        ):
            return

        key = irc_casefold(ctx.message.target)
        try:
            await ctx.db.execute("BEGIN IMMEDIATE")
            row = await self._state(ctx)
            if row is None:
                await ctx.db.execute(
                    """INSERT INTO fishing_schedule(bot_id, network, channel, next_recast_at)
                       VALUES (?, ?, ?, ?)""",
                    (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target,
                     now + random.randint(60, FIRST_RECAST_MAX_SECONDS)),
                )
                row = await self._state(ctx)
                assert row is not None
            next_recast_at = int(row["next_recast_at"])
            planned = row["planned_recast"]
            planned_dynamite = bool(row["planned_dynamite"])
            dynamite_due_at = row["dynamite_due_at"]
            last_dynamite_at = row["last_dynamite_at"]
            banned_until = row["banned_until"]
            banned = banned_until is not None and now < int(banned_until)

            command: str | None = None
            if banned:
                pass
            elif dynamite_due_at is not None and now >= int(dynamite_due_at):
                command = "!dynamite"
                dynamite_due_at = None
                last_dynamite_at = now
            elif now >= next_recast_at:
                waiting_for_choice = (
                    planned is None and key in self._planning
                    and now - next_recast_at < PLAN_GRACE_SECONDS
                )
                if not waiting_for_choice:
                    command = planned or "!recast"
                    if planned_dynamite and self._dynamite_allowed(
                        settings, last_dynamite_at, now,
                    ):
                        dynamite_due_at = now + random.randint(*DYNAMITE_DELAY_SECONDS)
                    next_recast_at = now + int(random.uniform(
                        settings.min_recast_hours, settings.max_recast_hours,
                    ) * 3600)
                    planned = None
                    planned_dynamite = False

            await ctx.db.execute(
                """UPDATE fishing_schedule SET
                       next_recast_at = ?, planned_recast = ?, planned_dynamite = ?,
                       dynamite_due_at = ?, last_dynamite_at = ?,
                       last_command = COALESCE(?, last_command),
                       last_command_at = COALESCE(?, last_command_at),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE bot_id = ? AND network = ? AND channel = ?""",
                (next_recast_at, planned, int(planned_dynamite), dynamite_due_at,
                 last_dynamite_at, command, now if command else None,
                 ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target),
            )
            await ctx.db.commit()
        except Exception:
            await ctx.db.rollback()
            raise
        if command is not None:
            ctx.commands.append(ModuleCommand(command))
        if (
            settings.ai_choices and self._lock is not None and not banned
            and planned is None and now >= next_recast_at - PLAN_LEAD_SECONDS
            and key not in self._planning
        ):
            task = asyncio.create_task(self._plan(ctx, settings, self._lock))
            self._planning[key] = task
            task.add_done_callback(lambda _task: self._planning.pop(key, None))

    async def _record_reply(self, ctx: ModuleContext, now: int) -> None:
        """Keep Jeeves' answer to this Bottle's own command, and react to bans."""
        if not mentions_any_nick(ctx.message.body, ctx.bottle.address_names):
            return
        row = await self._state(ctx)
        if row is None or row["last_command_at"] is None:
            return
        last_command_at = int(row["last_command_at"])
        if now - last_command_at > OUTCOME_WINDOW_SECONDS:
            return
        body = " ".join(ctx.message.body.split())
        text = irc_casefold(body)
        previous = row["last_outcome"]
        if previous and row["last_outcome_at"] is not None and int(
            row["last_outcome_at"]
        ) >= last_command_at:
            body = f"{previous} / {body}"
        outcome = body[-MAX_OUTCOME_CHARS:]

        next_recast_at = int(row["next_recast_at"])
        dynamite_due_at = row["dynamite_due_at"]
        banned_until = row["banned_until"]
        if BAN_RE.search(text):
            days = BAN_DAYS_RE.search(text)
            banned_until = now + (int(days.group(1)) * 86400 if days else BAN_SECONDS)
            next_recast_at = max(next_recast_at, banned_until)
            dynamite_due_at = None
        elif "at least an hour" in text and str(row["last_command"]).startswith("!recast"):
            # The line was too fresh to reel; come back once it has soaked.
            next_recast_at = now + TOO_SOON_RETRY_SECONDS

        try:
            await ctx.db.execute("BEGIN IMMEDIATE")
            await ctx.db.execute(
                """UPDATE fishing_schedule SET
                       last_outcome = ?, last_outcome_at = ?, next_recast_at = ?,
                       dynamite_due_at = ?, banned_until = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE bot_id = ? AND network = ? AND channel = ?""",
                (outcome, now, next_recast_at, dynamite_due_at, banned_until,
                 ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target),
            )
            await ctx.db.commit()
        except Exception:
            await ctx.db.rollback()
            raise

    async def _plan(
        self, ctx: ModuleContext, settings: Settings, lock: asyncio.Lock,
    ) -> None:
        """Let the Bottle pick its next recast, then store the pick."""
        channel = ctx.message.target
        try:
            async with lock:
                row = await self._state(ctx)
            if row is None:
                return
            now = int(time.time())
            dynamite_ok = self._dynamite_allowed(settings, row["last_dynamite_at"], now)
            try:
                choice = await choose(
                    ctx.bottle.llm_for("initiative"),
                    soul=read_soul(ctx.bottle.soul_prompt_path),
                    last_command=row["last_command"],
                    last_outcome=row["last_outcome"],
                    dynamite_ok=dynamite_ok,
                )
            except Exception:
                logger.exception("fishing choice failed; using a plain !recast")
                choice = Choice(recast="!recast", dynamite=False)
            logger.info(
                "fishing choice for %s: %s%s", channel, choice.recast,
                " then !dynamite" if choice.dynamite else "",
            )
            async with lock:
                await ctx.db.execute(
                    """UPDATE fishing_schedule SET
                           planned_recast = ?, planned_dynamite = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE bot_id = ? AND network = ? AND channel = ?
                         AND planned_recast IS NULL""",
                    (choice.recast, int(choice.dynamite and dynamite_ok),
                     ctx.bottle.id, ctx.bottle.irc.network, channel),
                )
                await ctx.db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("fishing planning failed in %s", channel)

    async def before_prompt(self, ctx: ModuleContext) -> None:
        """Let the Bottle talk about its own fishing when fishing comes up."""
        settings = self._settings(ctx)
        if irc_casefold(ctx.message.target) not in settings.channels:
            return
        if not FISHING_TALK_RE.search(ctx.message.body):
            return
        row = await self._state(ctx)
        if row is None or not row["last_outcome"] or row["last_outcome_at"] is None:
            return
        if int(time.time()) - int(row["last_outcome_at"]) > OUTCOME_PROMPT_SECONDS:
            return
        ctx.prompt_sections.append(
            "You play Jeeves' fishing game once a day. Your latest go was "
            f"{row['last_command']}; {settings.game_nick} answered: {row['last_outcome']}"
        )

    async def after_response(self, ctx: ModuleContext) -> None:
        return None

    async def nightly(self, ctx: NightlyContext) -> None:
        return None

    @staticmethod
    def _dynamite_allowed(settings: Settings, last_dynamite_at: int | None, now: int) -> bool:
        if not settings.allow_dynamite:
            return False
        if last_dynamite_at is None:
            return True
        return now - int(last_dynamite_at) >= settings.dynamite_cooldown_days * 86400

    async def _state(self, ctx: ModuleContext):
        return await (await ctx.db.execute(
            """SELECT next_recast_at, planned_recast, planned_dynamite, dynamite_due_at,
                      last_command, last_command_at, last_dynamite_at, last_outcome,
                      last_outcome_at, banned_until
               FROM fishing_schedule
               WHERE bot_id = ? AND network = ? AND channel = ?""",
            (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target),
        )).fetchone()

    def _settings(self, ctx: ModuleContext) -> Settings:
        raw = ctx.module_settings.get("fishing", {})
        channels = raw.get("channels")
        if not isinstance(channels, list) or not channels or not all(
            isinstance(channel, str) and channel.startswith("#") for channel in channels
        ):
            raise ValueError("fishing requires a non-empty channels list")
        game_nick = raw.get("game_nick", "Jeeves")
        min_hours = raw.get("min_recast_hours", DEFAULT_MIN_RECAST_HOURS)
        max_hours = raw.get("max_recast_hours", DEFAULT_MAX_RECAST_HOURS)
        ai_choices = raw.get("ai_choices", True)
        allow_dynamite = raw.get("allow_dynamite", True)
        cooldown = raw.get("dynamite_cooldown_days", DEFAULT_DYNAMITE_COOLDOWN_DAYS)
        if not isinstance(game_nick, str) or not game_nick.strip():
            raise ValueError("fishing game_nick must be a non-empty string")
        if (
            not isinstance(min_hours, (int, float)) or isinstance(min_hours, bool)
            or not isinstance(max_hours, (int, float)) or isinstance(max_hours, bool)
            or float(min_hours) < 1.0 or float(max_hours) < float(min_hours)
            or float(max_hours) > 24.0
        ):
            # Jeeves starts losing catches once a line has been out past 24 hours.
            raise ValueError("fishing requires 1 <= min_recast_hours <= max_recast_hours <= 24")
        if not isinstance(ai_choices, bool) or not isinstance(allow_dynamite, bool):
            raise ValueError("fishing ai_choices and allow_dynamite must be booleans")
        if (
            not isinstance(cooldown, int) or isinstance(cooldown, bool)
            or cooldown < MIN_DYNAMITE_COOLDOWN_DAYS
        ):
            raise ValueError(
                f"fishing dynamite_cooldown_days must be an integer >= "
                f"{MIN_DYNAMITE_COOLDOWN_DAYS}"
            )
        return Settings(
            channels=frozenset(irc_casefold(channel) for channel in channels),
            game_nick=game_nick.strip(), min_recast_hours=float(min_hours),
            max_recast_hours=float(max_hours), ai_choices=ai_choices,
            allow_dynamite=allow_dynamite, dynamite_cooldown_days=cooldown,
        )


def choice_messages(
    *, soul: str, last_command: str | None, last_outcome: str | None, dynamite_ok: bool,
) -> list[dict[str, str]]:
    last = (
        f"Last time you sent {last_command} and Jeeves answered: {last_outcome}"
        if last_command and last_outcome else "You have no recent result to go on."
    )
    dynamite = (
        "\n\nAfterwards you may also light a stick of dynamite. It is a terrible "
        "idea: about 1 time in 5 it blows up a haul of rare fish and a lot of XP, "
        "1 in 10 you chicken out, and otherwise it costs you a hand for a week."
        if dynamite_ok else ""
    )
    return [
        {"role": "system", "content": soul},
        {
            "role": "user",
            "content": (
                "Out of character for a moment: it is time for your daily go at "
                "Jeeves' IRC fishing game. You reel in yesterday's line and cast "
                "straight back out. You may sweeten it first:\n"
                "- plain: just recast.\n"
                "- lure: spend 30 XP on a mystery lure for a rarer or bigger fish.\n"
                "- chum: spend 250 XP chumming the water, so fish run large for "
                "everyone in the channel for 20 minutes.\n"
                "- lure chum: both."
                f"{dynamite}\n\n{last}\n\n"
                "Choose the way your character would today. Return only JSON: "
                '{"recast": "plain" | "lure" | "chum" | "lure chum", '
                '"dynamite": true | false}'
            ),
        },
    ]


def parse_choice(raw: str, *, dynamite_ok: bool) -> Choice:
    data = json.loads(FENCE_RE.sub("", raw.strip()))
    if not isinstance(data, dict):
        raise ValueError("fishing choice must be a JSON object")
    recast = RECASTS.get(" ".join(str(data.get("recast", "plain")).lower().split()))
    if recast is None:
        raise ValueError("fishing choice named an unknown recast")
    return Choice(recast=recast, dynamite=dynamite_ok and data.get("dynamite") is True)


async def choose(
    profile, *, soul: str, last_command: str | None, last_outcome: str | None,
    dynamite_ok: bool,
) -> Choice:
    messages = choice_messages(
        soul=soul, last_command=last_command, last_outcome=last_outcome,
        dynamite_ok=dynamite_ok,
    )
    raw = await complete(profile.model_copy(update={"max_tokens": 256}), messages)
    return parse_choice(raw, dynamite_ok=dynamite_ok)
