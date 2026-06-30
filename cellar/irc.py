import asyncio
import base64
import logging
import re
import ssl
from collections.abc import Awaitable, Callable

from cellar.models import IRCProfile, IncomingIRCMessage

MessageHandler = Callable[[IncomingIRCMessage], Awaitable[None]]
logger = logging.getLogger(__name__)
IRC_PAYLOAD_BYTES = 510
IRC_NICK_CHARACTERS = r"A-Za-z0-9\-\[\]\\`_^{|}~"


def irc_casefold(value: str) -> str:
    return value.lower().translate(str.maketrans("[]\\^", "{}|~"))


def mentions_nick(text: str, nick: str) -> bool:
    folded_text = irc_casefold(text)
    folded_nick = re.escape(irc_casefold(nick))
    return re.search(
        rf"(?<![{IRC_NICK_CHARACTERS}]){folded_nick}(?![{IRC_NICK_CHARACTERS}])",
        folded_text,
    ) is not None


def truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes < 0:
        raise ValueError("UTF-8 byte limit cannot be negative")
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def parse_privmsg(line: str) -> IncomingIRCMessage | None:
    tags: dict[str, str | None] = {}
    if line.startswith("@"):
        if " " not in line:
            return None
        raw_tags, line = line.split(" ", 1)
        for item in raw_tags[1:].split(";"):
            key, separator, value = item.partition("=")
            tags[key] = value if separator else None
    if not line.startswith(":") or " PRIVMSG " not in line or " :" not in line:
        return None
    prefix, rest = line[1:].split(" ", 1)
    command, body = rest.split(" :", 1)
    parts = command.split()
    if len(parts) != 2 or parts[0] != "PRIVMSG":
        return None
    nick, separator, hostmask = prefix.partition("!")
    account = tags.get("account")
    return IncomingIRCMessage(nick=nick, hostmask=hostmask if separator else None,
                              account=account if account and account != "*" else None,
                              target=parts[1], body=body)


def sasl_plain_chunks(username: str, password: str) -> list[str]:
    payload = base64.b64encode(f"{username}\0{username}\0{password}".encode()).decode()
    chunks = [payload[start:start + 400] for start in range(0, len(payload), 400)]
    if len(payload) % 400 == 0:
        chunks.append("+")
    return chunks


class IRCClient:
    def __init__(self, profile: IRCProfile, handler: MessageHandler) -> None:
        self.profile = profile
        self.handler = handler
        self.writer: asyncio.StreamWriter | None = None
        self.capabilities: set[str] = set()
        self.pending_capabilities: set[str] = set()
        self.sasl_authenticating = False

    async def send_raw(self, line: str) -> None:
        if self.writer is None:
            raise RuntimeError("IRC client is not connected")
        encoded = line.encode("utf-8")
        if len(encoded) > IRC_PAYLOAD_BYTES:
            raise ValueError("IRC protocol line exceeds 510 bytes")
        self.writer.write(encoded + b"\r\n")
        await self.writer.drain()

    async def send_message(self, target: str, body: str) -> None:
        prefix = f"PRIVMSG {target} :"
        available = IRC_PAYLOAD_BYTES - len(prefix.encode("utf-8"))
        if available < 1:
            raise ValueError("IRC message target leaves no room for a body")
        await self.send_raw(f"{prefix}{truncate_utf8(body, available)}")

    async def authenticate_sasl_plain(self) -> None:
        username = self.profile.sasl_username
        password = self.profile.sasl_password
        if username is None or password is None:
            raise RuntimeError("SASL credentials are incomplete")
        for chunk in sasl_plain_chunks(username, password):
            await self.send_raw(f"AUTHENTICATE {chunk}")

    async def run(self) -> None:
        context = ssl.create_default_context() if self.profile.tls else None
        reader, self.writer = await asyncio.open_connection(
            self.profile.host, self.profile.port, ssl=context
        )
        try:
            logger.info("connected to %s:%d (TLS: %s)", self.profile.host, self.profile.port,
                        self.profile.tls)
            await self.send_raw("CAP LS 302")
            if self.profile.password:
                await self.send_raw(f"PASS {self.profile.password}")
            await self.send_raw(f"NICK {self.profile.nick}")
            await self.send_raw(f"USER {self.profile.username} 0 * :{self.profile.realname}")
            while raw := await reader.readline():
                line = raw.decode(errors="replace").rstrip("\r\n")
                if line.startswith("PING "):
                    await self.send_raw(f"PONG {line[5:]}")
                    continue
                if " CAP " in line and " LS " in line:
                    self.capabilities.update(
                        item.split("=", 1)[0] for item in line.rsplit(" :", 1)[-1].split()
                    )
                    if " LS * :" in line:
                        continue
                    if self.profile.sasl_username:
                        if "sasl" not in self.capabilities:
                            raise RuntimeError("IRC server does not advertise SASL")
                    if self.profile.sasl_username:
                        logger.info("requesting SASL PLAIN authentication")
                        self.pending_capabilities.add("sasl")
                        await self.send_raw("CAP REQ :sasl")
                    if "account-tag" in self.capabilities:
                        self.pending_capabilities.add("account-tag")
                        await self.send_raw("CAP REQ :account-tag")
                    if not self.pending_capabilities:
                        await self.send_raw("CAP END")
                    continue
                if " CAP " in line and " NAK " in line:
                    rejected = line.rsplit(" :", 1)[-1].split()
                    self.pending_capabilities.difference_update(rejected)
                    if self.profile.sasl_username and "sasl" in rejected:
                        raise RuntimeError("IRC server rejected the SASL capability request")
                    if not self.pending_capabilities and not self.sasl_authenticating:
                        await self.send_raw("CAP END")
                    continue
                if " CAP " in line and " ACK " in line:
                    acknowledged = line.rsplit(" :", 1)[-1].split()
                    self.pending_capabilities.difference_update(acknowledged)
                    if "sasl" in acknowledged:
                        self.sasl_authenticating = True
                        await self.send_raw("AUTHENTICATE PLAIN")
                    elif not self.pending_capabilities and not self.sasl_authenticating:
                        await self.send_raw("CAP END")
                    continue
                if line == "AUTHENTICATE +":
                    await self.authenticate_sasl_plain()
                    continue
                parts = line.split()
                numeric = parts[1] if len(parts) > 1 and parts[1].isdigit() else None
                if numeric == "903":
                    logger.info("SASL authentication succeeded")
                    self.sasl_authenticating = False
                    await self.send_raw("CAP END")
                    continue
                if numeric in {"904", "905", "906", "907"}:
                    raise RuntimeError(f"SASL authentication failed (IRC {numeric})")
                if numeric == "001":
                    logger.info("IRC registration complete as %s", self.profile.nick)
                    for channel in self.profile.channels:
                        await self.send_raw(f"JOIN {channel}")
                        logger.info("joining %s", channel)
                    continue
                if line.startswith("ERROR "):
                    raise ConnectionError(line)
                parsed = parse_privmsg(line)
                if parsed:
                    try:
                        await self.handler(parsed)
                    except Exception:
                        logger.exception("failed to handle message from %s in %s",
                                         parsed.nick, parsed.target)
            raise ConnectionError("IRC server closed the connection")
        finally:
            self.writer.close()
            await self.writer.wait_closed()
            self.writer = None
            logger.info("disconnected from %s", self.profile.network)
