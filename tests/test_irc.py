from cellar.irc import parse_privmsg


def test_parse_privmsg() -> None:
    assert parse_privmsg(":alice!u@h PRIVMSG #cellar :hello there") == (
        "alice", "#cellar", "hello there"
    )


def test_ignore_other_commands() -> None:
    assert parse_privmsg(":server 001 ghost :welcome") is None
