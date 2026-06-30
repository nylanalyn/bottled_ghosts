import base64

from cellar.irc import parse_privmsg, sasl_plain_chunks


def test_parse_privmsg() -> None:
    assert parse_privmsg(":alice!u@h PRIVMSG #cellar :hello there") == (
        "alice", "#cellar", "hello there"
    )


def test_ignore_other_commands() -> None:
    assert parse_privmsg(":server 001 ghost :welcome") is None


def test_sasl_plain_payload() -> None:
    expected = base64.b64encode(b"ghost\0ghost\0secret").decode()
    assert sasl_plain_chunks("ghost", "secret") == [expected]
