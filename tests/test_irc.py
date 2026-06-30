import base64

import pytest

from cellar.irc import (
    IRCClient,
    irc_casefold,
    mentions_nick,
    parse_privmsg,
    sasl_plain_chunks,
    truncate_utf8,
)
from cellar.models import IRCProfile


def test_parse_privmsg() -> None:
    message = parse_privmsg(":alice!u@h PRIVMSG #cellar :hello there")
    assert message is not None
    assert (message.nick, message.hostmask, message.target, message.body) == (
        "alice", "u@h", "#cellar", "hello there"
    )
    assert message.account is None


def test_parse_privmsg_with_account_tag() -> None:
    message = parse_privmsg("@account=alice;time=now :newnick!u@h PRIVMSG #cellar :hello")
    assert message is not None
    assert message.nick == "newnick"
    assert message.account == "alice"


def test_ignore_other_commands() -> None:
    assert parse_privmsg(":server 001 ghost :welcome") is None
    assert parse_privmsg("@malformed-tag-only") is None


def test_sasl_plain_payload() -> None:
    expected = base64.b64encode(b"ghost\0ghost\0secret").decode()
    assert sasl_plain_chunks("ghost", "secret") == [expected]


def test_rfc1459_nickname_matching_requires_boundaries() -> None:
    assert irc_casefold("[Ghost]\\^") == "{ghost}|~"
    assert mentions_nick("ghost: are you there?", "Ghost")
    assert mentions_nick("hello {ghost}", "[Ghost]")
    assert not mentions_nick("ghostwriter", "ghost")


def test_utf8_truncation_preserves_complete_characters() -> None:
    assert truncate_utf8("ééé", 5) == "éé"


@pytest.mark.asyncio
async def test_capabilities_are_requested_separately(monkeypatch) -> None:
    class Reader:
        def __init__(self) -> None:
            self.lines = iter([
                b":server CAP ghost LS :sasl account-tag\r\n",
                b":server CAP ghost ACK :account-tag\r\n",
                b":server CAP ghost ACK :sasl\r\n",
                b"AUTHENTICATE +\r\n",
                b":server 903 ghost :SASL successful\r\n",
                b"",
            ])

        async def readline(self) -> bytes:
            return next(self.lines)

    class Writer:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def write(self, data: bytes) -> None:
            self.lines.append(data.decode().rstrip("\r\n"))

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    reader = Reader()
    writer = Writer()

    async def open_connection(*_args, **_kwargs):
        return reader, writer

    async def handler(_message) -> None:
        return None

    monkeypatch.setattr("cellar.irc.asyncio.open_connection", open_connection)
    client = IRCClient(
        IRCProfile(
            network="test", host="localhost", tls=False, nick="ghost",
            username="ghost", realname="Ghost", channels=["#test"],
            sasl_username="ghost", sasl_password="secret",
        ),
        handler,
    )
    with pytest.raises(ConnectionError):
        await client.run()

    assert "CAP REQ :sasl" in writer.lines
    assert "CAP REQ :account-tag" in writer.lines
    assert "CAP REQ :sasl account-tag" not in writer.lines
    assert writer.lines.count("CAP END") == 1


@pytest.mark.asyncio
async def test_send_message_enforces_irc_byte_limit() -> None:
    class Writer:
        def __init__(self) -> None:
            self.data = b""

        def write(self, data: bytes) -> None:
            self.data = data

        async def drain(self) -> None:
            return None

    async def handler(_message) -> None:
        return None

    client = IRCClient(
        IRCProfile(network="test", host="localhost", tls=False, nick="ghost",
                   username="ghost", realname="Ghost", channels=["#test"]),
        handler,
    )
    writer = Writer()
    client.writer = writer  # type: ignore[assignment]
    await client.send_message("#test", "é" * 400)

    assert len(writer.data) <= 512
    assert len(writer.data[:-2]) <= 510
    assert writer.data.endswith(b"\r\n")
    writer.data[:-2].decode("utf-8")
