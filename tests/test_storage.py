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
    set_llm_api_key,
    set_sasl_credentials,
    set_server_password,
)


@pytest.mark.asyncio
async def test_migration_configuration_and_logging(tmp_path) -> None:
    db = await open_database(tmp_path / "test.db")
    try:
        journal_mode = await (await db.execute("PRAGMA journal_mode")).fetchone()
        busy_timeout = await (await db.execute("PRAGMA busy_timeout")).fetchone()
        assert journal_mode is not None and journal_mode[0] == "wal"
        assert busy_timeout is not None and busy_timeout[0] == 5000
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
        await set_llm_api_key(
            db, bottle_id=bottle_id, api_key="api-secret", actor="test-operator"
        )
        await set_server_password(
            db, bottle_id=bottle_id, password="server-secret", actor="test-operator"
        )
        bottle = await load_bottle(db, bottle_id)
        assert bottle.irc.sasl_username == "account"
        assert bottle.irc.sasl_password == "secret"
        assert bottle.irc.password == "server-secret"
        assert bottle.llm.api_key == "api-secret"
        events = await (await db.execute(
            "SELECT changed_fields FROM configuration_events ORDER BY id"
        )).fetchall()
        assert [row[0] for row in events] == ["api_key", "server_password"]

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
        hyphenated_id = await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="alice",
                           body="this is well-known behavior", bot_id=1)
        )
        assert [result.id for result in await search_logs(db, text="well-known")] == [
            hyphenated_id
        ]
        results = await search_logs(db, text="brass telescope", bot_id=bottle_id)
        assert [(result.id, result.speaker) for result in results] == [
            (searchable_id, "alice")
        ]
    finally:
        await db.close()
