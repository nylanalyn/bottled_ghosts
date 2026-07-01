import random
import re
import time
from dataclasses import dataclass

from cellar.irc import irc_casefold, mentions_any_nick
from cellar.module_api import ModuleCommand, ModuleContext, NightlyContext

DEFAULT_MIN_CAST_LINES = 20
DEFAULT_MAX_CAST_LINES = 60
DEFAULT_MIN_REEL_HOURS = 2.0
DEFAULT_MAX_REEL_HOURS = 26.0
DEFAULT_DYNAMITE_CHANCE = 0.02
ACK_TIMEOUT_SECONDS = 20 * 60
BAN_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class Settings:
    channels: frozenset[str]
    game_nick: str
    min_cast_lines: int
    max_cast_lines: int
    min_reel_hours: float
    max_reel_hours: float
    dynamite_chance: float


class Module:
    async def on_message(self, ctx: ModuleContext) -> None:
        settings = self._settings(ctx)
        channel = irc_casefold(ctx.message.target)
        if channel not in settings.channels:
            return
        if irc_casefold(ctx.message.target) == irc_casefold(ctx.bottle.irc.nick):
            return

        now = int(time.time())
        if irc_casefold(ctx.message.nick) == irc_casefold(settings.game_nick):
            await self._handle_game_reply(ctx, settings, now)
            return
        if not ctx.response_allowed or irc_casefold(ctx.message.nick) == irc_casefold(
            ctx.bottle.irc.nick
        ):
            return

        try:
            await ctx.db.execute("BEGIN IMMEDIATE")
            row = await self._state(ctx)
            if row is None:
                trigger = random.randint(settings.min_cast_lines, settings.max_cast_lines)
                lines = 1
                await ctx.db.execute(
                    """INSERT INTO fishing_state(
                           bot_id, network, channel, phase, eligible_lines_seen,
                           next_cast_line
                       ) VALUES (?, ?, ?, 'idle', ?, ?)""",
                    (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target,
                     lines, trigger),
                )
                phase = "idle"
                command_sent_at = None
                reel_after = None
                banned_until = None
            else:
                phase = str(row["phase"])
                lines = int(row["eligible_lines_seen"])
                trigger = int(row["next_cast_line"])
                command_sent_at = row["command_sent_at"]
                reel_after = row["reel_after"]
                banned_until = row["banned_until"]

            command: str | None = None
            if phase == "banned" and banned_until is not None and now >= int(banned_until):
                phase = "idle"
                lines = 0
                banned_until = None
                trigger = random.randint(settings.min_cast_lines, settings.max_cast_lines)
            if phase.startswith("awaiting_") and command_sent_at is not None:
                if now - int(command_sent_at) >= ACK_TIMEOUT_SECONDS:
                    # Reissuing is safe: Jeeves reports an existing cast or a missing one.
                    command = {
                        "awaiting_cast": "!cast",
                        "awaiting_reel": "!reel",
                        "awaiting_dynamite": "!dynamite",
                    }[phase]
                    command_sent_at = now
            elif phase == "fishing" and reel_after is not None and now >= int(reel_after):
                phase = "awaiting_reel"
                command_sent_at = now
                command = "!reel"
            elif phase == "idle":
                lines += 1 if row is not None else 0
                if lines >= trigger:
                    phase = "awaiting_cast"
                    lines = 0
                    command_sent_at = now
                    command = "!cast"

            await ctx.db.execute(
                """UPDATE fishing_state SET
                       phase = ?, eligible_lines_seen = ?, next_cast_line = ?,
                       command_sent_at = ?, banned_until = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE bot_id = ? AND network = ? AND channel = ?""",
                (phase, lines, trigger, command_sent_at, banned_until,
                 ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target),
            )
            await ctx.db.commit()
        except Exception:
            await ctx.db.rollback()
            raise
        if command is not None:
            ctx.commands.append(ModuleCommand(command))

    async def _handle_game_reply(
        self, ctx: ModuleContext, settings: Settings, now: int,
    ) -> None:
        if not mentions_any_nick(ctx.message.body, ctx.bottle.address_names):
            return
        row = await self._state(ctx)
        if row is None or not str(row["phase"]).startswith("awaiting_"):
            return
        phase = str(row["phase"])
        text = irc_casefold(ctx.message.body)
        next_phase = phase
        cast_at = row["cast_at"]
        reel_after = row["reel_after"]
        banned_until = row["banned_until"]
        trigger = int(row["next_cast_line"])

        if "ban" in text or "no hands" in text or "both stumps" in text:
            next_phase = "banned"
            days = re.search(r"(\d+) day", text)
            banned_until = now + (int(days.group(1)) * 86400 if days else BAN_SECONDS)
            cast_at = reel_after = None
        elif phase == "awaiting_cast":
            if "already have a line" in text:
                hours = re.search(r"\(([0-9]+(?:\.[0-9]+)?)h\)", text)
                cast_at = now - int(float(hours.group(1)) * 3600) if hours else now
                next_phase = "fishing"
            elif "cast" in text and not any(
                marker in text for marker in ("haven't unlocked", "no such spot", "usage")
            ):
                cast_at = now
                next_phase = "fishing"
            else:
                next_phase = "idle"
                trigger = random.randint(settings.min_cast_lines, settings.max_cast_lines)
            if next_phase == "fishing":
                delay = random.uniform(settings.min_reel_hours, settings.max_reel_hours)
                reel_after = int(cast_at) + int(delay * 3600)
        elif phase == "awaiting_reel":
            # Every reel attempt consumes a cast; "cast first" is the same recovery path.
            next_phase = "idle"
            cast_at = reel_after = None
            trigger = random.randint(settings.min_cast_lines, settings.max_cast_lines)
        elif phase == "awaiting_dynamite":
            next_phase = "idle"
            trigger = random.randint(settings.min_cast_lines, settings.max_cast_lines)

        # Occasionally make the explicitly ill-advised choice between fishing rounds.
        command: str | None = None
        if (
            next_phase == "idle" and phase != "awaiting_dynamite"
            and random.random() < settings.dynamite_chance
        ):
            next_phase = "awaiting_dynamite"
            command = "!dynamite"

        try:
            await ctx.db.execute("BEGIN IMMEDIATE")
            await ctx.db.execute(
                """UPDATE fishing_state SET
                       phase = ?, eligible_lines_seen = 0, next_cast_line = ?,
                       cast_at = ?, reel_after = ?, command_sent_at = ?,
                       banned_until = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE bot_id = ? AND network = ? AND channel = ?""",
                (next_phase, trigger, cast_at, reel_after,
                 now if command else None, banned_until, ctx.bottle.id,
                 ctx.bottle.irc.network, ctx.message.target),
            )
            await ctx.db.commit()
        except Exception:
            await ctx.db.rollback()
            raise
        if command:
            ctx.commands.append(ModuleCommand(command))

    async def before_prompt(self, ctx: ModuleContext) -> None:
        return None

    async def after_response(self, ctx: ModuleContext) -> None:
        return None

    async def nightly(self, ctx: NightlyContext) -> None:
        return None

    async def _state(self, ctx: ModuleContext):
        return await (await ctx.db.execute(
            """SELECT phase, eligible_lines_seen, next_cast_line, cast_at,
                      reel_after, command_sent_at, banned_until
               FROM fishing_state
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
        minimum = raw.get("min_cast_lines", DEFAULT_MIN_CAST_LINES)
        maximum = raw.get("max_cast_lines", DEFAULT_MAX_CAST_LINES)
        min_hours = raw.get("min_reel_hours", DEFAULT_MIN_REEL_HOURS)
        max_hours = raw.get("max_reel_hours", DEFAULT_MAX_REEL_HOURS)
        chance = raw.get("dynamite_chance", DEFAULT_DYNAMITE_CHANCE)
        if not isinstance(game_nick, str) or not game_nick.strip():
            raise ValueError("fishing game_nick must be a non-empty string")
        if (
            not isinstance(minimum, int) or isinstance(minimum, bool)
            or not isinstance(maximum, int) or isinstance(maximum, bool)
            or minimum < 1 or maximum < minimum
        ):
            raise ValueError("fishing requires integer 1 <= min_cast_lines <= max_cast_lines")
        if (
            not isinstance(min_hours, (int, float)) or isinstance(min_hours, bool)
            or not isinstance(max_hours, (int, float)) or isinstance(max_hours, bool)
            or float(min_hours) < 1.0 or float(max_hours) < float(min_hours)
        ):
            raise ValueError("fishing requires 1 <= min_reel_hours <= max_reel_hours")
        if (
            not isinstance(chance, (int, float)) or isinstance(chance, bool)
            or not 0.0 <= float(chance) <= 1.0
        ):
            raise ValueError("fishing dynamite_chance must be between 0 and 1")
        return Settings(
            channels=frozenset(irc_casefold(channel) for channel in channels),
            game_nick=game_nick.strip(), min_cast_lines=minimum, max_cast_lines=maximum,
            min_reel_hours=float(min_hours), max_reel_hours=float(max_hours),
            dynamite_chance=float(chance),
        )
