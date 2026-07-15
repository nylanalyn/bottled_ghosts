import base64

import pytest
from pydantic import ValidationError

from cellar.irc import (
    IRCClient,
    IRCKickEvent,
    IRCJoinEvent,
    IRCKickedError,
    capability_names,
    irc_casefold,
    mentions_nick,
    parse_irc_command,
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


def test_parse_privmsg_strips_formatting_and_ignores_non_action_ctcp() -> None:
    message = parse_privmsg(":alice!u@h PRIVMSG #cellar :\x02bold\x0f \x0312blue")
    assert message is not None
    assert message.body == "bold blue"
    assert parse_privmsg(":alice!u@h PRIVMSG ghost :\x01VERSION\x01") is None


def test_parse_privmsg_accepts_ctcp_action() -> None:
    message = parse_privmsg(
        ":alice!u@h PRIVMSG #cellar :\x01ACTION pokes frauderick\x01"
    )

    assert message is not None
    assert message.body == "/me pokes frauderick"


def test_ignore_other_commands() -> None:
    assert parse_privmsg(":server 001 ghost :welcome") is None
    assert parse_privmsg("@malformed-tag-only") is None


def test_parse_irc_command_accepts_optional_tags_and_prefix() -> None:
    assert parse_irc_command(":server AUTHENTICATE +") == ("AUTHENTICATE", ["+"])
    assert parse_irc_command("@time=now :server 903 ghost :success") == (
        "903", ["ghost", "success"]
    )
    assert parse_irc_command("AUTHENTICATE :+") == ("AUTHENTICATE", ["+"])
    assert parse_irc_command("PING :registration-token") == (
        "PING", ["registration-token"]
    )


def test_capability_names_strip_values_and_modifiers() -> None:
    assert capability_names([":sasl=PLAIN,EXTERNAL", "~account-tag", "-echo-message"]) == {
        "sasl", "account-tag", "echo-message",
    }


def test_sasl_plain_payload() -> None:
    expected = base64.b64encode(b"\0ghost\0secret").decode()
    assert sasl_plain_chunks("ghost", "secret") == [expected]


def test_rfc1459_nickname_matching_requires_boundaries() -> None:
    assert irc_casefold("[Ghost]\\^") == "{ghost}|~"
    assert mentions_nick("ghost: are you there?", "Ghost")
    assert mentions_nick("hello {ghost}", "[Ghost]")
    assert not mentions_nick("ghostwriter", "ghost")


def test_utf8_truncation_preserves_complete_characters() -> None:
    assert truncate_utf8("ééé", 5) == "éé"


def test_user_modes_reject_protocol_injection() -> None:
    with pytest.raises(ValidationError, match="user modes"):
        IRCProfile(
            network="test", host="localhost", nick="ghost", username="ghost",
            realname="Ghost", channels=["#test"], user_modes="+B JOIN #other",
        )


def test_quit_message_rejects_protocol_injection() -> None:
    with pytest.raises(ValidationError, match="single line"):
        IRCProfile(
            network="test", host="localhost", nick="ghost", username="ghost",
            realname="Ghost", channels=["#test"], quit_message="bye\r\nJOIN #other",
        )


@pytest.mark.asyncio
async def test_capabilities_are_requested_together(monkeypatch) -> None:
    class Reader:
        def __init__(self) -> None:
            self.lines = iter([
                b":server CAP ghost LS :sasl account-tag\r\n",
                b":server CAP ghost ACK :account-tag sasl=PLAIN\r\n",
                b"AUTHENTICATE :+\r\n",
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

    assert "CAP REQ :account-tag sasl" in writer.lines
    assert writer.lines.index("CAP REQ :account-tag sasl") < writer.lines.index("NICK ghost")
    assert writer.lines.index("CAP REQ :account-tag sasl") < writer.lines.index(
        "USER ghost 0 * :Ghost"
    )
    assert writer.lines.count("CAP END") == 1


@pytest.mark.asyncio
async def test_privmsg_cap_words_do_not_restart_negotiation(monkeypatch) -> None:
    seen = []

    class Reader:
        def __init__(self) -> None:
            self.lines = iter([
                b":server 001 ghost :welcome\r\n",
                b":alice!u@h PRIVMSG #test :yeah the CAP LS thing is weird\r\n",
                b"",
            ])

        async def readline(self) -> bytes:
            return next(self.lines)

    class Writer:
        def __init__(self) -> None:
            self.lines = []
        def write(self, data: bytes) -> None:
            self.lines.append(data.decode().rstrip())
        async def drain(self) -> None: return None
        def close(self) -> None: return None
        async def wait_closed(self) -> None: return None

    reader, writer = Reader(), Writer()
    async def open_connection(*_args, **_kwargs): return reader, writer
    async def handler(message) -> None: seen.append(message.body)
    monkeypatch.setattr("cellar.irc.asyncio.open_connection", open_connection)
    client = IRCClient(IRCProfile(network="test", host="localhost", tls=False,
        nick="ghost", username="ghost", realname="Ghost", channels=["#test"]), handler)
    with pytest.raises(ConnectionError):
        await client.run()
    assert seen == ["yeah the CAP LS thing is weird"]
    assert writer.lines.count("CAP LS 302") == 1
    assert not any(line.startswith("NICK ") for line in writer.lines)


@pytest.mark.asyncio
async def test_nick_collision_uses_configured_alternate(monkeypatch) -> None:
    class Reader:
        def __init__(self) -> None:
            self.lines = iter([
                b":server CAP * LS :account-tag\r\n",
                b":server 433 * ghost :Nickname is already in use\r\n",
                b":server 001 ghost_ :welcome\r\n",
                b"",
            ])
        async def readline(self) -> bytes: return next(self.lines)
    class Writer:
        def __init__(self) -> None: self.lines = []
        def write(self, data: bytes) -> None: self.lines.append(data.decode().rstrip())
        async def drain(self) -> None: return None
        def close(self) -> None: return None
        async def wait_closed(self) -> None: return None
    reader, writer = Reader(), Writer()
    async def open_connection(*_args, **_kwargs): return reader, writer
    async def handler(_message) -> None: return None
    monkeypatch.setattr("cellar.irc.asyncio.open_connection", open_connection)
    client = IRCClient(IRCProfile(network="test", host="localhost", tls=False,
        nick="ghost", alternate_nicks=["ghost_"], username="ghost",
        realname="Ghost", channels=["#test"]), handler)
    with pytest.raises(ConnectionError):
        await client.run()
    assert "NICK ghost" in writer.lines
    assert "NICK ghost_" in writer.lines
    assert client.current_nick == "ghost_"


@pytest.mark.asyncio
async def test_registered_idle_connection_requires_keepalive_pong(monkeypatch) -> None:
    class Reader:
        async def readline(self) -> bytes:
            return b":server 001 ghost :welcome\r\n"
    class Writer:
        def __init__(self) -> None: self.lines = []
        def write(self, data: bytes) -> None: self.lines.append(data.decode().rstrip())
        async def drain(self) -> None: return None
        def close(self) -> None: return None
        async def wait_closed(self) -> None: return None
    reader, writer = Reader(), Writer()
    calls = 0
    real_wait_for = __import__("asyncio").wait_for
    async def wait_for(awaitable, *, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await real_wait_for(awaitable, timeout=timeout)
        awaitable.close()
        raise TimeoutError
    async def open_connection(*_args, **_kwargs): return reader, writer
    async def handler(_message) -> None: return None
    monkeypatch.setattr("cellar.irc.asyncio.open_connection", open_connection)
    monkeypatch.setattr("cellar.irc.asyncio.wait_for", wait_for)
    client = IRCClient(IRCProfile(network="test", host="localhost", tls=False,
        nick="ghost", username="ghost", realname="Ghost", channels=["#test"]), handler)
    with pytest.raises(ConnectionError, match="PONG timed out"):
        await client.run()
    assert "PING :bottled-ghosts-keepalive" in writer.lines


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


@pytest.mark.asyncio
async def test_user_modes_are_set_before_channel_join(monkeypatch) -> None:
    class Reader:
        def __init__(self) -> None:
            self.lines = iter([b":server 001 ghost :welcome\r\n", b""])

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
            username="ghost", realname="Ghost", channels=["#one", "#two"],
            user_modes="+B",
        ),
        handler,
    )
    with pytest.raises(ConnectionError):
        await client.run()

    assert writer.lines.index("MODE ghost +B") < writer.lines.index("JOIN #one")
    assert writer.lines.index("MODE ghost +B") < writer.lines.index("JOIN #two")



@pytest.mark.asyncio
async def test_quit_sends_quit_message_before_closing() -> None:
    """quit() writes the profile's quit_message as an IRC QUIT line."""
    class Writer:
        def __init__(self) -> None:
            self.lines: list[str] = []
        def write(self, data: bytes) -> None:
            self.lines.append(data.decode().rstrip())
        async def drain(self) -> None:
            return None
    async def handler(_message) -> None:
        return None

    client = IRCClient(
        IRCProfile(network="test", host="localhost", tls=False, nick="ghost",
                   username="ghost", realname="Ghost", channels=["#test"],
                   quit_message="going down for maintenance"),
        handler,
    )
    writer = Writer()
    client.writer = writer  # type: ignore[assignment]
    await client.quit()
    assert writer.lines == ["QUIT :going down for maintenance"]


@pytest.mark.asyncio
async def test_quit_with_explicit_message_overrides_profile_default() -> None:
    class Writer:
        def __init__(self) -> None:
            self.lines: list[str] = []
        def write(self, data: bytes) -> None:
            self.lines.append(data.decode().rstrip())
        async def drain(self) -> None:
            return None
    async def handler(_message) -> None:
        return None

    client = IRCClient(
        IRCProfile(network="test", host="localhost", tls=False, nick="ghost",
                   username="ghost", realname="Ghost", channels=["#test"],
                   quit_message="default message"),
        handler,
    )
    writer = Writer()
    client.writer = writer  # type: ignore[assignment]
    await client.quit("custom goodbye")
    assert writer.lines == ["QUIT :custom goodbye"]


@pytest.mark.asyncio
async def test_quit_collapses_line_breaks_in_explicit_message() -> None:
    class Writer:
        def __init__(self) -> None:
            self.lines: list[str] = []
        def write(self, data: bytes) -> None:
            self.lines.append(data.decode().rstrip("\r\n"))
        async def drain(self) -> None:
            return None
    async def handler(_message) -> None:
        return None

    client = IRCClient(
        IRCProfile(network="test", host="localhost", tls=False, nick="ghost",
                   username="ghost", realname="Ghost", channels=["#test"],
                   quit_message="default message"),
        handler,
    )
    writer = Writer()
    client.writer = writer  # type: ignore[assignment]
    await client.quit("custom\r\nJOIN #other")
    assert writer.lines == ["QUIT :custom JOIN #other"]


@pytest.mark.asyncio
async def test_quit_does_nothing_when_not_connected() -> None:
    async def handler(_message) -> None:
        return None
    client = IRCClient(
        IRCProfile(network="test", host="localhost", tls=False, nick="ghost",
                   username="ghost", realname="Ghost", channels=["#test"]),
        handler,
    )
    # writer is None; quit should return without error
    await client.quit()


@pytest.mark.asyncio
async def test_run_sends_quit_on_server_disconnect(monkeypatch) -> None:
    """The run() finally block sends QUIT even when the server closes the connection."""
    class Reader:
        def __init__(self) -> None:
            self.lines = iter([
                b":server 001 ghost :welcome\r\n",
                b"",  # server closed connection
            ])
        async def readline(self) -> bytes:
            return next(self.lines)
    class Writer:
        def __init__(self) -> None:
            self.lines: list[str] = []
        def write(self, data: bytes) -> None:
            self.lines.append(data.decode().rstrip())
        async def drain(self) -> None:
            return None
        def close(self) -> None:
            return None
        async def wait_closed(self) -> None:
            return None
    reader, writer = Reader(), Writer()
    async def open_connection(*_args, **_kwargs):
        return reader, writer
    async def handler(_message) -> None:
        return None
    monkeypatch.setattr("cellar.irc.asyncio.open_connection", open_connection)
    client = IRCClient(
        IRCProfile(network="test", host="localhost", tls=False, nick="ghost",
                   username="ghost", realname="Ghost", channels=["#test"],
                   quit_message="goodbye from test"),
        handler,
    )
    with pytest.raises(ConnectionError):
        await client.run()
    # QUIT was sent in the finally block before close
    assert "QUIT :goodbye from test" in writer.lines


@pytest.mark.asyncio
async def test_kick_notifies_runtime_and_requests_delayed_reconnect(monkeypatch) -> None:
    class Reader:
        def __init__(self) -> None:
            self.lines = iter([
                b":server 001 ghost :welcome\r\n",
                b":operator!op@example KICK #test ghost :too rude\r\n",
            ])

        async def readline(self) -> bytes:
            return next(self.lines)

    class Writer:
        def write(self, _data: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    events: list[IRCKickEvent] = []

    async def open_connection(*_args, **_kwargs):
        return Reader(), Writer()

    async def handler(_message) -> None:
        return None

    async def kick_handler(event: IRCKickEvent) -> None:
        events.append(event)

    monkeypatch.setattr("cellar.irc.asyncio.open_connection", open_connection)
    client = IRCClient(
        IRCProfile(network="test", host="localhost", tls=False, nick="ghost",
                   username="ghost", realname="Ghost", channels=["#test"]),
        handler, kick_handler=kick_handler,
    )
    with pytest.raises(IRCKickedError, match="kicked from #test"):
        await client.run()

    assert events == [IRCKickEvent("#test", "operator", "too rude")]


@pytest.mark.asyncio
async def test_self_join_notifies_runtime(monkeypatch) -> None:
    class Reader:
        def __init__(self) -> None:
            self.lines = iter([
                b":server 001 ghost :welcome\r\n",
                b":ghost!ghost@example JOIN #test\r\n",
                b"",
            ])

        async def readline(self) -> bytes:
            return next(self.lines)

    class Writer:
        def write(self, _data: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    events: list[IRCJoinEvent] = []

    async def open_connection(*_args, **_kwargs):
        return Reader(), Writer()

    async def handler(_message) -> None:
        return None

    async def join_handler(event: IRCJoinEvent) -> None:
        events.append(event)

    monkeypatch.setattr("cellar.irc.asyncio.open_connection", open_connection)
    client = IRCClient(
        IRCProfile(network="test", host="localhost", tls=False, nick="ghost",
                   username="ghost", realname="Ghost", channels=["#test"]),
        handler, join_handler=join_handler,
    )
    with pytest.raises(ConnectionError):
        await client.run()
    assert events == [IRCJoinEvent("#test")]
