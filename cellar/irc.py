import asyncio
import ssl
from collections.abc import Awaitable, Callable

from cellar.models import IRCProfile

MessageHandler = Callable[[str, str, str], Awaitable[None]]


def parse_privmsg(line: str) -> tuple[str, str, str] | None:
    if not line.startswith(":") or " PRIVMSG " not in line or " :" not in line:
        return None
    prefix, rest = line[1:].split(" ", 1)
    command, body = rest.split(" :", 1)
    parts = command.split()
    if len(parts) != 2 or parts[0] != "PRIVMSG":
        return None
    return prefix.split("!", 1)[0], parts[1], body


class IRCClient:
    def __init__(self, profile: IRCProfile, handler: MessageHandler) -> None:
        self.profile = profile
        self.handler = handler
        self.writer: asyncio.StreamWriter | None = None

    async def send_raw(self, line: str) -> None:
        if self.writer is None:
            raise RuntimeError("IRC client is not connected")
        self.writer.write(f"{line}\r\n".encode())
        await self.writer.drain()

    async def send_message(self, target: str, body: str) -> None:
        await self.send_raw(f"PRIVMSG {target} :{body}")

    async def run(self) -> None:
        context = ssl.create_default_context() if self.profile.tls else None
        reader, self.writer = await asyncio.open_connection(
            self.profile.host, self.profile.port, ssl=context
        )
        if self.profile.password:
            await self.send_raw(f"PASS {self.profile.password}")
        await self.send_raw(f"NICK {self.profile.nick}")
        await self.send_raw(f"USER {self.profile.username} 0 * :{self.profile.realname}")
        for channel in self.profile.channels:
            await self.send_raw(f"JOIN {channel}")
        while raw := await reader.readline():
            line = raw.decode(errors="replace").rstrip("\r\n")
            if line.startswith("PING "):
                await self.send_raw(f"PONG {line[5:]}")
                continue
            parsed = parse_privmsg(line)
            if parsed:
                await self.handler(*parsed)
