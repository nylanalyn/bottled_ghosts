import hmac
import ipaddress
import logging
from typing import Any

from aiohttp import web

from cellar.admin_store import (
    admin_api_token,
    away_status,
    consume_admin_events,
    response_enabled,
    set_away_status,
    set_response_enabled,
)
from cellar.irc import mentions_any_nick
from cellar.llm import complete
from cellar.module_api import ModuleContext, NightlyContext, RuntimeContext
from cellar.storage import recent_channel_message_records
from modules.moods import mood_status_line

logger = logging.getLogger(__name__)


def _active_module_names(
    module_settings: dict[str, dict[str, object]], failed_modules: dict[str, str],
) -> tuple[str, ...]:
    return tuple(
        name for name in sorted(module_settings)
        if name not in failed_modules
    )


class Module:
    def __init__(self) -> None:
        self._runner: web.AppRunner | None = None

    async def start(self, ctx: RuntimeContext) -> None:
        settings = ctx.module_settings.get("admin_api", {})
        host = str(settings.get("host", "127.0.0.1")).strip()
        token = await admin_api_token(ctx.db, bottle_id=ctx.bottle.id)
        raw_port = settings.get("port", 9100)
        try:
            if isinstance(raw_port, bool) or not isinstance(raw_port, (int, str)):
                raise ValueError
            port = int(raw_port)
        except (TypeError, ValueError) as error:
            raise ValueError("admin_api port must be an integer") from error
        if not token:
            raise ValueError("admin_api token is required; run set-admin-token")
        if not 1 <= port <= 65535:
            raise ValueError("admin_api port must be between 1 and 65535")
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("admin_api must bind to a loopback address")
        except ValueError as error:
            if "must bind" in str(error):
                raise
            raise ValueError("admin_api host must be a loopback IP address") from error

        @web.middleware
        async def authenticate(request: web.Request, handler: Any) -> web.StreamResponse:
            if request.path != "/health":
                expected = f"Bearer {token}"
                supplied = request.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied, expected):
                    return web.json_response({"error": "unauthorized"}, status=401)
            return await handler(request)

        async def health(request: web.Request) -> web.Response:
            return await self._health()

        async def command(request: web.Request) -> web.Response:
            return await self._command(ctx, request)

        async def events(request: web.Request) -> web.Response:
            return await self._events(ctx, request)

        app = web.Application(middlewares=[authenticate])
        app.add_routes([
            web.get("/health", health),
            web.post("/v1/command", command),
            web.get("/v1/events", events),
        ])
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, host, port).start()
        except Exception:
            await self._runner.cleanup()
            self._runner = None
            raise

    async def stop(self, ctx: RuntimeContext) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _health(self) -> web.Response:
        return web.json_response({"ok": True})

    async def _command(self, ctx: RuntimeContext, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "invalid json"}, status=400)
        command_text = str(payload.get("command", "")).strip()
        args = str(payload.get("args", "")).strip()
        if args:
            command_text = f"{command_text} {args}".strip()
        if not command_text:
            return web.json_response({"error": "command is required"}, status=400)
        command, _, argument = command_text.partition(" ")
        command = command.lower()
        argument = argument.strip()
        if command in {"summarize", "summary"}:
            async with ctx.database_lock:
                summary_data = await self._summary_data(ctx, argument)
            if isinstance(summary_data, list):
                return web.json_response({"messages": summary_data})
            channel, lines = summary_data
            return web.json_response({"messages": await self._summarize(ctx, channel, lines)})
        async with ctx.database_lock:
            if command == "help":
                messages = [
                    "help - this message\nstatus - Bottle status, active modules, and mood when the moods module is active\nmodel - current LLM model\n"
                    "off - stop public model responses\non - resume public model responses\n"
                    "away <message> - set an availability note\nback - clear the availability note\n"
                    "summarize [#channel] - summarize the last 50 room lines and report watched-nick pings"
                ]
            elif command == "status":
                enabled = await response_enabled(ctx.db, bottle_id=ctx.bottle.id)
                active_modules = _active_module_names(ctx.module_settings, ctx.state.failed_modules)
                messages = [
                    "admin: connected\n"
                    f"irc: {'connected' if ctx.state.irc_connected else 'disconnected'}\n"
                    f"model: {ctx.bottle.llm.model}\n"
                    f"responding: {'yes' if enabled else 'OFF (monitoring emergencies)'}\n"
                    f"modules: {', '.join(active_modules) if active_modules else 'none'}"
                ]
                if ctx.state.failed_modules:
                    failures = ", ".join(
                        f"{name} ({hook})"
                        for name, hook in sorted(ctx.state.failed_modules.items())
                    )
                    messages[0] += f"\nmodules disabled after errors: {failures}"
                mood_block = await mood_status_line(ctx.db, ctx.bottle.id)
                if mood_block is not None:
                    messages.append(mood_block)
                away = await away_status(ctx.db, bottle_id=ctx.bottle.id)
                messages.append(f"away: {away or 'no'}")
            elif command == "model":
                messages = [f"model: {ctx.bottle.llm.model}"]
            elif command in {"off", "on"}:
                enabled = command == "on"
                await set_response_enabled(
                    ctx.db, bottle_id=ctx.bottle.id, enabled=enabled,
                    actor="discord-admin",
                )
                messages = [
                    f"{ctx.bottle.name} is "
                    + ("back online." if enabled else "now silent; emergency monitoring remains active.")
                ]
            elif command == "away":
                if not argument:
                    away = await away_status(ctx.db, bottle_id=ctx.bottle.id)
                    messages = [f"away: {away or 'no'}"]
                elif argument.lower() in {"off", "clear", "back"}:
                    await set_away_status(ctx.db, bottle_id=ctx.bottle.id, message=None)
                    messages = [f"{ctx.bottle.name} is back."]
                else:
                    await set_away_status(
                        ctx.db, bottle_id=ctx.bottle.id, message=argument,
                    )
                    messages = [f"away status set: {argument}"]
            elif command == "back":
                await set_away_status(ctx.db, bottle_id=ctx.bottle.id, message=None)
                messages = [f"{ctx.bottle.name} is back."]
            else:
                messages = [f"unknown command: {command}"]
        return web.json_response({"messages": messages})

    async def _summary_data(
        self, ctx: RuntimeContext, requested_channel: str,
    ) -> tuple[str, list[tuple[str, str, str]]] | list[str]:
        configured_channels = ctx.bottle.irc.channels
        channel = requested_channel or str(
            ctx.module_settings.get("admin_api", {}).get("summary_channel", "")
        ).strip()
        if not channel:
            channel = configured_channels[0] if configured_channels else ""
        if channel not in configured_channels:
            return ["summary channel must be one of this Bottle's configured IRC channels"]
        lines = await recent_channel_message_records(
            ctx.db, bot_id=ctx.bottle.id, network=ctx.bottle.irc.network,
            channel=channel, limit=50,
        )
        if not lines:
            return [f"No recorded messages in {channel} yet."]
        return channel, lines

    async def _summarize(
        self, ctx: RuntimeContext, channel: str, lines: list[tuple[str, str, str]],
    ) -> list[str]:
        transcript = "\n".join(
            f"<{speaker}> {body[:450]}" for _timestamp, speaker, body in lines
        )
        try:
            summary = await complete(ctx.bottle.llm, [
                {"role": "system", "content": (
                    "Give a short factual Discord admin summary of this IRC room. "
                    "State the main topics, decisions, and unresolved questions. "
                    "Do not roleplay, invent details, or address the channel."
                )},
                {"role": "user", "content": f"Room {channel}, last {len(lines)} lines:\n{transcript}"},
            ])
        except Exception:
            logger.exception("admin summary failed for Bottle %d", ctx.bottle.id)
            return ["Unable to summarize the room right now."]
        messages = [f"{channel} summary:\n{summary.strip()[:1800]}"]
        watched = self._watched_nicks(ctx.module_settings)
        pings = [
            f"{channel} <{speaker}> {body}"
            for _timestamp, speaker, body in lines
            if mentions_any_nick(body, watched)
        ]
        if pings:
            messages.append("Watched-nick pings (verbatim):")
            messages.extend(pings)
        return messages

    @staticmethod
    def _watched_nicks(settings: dict[str, dict[str, object]]) -> tuple[str, ...]:
        raw = settings.get("admin_api", {}).get("watch_nicks", [])
        if not isinstance(raw, list):
            return ()
        return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())

    async def _events(self, ctx: RuntimeContext, request: web.Request) -> web.Response:
        raw_since = request.query.get("since", "0")
        try:
            since = int(raw_since)
        except ValueError:
            return web.json_response({"error": "since must be an integer"}, status=400)
        if since < 0:
            return web.json_response({"error": "since must be non-negative"}, status=400)
        async with ctx.database_lock:
            events = await consume_admin_events(
                ctx.db, bottle_id=ctx.bottle.id, since=since,
            )
        return web.json_response({"events": events})

    async def on_message(self, ctx: ModuleContext) -> None:
        return None

    async def before_prompt(self, ctx: ModuleContext) -> None:
        return None

    async def after_response(self, ctx: ModuleContext) -> None:
        return None

    async def nightly(self, ctx: NightlyContext) -> None:
        return None
