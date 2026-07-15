import asyncio
import base64
import logging
import re
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cellar.models import IRCProfile, IncomingIRCMessage

MessageHandler = Callable[[IncomingIRCMessage], Awaitable[None]]
KickHandler = Callable[["IRCKickEvent"], Awaitable[None]]
logger = logging.getLogger(__name__)
IRC_PAYLOAD_BYTES = 510
IRC_NICK_CHARACTERS = r"A-Za-z0-9\-\[\]\\`_^{|}~"
IRC_REGISTRATION_TIMEOUT_SECONDS = 30.0
IRC_IDLE_TIMEOUT_SECONDS = 300.0
IRC_PONG_TIMEOUT_SECONDS = 60.0
IRC_LINEBREAK_RE = re.compile(r"[\r\n]+")
IRC_FORMATTING_RE = re.compile(
    r"\x03(?:\d{1,2}(?:,\d{1,2})?)?|\x04(?:[0-9A-Fa-f]{6}(?:,[0-9A-Fa-f]{6})?)?|"
    r"[\x00-\x02\x05-\x08\x0b\x0c\x0e-\x1f\x7f]"
)


class IRCAuthenticationError(RuntimeError):
    """Fatal authentication/configuration failure that should not reconnect rapidly."""


class IRCNickCollisionError(ConnectionError):
    """All configured nick choices are currently in use."""


@dataclass(frozen=True)
class IRCKickEvent:
    """A server-confirmed removal of this client from an IRC channel."""

    channel: str
    kicker: str
    reason: str


class IRCKickedError(ConnectionError):
    """Reconnect after a deliberate delay following a channel KICK."""

    def __init__(self, event: IRCKickEvent) -> None:
        self.event = event
        super().__init__(f"kicked from {event.channel} by {event.kicker}: {event.reason}")


def irc_casefold(value: str) -> str:
    return value.lower().translate(str.maketrans("[]\\^", "{}|~"))


def mentions_nick(text: str, nick: str) -> bool:
    folded_text = irc_casefold(text)
    folded_nick = re.escape(irc_casefold(nick))
    return re.search(
        rf"(?<![{IRC_NICK_CHARACTERS}]){folded_nick}(?![{IRC_NICK_CHARACTERS}])",
        folded_text,
    ) is not None


def mentions_any_nick(text: str, names: tuple[str, ...] | list[str]) -> bool:
    return any(mentions_nick(text, name) for name in names)


def truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes < 0:
        raise ValueError("UTF-8 byte limit cannot be negative")
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def single_line_irc_text(text: str) -> str:
    """Collapse line breaks before interpolating text into an IRC command."""
    return IRC_LINEBREAK_RE.sub(" ", text).strip()


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
    if body.startswith("\x01") and body.endswith("\x01"):
        ctcp_payload = body[1:-1]
        ctcp_command, separator, ctcp_body = ctcp_payload.partition(" ")
        if ctcp_command.upper() != "ACTION" or not separator or not ctcp_body.strip():
            return None
        body = f"/me {ctcp_body}"
    body = IRC_FORMATTING_RE.sub("", body)
    return IncomingIRCMessage(nick=nick, hostmask=hostmask if separator else None,
                              account=account if account and account != "*" else None,
                              target=parts[1], body=body)


def parse_irc_command(line: str) -> tuple[str, list[str]]:
    """Return an IRC command and parameters, ignoring tags and source prefix."""
    if line.startswith("@"):
        _, separator, line = line.partition(" ")
        if not separator:
            return "", []
    if line.startswith(":"):
        _, separator, line = line.partition(" ")
        if not separator:
            return "", []
    command, separator, raw_params = line.partition(" ")
    if not command:
        return "", []
    if not separator:
        return command.upper(), []
    if raw_params.startswith(":"):
        params = [raw_params[1:]]
    elif " :" in raw_params:
        middle, trailing = raw_params.split(" :", 1)
        params = [*middle.split(), trailing]
    else:
        params = raw_params.split()
    return command.upper(), params


def capability_names(items: list[str]) -> set[str]:
    """Normalize IRCv3 capability tokens to their capability names."""
    return {
        item.lstrip(":-~=").split("=", 1)[0]
        for item in items
        if item.lstrip(":-~=")
    }


def sasl_plain_chunks(username: str, password: str) -> list[str]:
    payload = base64.b64encode(f"\0{username}\0{password}".encode()).decode()
    chunks = [payload[start:start + 400] for start in range(0, len(payload), 400)]
    if len(payload) % 400 == 0:
        chunks.append("+")
    return chunks


class IRCClient:
    def __init__(
        self, profile: IRCProfile, handler: MessageHandler,
        kick_handler: KickHandler | None = None,
    ) -> None:
        self.profile = profile
        self.handler = handler
        self.kick_handler = kick_handler
        self.writer: asyncio.StreamWriter | None = None
        self.capabilities: set[str] = set()
        self.pending_capabilities: set[str] = set()
        self.sasl_authenticating = False
        self.current_nick = profile.nick
        self._nick_choices = [profile.nick, *profile.alternate_nicks]
        self._nick_index = 0
        self.join_channels = list(profile.channels)
        self.connection_state_handler: Callable[[bool], None] | None = None

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

    async def part_channel(self, channel: str, reason: str = "Taking thirty minutes to breathe.") -> None:
        """Leave one channel with a bounded, single-line IRC PART reason."""
        prefix = f"PART {channel} :"
        available = IRC_PAYLOAD_BYTES - len(prefix.encode("utf-8"))
        await self.send_raw(f"{prefix}{truncate_utf8(single_line_irc_text(reason), available)}")

    async def join_channel(self, channel: str) -> None:
        await self.send_raw(f"JOIN {channel}")

    async def quit(self, message: str | None = None) -> None:
        """Send a graceful IRC QUIT before closing the connection.

        Best-effort: sends a single sanitized QUIT line and attempts to drain it.
        This avoids the TLS abrupt-close error that occurs when the connection is
        torn down without telling the server.
        """
        if self.writer is None:
            return
        text = single_line_irc_text(message if message is not None else self.profile.quit_message)
        if not text:
            text = "Restarting"
        prefix = "QUIT :"
        available = IRC_PAYLOAD_BYTES - len(prefix.encode("utf-8"))
        line = f"{prefix}{truncate_utf8(text, available)}"
        try:
            await asyncio.wait_for(self.send_raw(line), timeout=2.0)
        except (ConnectionError, OSError, RuntimeError, TimeoutError, asyncio.CancelledError):
            logger.warning("could not drain IRC QUIT on %s; closing anyway",
                           self.profile.network)

    async def authenticate_sasl_plain(self) -> None:
        username = self.profile.sasl_username
        password = self.profile.sasl_password
        if username is None or password is None:
            raise IRCAuthenticationError("SASL credentials are incomplete")
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
            registered = False
            awaiting_pong = False
            while True:
                timeout = (
                    IRC_PONG_TIMEOUT_SECONDS if awaiting_pong
                    else IRC_IDLE_TIMEOUT_SECONDS if registered
                    else IRC_REGISTRATION_TIMEOUT_SECONDS
                )
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
                except TimeoutError:
                    if not registered:
                        raise ConnectionError("IRC registration timed out") from None
                    if awaiting_pong:
                        raise ConnectionError("IRC keepalive PONG timed out") from None
                    await self.send_raw("PING :bottled-ghosts-keepalive")
                    awaiting_pong = True
                    continue
                if not raw:
                    break
                line = raw.decode(errors="replace").rstrip("\r\n")
                command, params = parse_irc_command(line)
                if command == "CAP":
                    logger.info("IRC capability response: %s", line)
                elif not registered and command.isdigit():
                    logger.info("IRC registration response: %s", line)
                if command == "PING":
                    await self.send_raw(f"PONG {' '.join(params)}")
                    continue
                if command == "PONG":
                    awaiting_pong = False
                    continue
                cap_subcommand = params[1].upper() if command == "CAP" and len(params) > 1 else ""
                if not registered and cap_subcommand == "LS":
                    self.capabilities.update(capability_names(params[-1].split()))
                    if len(params) > 2 and params[2] == "*":
                        continue
                    if self.profile.sasl_username:
                        if "sasl" not in self.capabilities:
                            raise IRCAuthenticationError(
                                "IRC server does not advertise SASL"
                            )
                    if self.profile.sasl_username:
                        logger.info("requesting SASL PLAIN authentication")
                        self.pending_capabilities.add("sasl")
                    if "account-tag" in self.capabilities:
                        self.pending_capabilities.add("account-tag")
                    if self.pending_capabilities:
                        requested = " ".join(sorted(self.pending_capabilities))
                        await self.send_raw(f"CAP REQ :{requested}")
                    else:
                        await self.send_raw("CAP END")
                    await self.send_raw(f"NICK {self.current_nick}")
                    await self.send_raw(
                        f"USER {self.profile.username} 0 * :{self.profile.realname}"
                    )
                    continue
                if not registered and cap_subcommand == "NAK":
                    rejected = capability_names(params[-1].split())
                    self.pending_capabilities.difference_update(rejected)
                    if self.profile.sasl_username and "sasl" in rejected:
                        raise IRCAuthenticationError(
                            "IRC server rejected the SASL capability request"
                        )
                    if not self.pending_capabilities and not self.sasl_authenticating:
                        await self.send_raw("CAP END")
                    continue
                if not registered and cap_subcommand == "ACK":
                    acknowledged = capability_names(params[-1].split())
                    self.pending_capabilities.difference_update(acknowledged)
                    if "sasl" in acknowledged:
                        self.sasl_authenticating = True
                        logger.info("starting SASL PLAIN exchange")
                        await self.send_raw("AUTHENTICATE PLAIN")
                    elif not self.pending_capabilities and not self.sasl_authenticating:
                        await self.send_raw("CAP END")
                    continue
                if command == "AUTHENTICATE" and params == ["+"]:
                    await self.authenticate_sasl_plain()
                    continue
                numeric = command if command.isdigit() else None
                if numeric == "903":
                    logger.info("SASL authentication succeeded")
                    self.sasl_authenticating = False
                    await self.send_raw("CAP END")
                    continue
                if numeric in {"904", "905", "906", "907"}:
                    raise IRCAuthenticationError(
                        f"SASL authentication failed (IRC {numeric})"
                    )
                if numeric == "433" and not registered:
                    self._nick_index += 1
                    if self._nick_index >= len(self._nick_choices):
                        raise IRCNickCollisionError(
                            "all configured IRC nick choices are in use: "
                            + ", ".join(self._nick_choices)
                        )
                    self.current_nick = self._nick_choices[self._nick_index]
                    logger.warning("IRC nick in use; trying %s", self.current_nick)
                    await self.send_raw(f"NICK {self.current_nick}")
                    continue
                if numeric == "474":
                    channel = params[1] if len(params) > 1 else "an IRC channel"
                    logger.warning("banned from %s; leaving this connection up", channel)
                    continue
                if numeric == "001":
                    registered = True
                    if params:
                        self.current_nick = params[0]
                    if self.connection_state_handler is not None:
                        self.connection_state_handler(True)
                    logger.info("IRC registration complete as %s", self.current_nick)
                    if self.profile.user_modes:
                        await self.send_raw(
                            f"MODE {self.current_nick} {self.profile.user_modes}"
                        )
                        logger.info(
                            "setting user modes %s on %s",
                            self.profile.user_modes, self.current_nick,
                        )
                    for channel in self.join_channels:
                        await self.send_raw(f"JOIN {channel}")
                        logger.info("joining %s", channel)
                    continue
                if command == "KICK" and len(params) >= 2:
                    channel, kicked_nick = params[:2]
                    if irc_casefold(kicked_nick) == irc_casefold(self.current_nick):
                        kicker = line[1:].split("!", 1)[0] if line.startswith(":") else "server"
                        event = IRCKickEvent(
                            channel=channel,
                            kicker=kicker,
                            reason=params[2] if len(params) > 2 else "No reason given",
                        )
                        logger.warning("kicked from %s by %s", event.channel, event.kicker)
                        if self.kick_handler is not None:
                            await self.kick_handler(event)
                        raise IRCKickedError(event)
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
            if self.connection_state_handler is not None:
                self.connection_state_handler(False)
            # Best-effort graceful QUIT before closing the socket. This avoids
            # the TLS abrupt-close error that occurs when the connection is
            # torn down without telling the server.
            await self.quit()
            try:
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), timeout=2.0)
            except (OSError, TimeoutError, asyncio.CancelledError):
                pass
            self.writer = None
            logger.info("disconnected from %s", self.profile.network)
