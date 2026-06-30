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
        assert await recent_messages(db, bot_id=1, network="local", channel="#test") == [("alice", "hi")]
    finally:
        await db.close()
