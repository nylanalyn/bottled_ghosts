import random

from cellar.irc import irc_casefold
from cellar.module_api import ModuleContext, NightlyContext

DEFAULT_MIN_LINES = 20
DEFAULT_MAX_LINES = 40


class Module:
    async def on_message(self, ctx: ModuleContext) -> None:
        if not ctx.response_allowed:
            return
        if irc_casefold(ctx.message.target) == irc_casefold(ctx.bottle.irc.nick):
            return
        if irc_casefold(ctx.message.nick) == irc_casefold(ctx.bottle.irc.nick):
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

    async def before_prompt(self, ctx: ModuleContext) -> None:
        if ctx.response_reason == "ambient":
            ctx.prompt_sections.append(
                "This is an occasional ambient contribution, not a direct reply. "
                "Respond naturally to the current channel conversation without claiming "
                "someone addressed you."
            )

    async def after_response(self, ctx: ModuleContext) -> None:
        if irc_casefold(ctx.message.target) == irc_casefold(ctx.bottle.irc.nick):
            return
        if ctx.response_reason == "ambient":
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
