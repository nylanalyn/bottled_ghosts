import base64

from cellar.irc import parse_privmsg, sasl_plain_chunks


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


def test_sasl_plain_payload() -> None:
    expected = base64.b64encode(b"ghost\0ghost\0secret").decode()
    assert sasl_plain_chunks("ghost", "secret") == [expected]
