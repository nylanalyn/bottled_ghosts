import pytest

from cellar.models import IRCMessage, IRCProfile, LLMProfile
from cellar.storage import (
    create_bottle,
    load_bottle,
    load_enabled_bottles,
    list_bottles,
    log_message,
    open_database,
    prune_messages,
    recent_messages,
    search_messages,
    search_logs,
    set_llm_api_key,
    set_sasl_credentials,
    set_server_password,
)
from pydantic import ValidationError


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
        assert await set_sasl_credentials(
            db, bottle_id=bottle_id, username="account", password="secret"
        ) is True
        assert await set_sasl_credentials(
            db, bottle_id=bottle_id, username="account", password="secret"
        ) is False
        assert await set_llm_api_key(
            db, bottle_id=bottle_id, api_key="api-secret", actor="test-operator"
        ) is True
        assert await set_llm_api_key(
            db, bottle_id=bottle_id, api_key="api-secret", actor="test-operator"
        ) is False
        await set_server_password(
            db, bottle_id=bottle_id, password="server-secret", actor="test-operator"
        )
        bottle = await load_bottle(db, bottle_id)
        assert bottle.irc.sasl_username == "account"
        assert bottle.irc.sasl_password == "secret"
        assert bottle.irc.password == "server-secret"
        assert bottle.llm.api_key == "api-secret"
        events = await (await db.execute(
            "SELECT changed_fields, old_value, new_value FROM configuration_events ORDER BY id"
        )).fetchall()
        assert [row[0] for row in events] == [
            "created", "sasl_credentials", "api_key", "server_password",
        ]
        assert "secret" not in "".join(
            str(value) for row in events for value in (row["old_value"], row["new_value"])
        )

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
            exclude_message_ids=None,
        ) == [("alice", "the brass telescope is repaired")]
        assert await search_messages(
            db, bot_id=1, network="local", channel="#test", text="telescope",
            exclude_message_ids=[searchable_id],
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
        await db.execute(
            "UPDATE messages SET timestamp = '2000-01-01 00:00:00' WHERE id = ?",
            (searchable_id,),
        )
        await db.commit()
        assert await prune_messages(
            db, older_than_days=30, actor="test-operator",
        ) == 1
        assert await search_logs(db, text="brass telescope") == []
        maintenance = await (await db.execute(
            "SELECT actor, action, details FROM maintenance_events"
        )).fetchone()
        assert maintenance is not None
        assert (maintenance["actor"], maintenance["action"]) == (
            "test-operator", "messages:prune",
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_llm_penalty_defaults_and_round_trip(tmp_path) -> None:
    db = await open_database(tmp_path / "test.db")
    try:
        # Default-config Bottle keeps the wire shape (0.0 penalties).
        default_id = await create_bottle(
            db, name="default", soul_prompt_path=tmp_path / "soul.md",
            irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost/chat", model="m"),
        )
        default = await load_bottle(db, default_id)
        assert default.llm.frequency_penalty == 0.0
        assert default.llm.presence_penalty == 0.0

        # Non-zero penalties persist and reload through both load paths.
        tuned_id = await create_bottle(
            db, name="tuned", soul_prompt_path=tmp_path / "soul.md",
            irc=IRCProfile(network="local", host="irc.example", nick="ghost2",
                           username="ghost2", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(
                endpoint="http://localhost/chat", model="m",
                frequency_penalty=0.6, presence_penalty=0.4,
            ),
            actor="test-operator",
        )
        tuned = await load_bottle(db, tuned_id)
        assert (tuned.llm.frequency_penalty, tuned.llm.presence_penalty) == (0.6, 0.4)
        enabled = await load_enabled_bottles(db)
        tuned_via_enabled = next(b for b in enabled if b.id == tuned_id)
        assert (tuned_via_enabled.llm.frequency_penalty,
                tuned_via_enabled.llm.presence_penalty) == (0.6, 0.4)

        # Audit row carries the new fields.
        row = await (await db.execute(
            "SELECT new_value FROM configuration_events WHERE bot_id = ? AND changed_fields = 'created'",
            (tuned_id,),
        )).fetchone()
        assert row is not None
        import json
        audit = json.loads(row["new_value"])
        assert audit["frequency_penalty"] == 0.6
        assert audit["presence_penalty"] == 0.4
    finally:
        await db.close()


def test_llm_profile_rejects_out_of_range_penalties() -> None:
    with pytest.raises(ValidationError):
        LLMProfile(endpoint="x", model="m", frequency_penalty=2.5)
    with pytest.raises(ValidationError):
        LLMProfile(endpoint="x", model="m", presence_penalty=-2.5)
    # Boundaries are accepted.
    LLMProfile(endpoint="x", model="m", frequency_penalty=2.0, presence_penalty=-2.0)
