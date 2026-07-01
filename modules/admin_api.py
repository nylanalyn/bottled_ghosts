import hmac
import ipaddress
from typing import Any

from aiohttp import web

from cellar.admin_store import (
    admin_api_token,
    consume_admin_events,
    response_enabled,
    set_response_enabled,
)
from cellar.module_api import ModuleContext, NightlyContext, RuntimeContext


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
        command = str(payload.get("command", "")).strip().lower()
        if not command:
            return web.json_response({"error": "command is required"}, status=400)
        async with ctx.database_lock:
            if command == "help":
                messages = [
                    "help - this message\nstatus - Bottle status\nmodel - current LLM model\n"
                    "off - stop public model responses\non - resume public model responses"
                ]
            elif command == "status":
                enabled = await response_enabled(ctx.db, bottle_id=ctx.bottle.id)
                messages = [
                    "admin: connected\n"
                    f"irc: {'connected' if ctx.state.irc_connected else 'disconnected'}\n"
                    f"model: {ctx.bottle.llm.model}\n"
                    f"responding: {'yes' if enabled else 'OFF (monitoring emergencies)'}"
                ]
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
            else:
                messages = [f"unknown command: {command}"]
        return web.json_response({"messages": messages})

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
