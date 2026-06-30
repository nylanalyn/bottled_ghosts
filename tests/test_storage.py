import json

import pytest

from cellar.models import IRCMessage
from cellar.storage import load_bottle, log_message, open_database, recent_messages


@pytest.mark.asyncio
async def test_migration_configuration_and_logging(tmp_path) -> None:
    db = await open_database(tmp_path / "test.db")
    try:
        await db.execute(
            "INSERT INTO irc_profiles(network, host, nick, username, realname, channels) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("local", "irc.example", "ghost", "ghost", "Ghost", json.dumps(["#test"])),
        )
        await db.execute(
            "INSERT INTO llm_profiles(endpoint, model) VALUES (?, ?)",
            ("http://localhost/chat", "test-model"),
        )
        await db.execute(
            "INSERT INTO bots(name, soul_prompt_path, llm_profile_id, irc_profile_id) "
            "VALUES (?, ?, 1, 1)", ("test", "soul.md"),
        )
        await db.commit()
        bottle = await load_bottle(db, 1)
        assert bottle.irc.channels == ["#test"]

        message = IRCMessage(network="local", channel="#test", speaker="alice", body="hi", bot_id=1)
        await log_message(db, message)
        assert await recent_messages(db, bot_id=1, network="local", channel="#test") == [("alice", "hi")]
    finally:
        await db.close()
