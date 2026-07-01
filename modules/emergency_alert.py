import re

from cellar.admin_store import enqueue_admin_event
from cellar.irc import irc_casefold, mentions_nick
from cellar.module_api import ModuleContext, NightlyContext, RuntimeContext

URGENT_MARKER = re.compile(r"^\[URGENT:\s*([^\]\r\n]{1,200})\]", re.IGNORECASE)


class Module:
    async def start(self, ctx: RuntimeContext) -> None:
        self._discord_user_id(ctx.module_settings)

    async def stop(self, ctx: RuntimeContext) -> None:
        return None

    async def on_message(self, ctx: ModuleContext) -> None:
        direct = irc_casefold(ctx.message.target) == irc_casefold(ctx.bottle.irc.nick)
        if direct or mentions_nick(ctx.message.body, ctx.bottle.irc.nick):
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
        discord_user_id = self._discord_user_id(ctx.module_settings)
        summary = " ".join(match.group(1).split())
        source = " ".join(ctx.message.body.split())
        message = (
            f"<@{discord_user_id}> **EMERGENCY**: {summary}\n"
            f"{ctx.bottle.irc.network} {ctx.message.target} <{ctx.message.nick}> {source}"
        )
        await enqueue_admin_event(
            ctx.db, bottle_id=ctx.bottle.id, event_type="emergency",
            message=message, source_message_id=ctx.source_message_id,
        )

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
