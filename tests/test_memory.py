import aiosqlite
import pytest
from pydantic import ValidationError

from cellar.identity import resolve_user
from cellar.memory import extract_candidates
from cellar.migrations import MIGRATIONS
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
    list_all_user_memories,
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


@pytest.mark.asyncio
async def test_extractor_retries_truncated_json_with_larger_budget(monkeypatch) -> None:
    token_budgets: list[int] = []

    async def fake_complete(profile, _messages) -> str:
        token_budgets.append(profile.max_tokens)
        if len(token_budgets) == 1:
            return '{"candidates":[{"text":"unfinished'
        return '{"candidates":[{"text":"Builds weather station","type":"project","confidence":0.8}]}'

    monkeypatch.setattr("cellar.memory.complete", fake_complete)
    candidates = await extract_candidates(
        LLMProfile(endpoint="http://localhost", model="test"),
        speaker="alice", body="I am building a weather station",
    )

    assert token_budgets == [512, 1024]
    assert candidates == [
        ExtractedMemory(
            text="Builds weather station", type="project", confidence=0.8,
        )
    ]


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
            db, bot_id=bottle_id, user_id=user_id,
            source_message_ids=[message_id], candidates=[candidate],
        ) == 1
        assert await store_memory_candidates(
            db, bot_id=bottle_id, user_id=user_id,
            source_message_ids=[message_id], candidates=[candidate],
        ) == 0
        row = await (await db.execute(
            """SELECT bot_id, user_id, source_message_id, memory_type, status
               FROM memory_candidates"""
        )).fetchone()
        assert row is not None
        assert tuple(row) == (
            bottle_id, user_id, message_id, "preference", "pending",
        )

        pending = await list_memory_candidates(db)
        assert len(pending) == 1
        assert (pending[0].bot_id, pending[0].bottle_name) == (bottle_id, "test")
        assert pending[0].source_body == "I love cheese"
        assert [(source.message_id, source.body) for source in pending[0].source_messages] == [
            (message_id, "I love cheese")
        ]
        memory_id = await approve_memory_candidate(
            db, candidate_id=pending[0].id, actor="test-operator"
        )
        assert await approved_memory_texts(
            db, bot_id=bottle_id, user_id=user_id,
        ) == [
            "preference: Likes cheese"
        ]
        await edit_user_memory(
            db, memory_id=memory_id, text="Prefers mature cheese", confidence=0.8,
            actor="test-operator",
        )
        memories = await list_user_memories(
            db, bot_id=bottle_id, user_id=user_id,
        )
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
            db, bot_id=bottle_id, user_id=user_id,
            source_message_ids=[second_message_id], candidates=[temporary],
        )
        rejected = (await list_memory_candidates(db))[0]
        await reject_memory_candidate(db, candidate_id=rejected.id, actor="test-operator")

        third_message_id = await log_message(
            db,
            IRCMessage(network="local", channel="#test", speaker="alice",
                       body="I am busy today", bot_id=bottle_id, user_id=user_id),
        )
        await store_memory_candidates(
            db, bot_id=bottle_id, user_id=user_id,
            source_message_ids=[third_message_id],
            candidates=[ExtractedMemory(
                text="Busy today", type="temporary_state", confidence=0.8,
            )],
        )
        temporary_candidate = (await list_memory_candidates(db))[0]
        temporary_memory_id = await approve_memory_candidate(
            db, candidate_id=temporary_candidate.id, actor="test-operator"
        )
        temporary_memory = next(
            memory for memory in await list_user_memories(
                db, bot_id=bottle_id, user_id=user_id,
            )
            if memory.id == temporary_memory_id
        )
        assert temporary_memory.expires_at is not None
        assert "temporary_state: Busy today" in await approved_memory_texts(
            db, bot_id=bottle_id, user_id=user_id,
        )

        await db.execute(
            "UPDATE user_memories SET expires_at = datetime('now', '-1 second') WHERE id = ?",
            (temporary_memory_id,),
        )
        await db.commit()
        assert "temporary_state: Busy today" not in await approved_memory_texts(
            db, bot_id=bottle_id, user_id=user_id,
        )
        assert temporary_memory_id not in {
            memory.id for memory in await list_all_user_memories(db)
        }
        assert temporary_memory_id in {
            memory.id for memory in await list_all_user_memories(
                db, include_expired=True,
            )
        }
        await edit_user_memory(
            db, memory_id=temporary_memory_id, memory_type="relationship",
            actor="test-operator",
        )
        revived = next(
            memory for memory in await list_user_memories(
                db, bot_id=bottle_id, user_id=user_id,
            )
            if memory.id == temporary_memory_id
        )
        assert revived.expires_at is None
        assert "relationship: Busy today" in await approved_memory_texts(
            db, bot_id=bottle_id, user_id=user_id,
        )

        audit = list(await (await db.execute(
            "SELECT action, actor, new_expires_at FROM audit_events ORDER BY id"
        )).fetchall())
        assert [(row["action"], row["actor"]) for row in audit] == [
            ("approve", "test-operator"),
            ("edit", "test-operator"),
            ("reject", "test-operator"),
            ("approve", "test-operator"),
            ("edit", "test-operator"),
        ]
        assert audit[3]["new_expires_at"] is not None
        assert audit[4]["new_expires_at"] is None
        with pytest.raises(aiosqlite.IntegrityError, match="append-only"):
            await db.execute("DELETE FROM audit_events")
        await db.rollback()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_approved_memories_are_isolated_by_bottle(tmp_path) -> None:
    db = await open_database(tmp_path / "perspectives.db")
    try:
        aria_id = await create_bottle(
            db, name="aria", soul_prompt_path=tmp_path / "aria.md",
            irc=IRCProfile(
                network="local", host="irc.example", nick="aria",
                username="aria", realname="Aria", channels=["#test"],
            ),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        frauderick_id = await create_bottle(
            db, name="frauderick", soul_prompt_path=tmp_path / "frauderick.md",
            irc=IRCProfile(
                network="local", host="irc.example", nick="frauderick",
                username="frauderick", realname="Frauderick", channels=["#test"],
            ),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        user_id = await resolve_user(
            db, network="local",
            identity=IncomingIRCMessage(
                nick="alice", hostmask="u@h", account="alice",
                target="#test", body="roleplay context",
            ),
        )
        aria_message = await log_message(
            db, IRCMessage(
                network="local", channel="#test", speaker="alice",
                body="You are in my squad", bot_id=aria_id, user_id=user_id,
            ),
        )
        frauderick_message = await log_message(
            db, IRCMessage(
                network="local", channel="#test", speaker="alice",
                body="I prefer black coffee", bot_id=frauderick_id, user_id=user_id,
            ),
        )

        await store_memory_candidates(
            db, bot_id=aria_id, user_id=user_id,
            source_message_ids=[aria_message],
            candidates=[ExtractedMemory(
                text="Alice is Aria's commanding officer",
                type="relationship", confidence=0.9,
            )],
        )
        await store_memory_candidates(
            db, bot_id=frauderick_id, user_id=user_id,
            source_message_ids=[frauderick_message],
            candidates=[ExtractedMemory(
                text="Prefers black coffee", type="preference", confidence=0.9,
            )],
        )
        candidates = await list_memory_candidates(db)
        assert [(item.bottle_name, item.candidate_text) for item in candidates] == [
            ("aria", "Alice is Aria's commanding officer"),
            ("frauderick", "Prefers black coffee"),
        ]
        for candidate in candidates:
            await approve_memory_candidate(
                db, candidate_id=candidate.id, actor="test-operator",
            )

        assert await approved_memory_texts(
            db, bot_id=aria_id, user_id=user_id,
        ) == ["relationship: Alice is Aria's commanding officer"]
        assert await approved_memory_texts(
            db, bot_id=frauderick_id, user_id=user_id,
        ) == ["preference: Prefers black coffee"]
        with pytest.raises(ValueError, match="owning Bottle"):
            await store_memory_candidates(
                db, bot_id=frauderick_id, user_id=user_id,
                source_message_ids=[aria_message],
                candidates=[ExtractedMemory(
                    text="Wrong perspective", type="relationship", confidence=0.5,
                )],
            )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_migration_030_backfills_memory_owners_from_provenance(tmp_path) -> None:
    database = tmp_path / "migration-030.db"
    db = await aiosqlite.connect(database)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute(
        """CREATE TABLE schema_migrations (
               version INTEGER PRIMARY KEY,
               applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    for version, migration in enumerate(MIGRATIONS[:-1], start=1):
        await migration(db)
        await db.execute(
            "INSERT INTO schema_migrations(version) VALUES (?)", (version,),
        )
        await db.commit()
    try:
        bottle_id = await create_bottle(
            db, name="aria", soul_prompt_path=tmp_path / "aria.md",
            irc=IRCProfile(
                network="local", host="irc.example", nick="aria",
                username="aria", realname="Aria", channels=["#test"],
            ),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        user_id = await resolve_user(
            db, network="local",
            identity=IncomingIRCMessage(
                nick="alice", hostmask="u@h", account="alice",
                target="#test", body="You are in my squad",
            ),
        )
        message_id = await log_message(
            db, IRCMessage(
                network="local", channel="#test", speaker="alice",
                body="You are in my squad", bot_id=bottle_id, user_id=user_id,
            ),
        )
        candidate = await db.execute(
            """INSERT INTO memory_candidates(
                   user_id, source_message_id, candidate_text, memory_type, confidence
               ) VALUES (?, ?, ?, 'relationship', 0.9)""",
            (user_id, message_id, "Alice is Aria's commanding officer"),
        )
        candidate_id = candidate.lastrowid
        assert candidate_id is not None
        await db.execute(
            """INSERT INTO memory_candidate_sources(candidate_id, message_id, ordinal)
               VALUES (?, ?, 0)""",
            (candidate_id, message_id),
        )
        await db.execute(
            """INSERT INTO user_memories(
                   user_id, source_candidate_id, memory_text, memory_type, confidence
               ) VALUES (?, ?, ?, 'relationship', 0.9)""",
            (user_id, candidate_id, "Alice is Aria's commanding officer"),
        )
        await db.commit()
    finally:
        await db.close()

    migrated = await open_database(database)
    try:
        version = await (await migrated.execute(
            "SELECT MAX(version) FROM schema_migrations"
        )).fetchone()
        candidate_owner = await (await migrated.execute(
            "SELECT bot_id FROM memory_candidates"
        )).fetchone()
        memory_owner = await (await migrated.execute(
            "SELECT bot_id FROM user_memories"
        )).fetchone()
        violations = await (await migrated.execute(
            "PRAGMA foreign_key_check"
        )).fetchall()
        assert version is not None and version[0] == 30
        assert candidate_owner is not None and candidate_owner[0] == bottle_id
        assert memory_owner is not None and memory_owner[0] == bottle_id
        assert violations == []
    finally:
        await migrated.close()
