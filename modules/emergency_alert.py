import re
import time

from cellar.admin_store import enqueue_admin_event
from cellar.irc import irc_casefold, mentions_any_nick
from cellar.module_api import ModuleContext, NightlyContext, RuntimeContext

URGENT_MARKER = re.compile(r"^\[URGENT:\s*([^\]\r\n]{1,200})\]", re.IGNORECASE)
DEFAULT_COOLDOWN_SECONDS = 900


class Module:
    async def start(self, ctx: RuntimeContext) -> None:
        self._discord_user_id(ctx.module_settings)

    async def stop(self, ctx: RuntimeContext) -> None:
        return None

    async def on_message(self, ctx: ModuleContext) -> None:
        direct = irc_casefold(ctx.message.target) == irc_casefold(ctx.bottle.irc.nick)
        if direct or mentions_any_nick(ctx.message.body, ctx.bottle.address_names):
            ctx.monitor_when_silent = True

    async def before_prompt(self, ctx: ModuleContext) -> None:
        ctx.prompt_sections.append(
            "Emergency monitoring is active. If the current addressed message and "
            "retrieved IRC context show a genuinely immediate problem requiring Rumi's "
            "attention, begin exactly with [URGENT: short factual summary]. Do not use "
            "the marker for jokes, historical problems, vague concern, or ordinary urgency."
        )

    async def after_response(self, ctx: ModuleContext) -> None:
        if ctx.response is None:
            return
        match = URGENT_MARKER.match(ctx.response.lstrip())
        if match is None:
            return
        ctx.response = URGENT_MARKER.sub("", ctx.response.lstrip(), count=1).lstrip()
        discord_user_id = self._discord_user_id(ctx.module_settings)
        cooldown = self._cooldown(ctx.module_settings)
        now = int(time.time())
        row = await (await ctx.db.execute(
            """SELECT last_alert_at FROM emergency_alert_state
               WHERE bot_id = ? AND network = ? AND channel = ?""",
            (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target),
        )).fetchone()
        if row is not None and now - int(row["last_alert_at"]) < cooldown:
            return
        summary = " ".join(match.group(1).split())
        source = " ".join(ctx.message.body.split())
        message = (
            f"<@{discord_user_id}> **EMERGENCY**: {summary}\n"
            f"{ctx.bottle.irc.network} {ctx.message.target} <{ctx.message.nick}> {source}"
        )
        inserted = await enqueue_admin_event(
            ctx.db, bottle_id=ctx.bottle.id, event_type="emergency",
            message=message, source_message_id=ctx.source_message_id,
        )
        if inserted:
            await ctx.db.execute(
                """INSERT INTO emergency_alert_state(
                       bot_id, network, channel, last_alert_at
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(bot_id, network, channel) DO UPDATE SET
                       last_alert_at = excluded.last_alert_at""",
                (ctx.bottle.id, ctx.bottle.irc.network, ctx.message.target, now),
            )
            await ctx.db.commit()

    async def nightly(self, ctx: NightlyContext) -> None:
        return None

    @staticmethod
    def _discord_user_id(settings: dict[str, dict[str, object]]) -> int:
        value = settings.get("emergency_alert", {}).get("discord_user_id")
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("emergency_alert discord_user_id must be an integer")
        try:
            user_id = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError("emergency_alert discord_user_id must be an integer") from error
        if user_id <= 0:
            raise ValueError("emergency_alert discord_user_id must be positive")
        return user_id

    @staticmethod
    def _cooldown(settings: dict[str, dict[str, object]]) -> int:
        value = settings.get("emergency_alert", {}).get(
            "cooldown_seconds", DEFAULT_COOLDOWN_SECONDS
        )
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("emergency_alert cooldown_seconds must be a non-negative integer")
        return value
