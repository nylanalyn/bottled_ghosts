import pytest

from cellar.models import IRCMessage, IRCProfile, LLMProfile
from cellar.recollections import (
    archive_recollection, list_recollections, recollect, relevant_recollections,
    recollection_sources,
)
from cellar.storage import (
    create_bottle, load_bottle, log_message, open_database, prune_messages,
    set_memory_extraction, set_recollections_enabled,
)


@pytest.mark.asyncio
async def test_recollections_are_scoped_restart_safe_and_audited(tmp_path, monkeypatch) -> None:
    db = await open_database(tmp_path / "recollections.db")
    try:
        bottle_id = await create_bottle(
            db, name="ghost", soul_prompt_path=tmp_path / "soul.md",
            irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#one", "#two"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await db.execute("INSERT INTO users(id, canonical_name) VALUES ('alice', 'alice')")
        await log_message(
            db, IRCMessage(network="local", channel="#one", speaker="alice",
                           body="Old conversation", bot_id=bottle_id, user_id="alice"),
        )
        await set_memory_extraction(db, bottle_id=bottle_id, enabled=True)
        assert await set_recollections_enabled(db, bottle_id=bottle_id, enabled=True)
        flags = await (await db.execute(
            "SELECT extract_memories, recollections_enabled FROM bots WHERE id = ?",
            (bottle_id,),
        )).fetchone()
        assert tuple(flags) == (0, 1)
        with pytest.raises(ValueError, match="disable recollections"):
            await set_memory_extraction(db, bottle_id=bottle_id, enabled=True)

        message_id = await log_message(
            db, IRCMessage(network="local", channel="#one", speaker="alice",
                           body="The telescope is repaired", bot_id=bottle_id,
                           user_id="alice"),
        )
        await db.execute(
            "UPDATE messages SET timestamp = '2020-01-01 00:00:00' WHERE id = ?",
            (message_id,),
        )
        await db.commit()

        calls = 0

        async def fake_complete(_profile, _messages) -> str:
            nonlocal calls
            calls += 1
            return '{"summary":"Alice said the telescope was repaired."}'

        monkeypatch.setattr("cellar.recollections.complete", fake_complete)
        bottle = await load_bottle(db, bottle_id)
        assert await recollect(db, bottle=bottle) == 1
        assert await recollect(db, bottle=bottle) == 0
        assert calls == 1
        assert len(await list_recollections(db, bot_id=bottle_id)) == 1
        assert len(await recollection_sources(db, recollection_id=1)) == 1
        assert await relevant_recollections(
            db, bot_id=bottle_id, network="local", channel="#two",
            query_text="telescope",
        ) == []
        assert await relevant_recollections(
            db, bot_id=bottle_id, network="local", channel="#one",
            query_text="telescope",
        ) == ["2020-01-01 00:00:00: Alice said the telescope was repaired."]
        assert await prune_messages(db, older_than_days=1) == 0
        await archive_recollection(db, recollection_id=1, actor="tester")
        assert await relevant_recollections(
            db, bot_id=bottle_id, network="local", channel="#one",
            query_text="telescope",
        ) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_empty_recollection_advances_cursor(tmp_path, monkeypatch) -> None:
    db = await open_database(tmp_path / "empty.db")
    try:
        bottle_id = await create_bottle(
            db, name="ghost", soul_prompt_path=tmp_path / "soul.md",
            irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#one"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_recollections_enabled(db, bottle_id=bottle_id, enabled=True)
        await db.execute("INSERT INTO users(id, canonical_name) VALUES ('alice', 'alice')")
        message_id = await log_message(
            db, IRCMessage(network="local", channel="#one", speaker="alice",
                           body="hi", bot_id=bottle_id, user_id="alice"),
        )
        await db.execute(
            "UPDATE messages SET timestamp = '2020-01-01 00:00:00' WHERE id = ?",
            (message_id,),
        )
        await db.commit()

        async def fake_complete(_profile, _messages) -> str:
            return '{"summary":null}'

        monkeypatch.setattr("cellar.recollections.complete", fake_complete)
        bottle = await load_bottle(db, bottle_id)
        assert await recollect(db, bottle=bottle) == 1
        assert await recollect(db, bottle=bottle) == 0
        assert await list_recollections(db, bot_id=bottle_id) == []
    finally:
        await db.close()
