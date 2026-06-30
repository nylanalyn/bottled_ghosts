import pytest

from cellar.models import IRCMessage, IRCProfile, LLMProfile
from cellar.storage import (
    create_bottle,
    load_bottle,
    load_enabled_bottles,
    list_bottles,
    log_message,
    open_database,
    recent_messages,
    search_messages,
    search_logs,
    set_sasl_credentials,
)


@pytest.mark.asyncio
async def test_migration_configuration_and_logging(tmp_path) -> None:
    db = await open_database(tmp_path / "test.db")
    try:
        bottle_id = await create_bottle(
            db,
            name="test",
            soul_prompt_path=tmp_path / "soul.md",
            irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost/chat", model="test-model"),
        )
        bottle = await load_bottle(db, bottle_id)
        assert bottle.irc.channels == ["#test"]
        summaries = await list_bottles(db)
        assert [(item.id, item.name, item.enabled) for item in summaries] == [(1, "test", True)]
        assert [item.id for item in await load_enabled_bottles(db)] == [bottle_id]
        await set_sasl_credentials(db, bottle_id=bottle_id, username="account", password="secret")
        bottle = await load_bottle(db, bottle_id)
        assert bottle.irc.sasl_username == "account"
        assert bottle.irc.sasl_password == "secret"

        message = IRCMessage(network="local", channel="#test", speaker="alice", body="hi", bot_id=1)
        await log_message(db, message)
        searchable_id = await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="alice",
                           body="the brass telescope is repaired", bot_id=1)
        )
        assert await recent_messages(db, bot_id=1, network="local", channel="#test") == [
            ("alice", "hi"),
            ("alice", "the brass telescope is repaired"),
        ]
        assert await search_messages(
            db, bot_id=1, network="local", channel="#test", text="telescope status",
            exclude_message_id=None,
        ) == [("alice", "the brass telescope is repaired")]
        assert await search_messages(
            db, bot_id=1, network="local", channel="#test", text="telescope",
            exclude_message_id=searchable_id,
        ) == []
        results = await search_logs(db, text="brass telescope", bot_id=bottle_id)
        assert [(result.id, result.speaker) for result in results] == [
            (searchable_id, "alice")
        ]
    finally:
        await db.close()
