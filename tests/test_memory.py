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
from cellar.storage import (
    create_bottle,
    log_message,
    open_database,
    set_memory_extraction,
    store_memory_candidates,
)


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
    finally:
        await db.close()
