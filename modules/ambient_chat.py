import random

from cellar.irc import irc_casefold, mentions_any_nick
from cellar.module_api import ModuleContext, NightlyContext

DEFAULT_MIN_LINES = 20
DEFAULT_MAX_LINES = 40
DEFAULT_UTILITY_MIN_LINES = 8
DEFAULT_UTILITY_MAX_LINES = 15


class Module:
    async def on_message(self, ctx: ModuleContext) -> None:
        if not ctx.response_allowed:
            return
        if irc_casefold(ctx.message.target) == irc_casefold(ctx.bottle.irc.nick):
            return
        if irc_casefold(ctx.message.nick) == irc_casefold(ctx.bottle.irc.nick):
            return
        utility_nicks, util_minimum, util_maximum = self._utility_settings(ctx)
        if irc_casefold(ctx.message.nick) in utility_nicks:
            await self._handle_utility_event(ctx, util_minimum, util_maximum)
            return
        minimum, maximum = self._limits(ctx)
        try:
            await ctx.db.execute("BEGIN IMMEDIATE")
            row = await (await ctx.db.execute(
                """SELECT eligible_lines_seen, next_trigger_line
                   FROM ambient_chat_state
                   WHERE bot_id = ? AND network = ? AND channel = ?""",
                (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target),
            )).fetchone()
            if row is None:
                lines_seen = 1
                trigger = random.randint(minimum, maximum)
                requested = lines_seen >= trigger
                stored_lines = 0 if requested else lines_seen
                stored_trigger = (
                    random.randint(minimum, maximum) if requested else trigger
                )
                await ctx.db.execute(
                    """INSERT INTO ambient_chat_state(
                           bot_id, network, channel, eligible_lines_seen, next_trigger_line
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target,
                     stored_lines, stored_trigger),
                )
            else:
                lines_seen = int(row["eligible_lines_seen"]) + 1
                trigger = int(row["next_trigger_line"])
                requested = lines_seen >= trigger
                stored_lines = 0 if requested else lines_seen
                stored_trigger = (
                    random.randint(minimum, maximum) if requested else trigger
                )
                await ctx.db.execute(
                    """UPDATE ambient_chat_state
                       SET eligible_lines_seen = ?, next_trigger_line = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE bot_id = ? AND network = ? AND channel = ?""",
                    (stored_lines, stored_trigger, ctx.bottle.id,
                     ctx.bottle.irc.network, ctx.message.target),
                )
            await ctx.db.commit()
        except Exception:
            await ctx.db.rollback()
            raise
        if requested:
            ctx.request_response = True

    async def _handle_utility_event(
        self, ctx: ModuleContext, util_minimum: int, util_maximum: int,
    ) -> None:
        # Any configured utility-bot channel message vetoes the runtime's
        # automatic addressed/window scheduling. An explicitly sampled reaction
        # below still reaches generation through request_response.
        ctx.suppress_automatic_response = True
        active = ctx.bot_nick or ctx.bottle.irc.nick
        names = (active, *ctx.bottle.address_names)
        if not mentions_any_nick(ctx.message.body, names):
            # Unrelated utility traffic: never reply and never make the next
            # human ambient response closer.
            return
        minimum, maximum = self._limits(ctx)
        try:
            await ctx.db.execute("BEGIN IMMEDIATE")
            row = await (await ctx.db.execute(
                """SELECT eligible_lines_seen, next_trigger_line,
                          utility_lines_seen, next_utility_trigger_line
                   FROM ambient_chat_state
                   WHERE bot_id = ? AND network = ? AND channel = ?""",
                (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target),
            )).fetchone()
            if row is None:
                # First ever channel activity: seed normal cadence for the next
                # human line while recording utility progress for this event.
                normal_trigger = random.randint(minimum, maximum)
                threshold = random.randint(util_minimum, util_maximum)
                util_seen = 1
                requested = util_seen >= threshold
                stored_util_seen = 0 if requested else util_seen
                stored_util_trigger = (
                    random.randint(util_minimum, util_maximum)
                    if requested else threshold
                )
                await ctx.db.execute(
                    """INSERT INTO ambient_chat_state(
                           bot_id, network, channel, eligible_lines_seen,
                           next_trigger_line, utility_lines_seen,
                           next_utility_trigger_line
                       ) VALUES (?, ?, ?, 0, ?, ?, ?)""",
                    (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target,
                     normal_trigger, stored_util_seen, stored_util_trigger),
                )
            else:
                current_threshold = row["next_utility_trigger_line"]
                threshold = (
                    random.randint(util_minimum, util_maximum)
                    if current_threshold is None
                    else int(current_threshold)
                )
                util_seen = int(row["utility_lines_seen"]) + 1
                requested = util_seen >= threshold
                stored_util_seen = 0 if requested else util_seen
                stored_util_trigger = (
                    random.randint(util_minimum, util_maximum)
                    if requested else threshold
                )
                await ctx.db.execute(
                    """UPDATE ambient_chat_state
                       SET utility_lines_seen = ?, next_utility_trigger_line = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE bot_id = ? AND network = ? AND channel = ?""",
                    (stored_util_seen, stored_util_trigger, ctx.bottle.id,
                     ctx.bottle.irc.network, ctx.message.target),
                )
            await ctx.db.commit()
        except Exception:
            await ctx.db.rollback()
            raise
        if requested:
            ctx.request_response = True
            ctx.response_reason = "utility_event"

    async def before_prompt(self, ctx: ModuleContext) -> None:
        if ctx.response_reason == "ambient":
            ctx.prompt_sections.append(
                "This is an occasional ambient contribution, not a direct reply. "
                "Respond naturally to the current channel conversation without claiming "
                "someone addressed you."
            )
        elif ctx.response_reason == "utility_event":
            ctx.prompt_sections.append(
                "This is an occasional reaction to a relevant channel event from an "
                "automated utility bot, not a reply to an invitation or question. "
                "Respond naturally without claiming someone addressed you."
            )

    async def after_response(self, ctx: ModuleContext) -> None:
        if irc_casefold(ctx.message.target) == irc_casefold(ctx.bottle.irc.nick):
            return
        if ctx.response_reason in ("ambient", "utility_event"):
            return
        minimum, maximum = self._limits(ctx)
        next_trigger = random.randint(minimum, maximum)
        try:
            await ctx.db.execute("BEGIN IMMEDIATE")
            await ctx.db.execute(
                """INSERT INTO ambient_chat_state(
                       bot_id, network, channel, eligible_lines_seen, next_trigger_line
                   ) VALUES (?, ?, ?, 0, ?)
                   ON CONFLICT(bot_id, network, channel) DO UPDATE SET
                       eligible_lines_seen = 0,
                       next_trigger_line = excluded.next_trigger_line,
                       updated_at = CURRENT_TIMESTAMP""",
                (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target, next_trigger),
            )
            await ctx.db.commit()
        except Exception:
            await ctx.db.rollback()
            raise

    async def nightly(self, ctx: NightlyContext) -> None:
        return None

    def _limits(self, ctx: ModuleContext) -> tuple[int, int]:
        settings = ctx.module_settings.get("ambient_chat", {})
        minimum = settings.get("min_lines", DEFAULT_MIN_LINES)
        maximum = settings.get("max_lines", DEFAULT_MAX_LINES)
        if (
            not isinstance(minimum, int) or isinstance(minimum, bool)
            or not isinstance(maximum, int) or isinstance(maximum, bool)
            or minimum < 1 or maximum < minimum
        ):
            raise ValueError("ambient_chat requires integer 1 <= min_lines <= max_lines")
        return minimum, maximum

    def _utility_settings(self, ctx: ModuleContext) -> tuple[tuple[str, ...], int, int]:
        settings = ctx.module_settings.get("ambient_chat", {})
        minimum = settings.get("utility_min_lines", DEFAULT_UTILITY_MIN_LINES)
        maximum = settings.get("utility_max_lines", DEFAULT_UTILITY_MAX_LINES)
        if (
            not isinstance(minimum, int) or isinstance(minimum, bool)
            or not isinstance(maximum, int) or isinstance(maximum, bool)
            or minimum < 1 or maximum < minimum
        ):
            raise ValueError(
                "ambient_chat requires integer "
                "1 <= utility_min_lines <= utility_max_lines"
            )
        raw_nicks = settings.get("utility_bot_nicks")
        if raw_nicks is None:
            return (), minimum, maximum
        if not isinstance(raw_nicks, list):
            raise ValueError("ambient_chat requires utility_bot_nicks to be a list")
        folded: list[str] = []
        for nick in raw_nicks:
            if not isinstance(nick, str) or not nick:
                raise ValueError(
                    "ambient_chat requires utility_bot_nicks to be non-empty strings"
                )
            folded_nick = irc_casefold(nick)
            if folded_nick in folded:
                raise ValueError(
                    "ambient_chat utility_bot_nicks must not contain duplicates"
                )
            folded.append(folded_nick)
        return tuple(folded), minimum, maximum
