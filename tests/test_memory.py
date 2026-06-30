import aiosqlite
import pytest
from pydantic import ValidationError

from cellar.identity import resolve_user
from cellar.memory import extract_candidates
from cellar.models import (
    ExtractedMemory,
    IRCMessage,
    IRCProfile,
    IncomingIRCMessage,
    LLMProfile,
)
from cellar.memory_store import (
    approve_memory_candidate,
    approved_memory_texts,
    edit_user_memory,
    list_memory_candidates,
    list_user_memories,
    reject_memory_candidate,
    store_memory_candidates,
)
from cellar.storage import create_bottle, log_message, open_database, set_memory_extraction


@pytest.mark.asyncio
async def test_extractor_accepts_strict_categorized_json(monkeypatch) -> None:
    async def fake_complete(_profile, _messages) -> str:
        return '```json\n{"candidates":[{"text":"Likes cheese","type":"preference","confidence":0.9}]}\n```'

    monkeypatch.setattr("cellar.memory.complete", fake_complete)
    candidates = await extract_candidates(
        LLMProfile(endpoint="http://localhost", model="test"),
        speaker="alice",
        body="I love cheese",
    )
    assert candidates == [ExtractedMemory(text="Likes cheese", type="preference", confidence=0.9)]


def test_extractor_model_rejects_unknown_memory_category() -> None:
    with pytest.raises(ValidationError):
        ExtractedMemory(text="Sensitive guess", type="medical", confidence=0.5)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pending_candidate_keeps_source_and_deduplicates(tmp_path) -> None:
    db = await open_database(tmp_path / "sediment.db")
    try:
        bottle_id = await create_bottle(
            db,
            name="test",
            soul_prompt_path=tmp_path / "soul.md",
            irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_memory_extraction(db, bottle_id=bottle_id, enabled=True)
        user_id = await resolve_user(
            db,
            network="local",
            identity=IncomingIRCMessage(nick="alice", hostmask="u@h", account=None,
                                        target="#test", body="I love cheese"),
        )
        message_id = await log_message(
            db,
            IRCMessage(network="local", channel="#test", speaker="alice",
                       body="I love cheese", bot_id=bottle_id, user_id=user_id),
        )
        candidate = ExtractedMemory(text="Likes cheese", type="preference", confidence=0.9)
        assert await store_memory_candidates(
            db, user_id=user_id, source_message_id=message_id, candidates=[candidate]
        ) == 1
        assert await store_memory_candidates(
            db, user_id=user_id, source_message_id=message_id, candidates=[candidate]
        ) == 0
        row = await (await db.execute(
            """SELECT user_id, source_message_id, memory_type, status
               FROM memory_candidates"""
        )).fetchone()
        assert tuple(row) == (user_id, message_id, "preference", "pending")

        pending = await list_memory_candidates(db)
        assert len(pending) == 1
        assert pending[0].source_body == "I love cheese"
        memory_id = await approve_memory_candidate(
            db, candidate_id=pending[0].id, actor="test-operator"
        )
        assert await approved_memory_texts(db, user_id=user_id) == [
            "preference: Likes cheese"
        ]
        await edit_user_memory(
            db, memory_id=memory_id, text="Prefers mature cheese", confidence=0.8,
            actor="test-operator",
        )
        memories = await list_user_memories(db, user_id=user_id)
        assert (memories[0].memory_text, memories[0].confidence) == (
            "Prefers mature cheese", 0.8,
        )

        second_message_id = await log_message(
            db,
            IRCMessage(network="local", channel="#test", speaker="alice",
                       body="I am tired today", bot_id=bottle_id, user_id=user_id),
        )
        temporary = ExtractedMemory(
            text="Tired today", type="temporary_state", confidence=0.7
        )
        await store_memory_candidates(
            db, user_id=user_id, source_message_id=second_message_id, candidates=[temporary]
        )
        rejected = (await list_memory_candidates(db))[0]
        await reject_memory_candidate(db, candidate_id=rejected.id, actor="test-operator")

        audit = await (await db.execute(
            "SELECT action, actor FROM audit_events ORDER BY id"
        )).fetchall()
        assert [tuple(row) for row in audit] == [
            ("approve", "test-operator"),
            ("edit", "test-operator"),
            ("reject", "test-operator"),
        ]
        with pytest.raises(aiosqlite.IntegrityError, match="append-only"):
            await db.execute("DELETE FROM audit_events")
        await db.rollback()
    finally:
        await db.close()
