import pytest

from cellar.identity import resolve_user
from cellar.memory_consolidation import (
    accept_consolidation_proposal,
    create_consolidation_proposal,
    get_consolidation_proposal,
    reject_consolidation_proposal,
    scan_consolidation_proposals,
)
from cellar.memory_store import (
    approve_memory_candidate,
    approved_memory_texts,
    attach_memory_candidate,
    list_memory_evidence,
    list_user_memories,
    merge_user_memories,
    store_memory_candidates,
)
from cellar.models import (
    ExtractedMemory,
    IRCMessage,
    IRCProfile,
    IncomingIRCMessage,
    LLMProfile,
)
from cellar.storage import create_bottle, load_bottle, log_message, open_database


async def _scope(db, tmp_path, *, name: str = "aria") -> tuple[int, str]:
    bot_id = await create_bottle(
        db, name=name, soul_prompt_path=tmp_path / f"{name}.md",
        irc=IRCProfile(
            network="local", host="irc.example", nick=name,
            username=name, realname=name.title(), channels=["#test"],
        ),
        llm=LLMProfile(endpoint="http://localhost", model="test"),
    )
    user_id = await resolve_user(
        db, network="local",
        identity=IncomingIRCMessage(
            nick="Mikoolo", hostmask="u@h", account="mikoolo",
            target="#test", body="hello",
        ),
    )
    return bot_id, user_id


async def _candidate(
    db, *, bot_id: int, user_id: str, text: str,
    memory_type: str = "relationship",
) -> int:
    message_id = await log_message(
        db, IRCMessage(
            network="local", channel="#test", speaker="Mikoolo",
            body=text, bot_id=bot_id, user_id=user_id,
        ),
    )
    await store_memory_candidates(
        db, bot_id=bot_id, user_id=user_id, source_message_ids=[message_id],
        candidates=[ExtractedMemory(
            text=text, type=memory_type, confidence=0.9,  # type: ignore[arg-type]
        )],
    )
    row = await (await db.execute(
        "SELECT MAX(id) FROM memory_candidates"
    )).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
async def test_exact_repetition_becomes_more_evidence_not_another_memory(
    tmp_path,
) -> None:
    db = await open_database(tmp_path / "exact.db")
    try:
        bot_id, user_id = await _scope(db, tmp_path)
        first = await _candidate(
            db, bot_id=bot_id, user_id=user_id,
            text="Aria is quartermaster of the Bullshittery Platoon.",
        )
        memory_id = await approve_memory_candidate(
            db, candidate_id=first, actor="tester",
        )
        repeated = await _candidate(
            db, bot_id=bot_id, user_id=user_id,
            text="  ARIA is quartermaster of the Bullshittery Platoon! ",
        )
        assert await approve_memory_candidate(
            db, candidate_id=repeated, actor="tester",
        ) == memory_id

        memories = await list_user_memories(
            db, bot_id=bot_id, user_id=user_id,
        )
        evidence = await list_memory_evidence(db, memory_id=memory_id)
        assert [memory.id for memory in memories] == [memory_id]
        assert [item.candidate_id for item in evidence] == [first, repeated]
        actions = await (await db.execute(
            "SELECT action FROM audit_events ORDER BY id"
        )).fetchall()
        assert [row[0] for row in actions] == ["approve", "attach"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_manual_merge_preserves_evidence_and_archives_sources(tmp_path) -> None:
    db = await open_database(tmp_path / "merge.db")
    try:
        bot_id, user_id = await _scope(db, tmp_path)
        ids = []
        for text in (
            "Aria serves as Mikoolo's quartermaster",
            "Mikoolo appointed Aria quartermaster of his platoon",
        ):
            candidate_id = await _candidate(
                db, bot_id=bot_id, user_id=user_id, text=text,
            )
            ids.append(await approve_memory_candidate(
                db, candidate_id=candidate_id, actor="tester",
            ))

        await merge_user_memories(
            db, target_memory_id=ids[0], merged_memory_ids=[ids[1]],
            text="Aria is Mikoolo's platoon quartermaster", actor="tester",
        )
        active = await list_user_memories(db, bot_id=bot_id, user_id=user_id)
        all_rows = await list_user_memories(
            db, bot_id=bot_id, user_id=user_id, include_merged=True,
        )
        evidence = await list_memory_evidence(db, memory_id=ids[0])
        assert [(item.id, item.memory_text) for item in active] == [
            (ids[0], "Aria is Mikoolo's platoon quartermaster")
        ]
        assert [(item.id, item.state, item.merged_into_id) for item in all_rows] == [
            (ids[0], "active", None),
            (ids[1], "merged", ids[0]),
        ]
        assert len(evidence) == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_candidate_cannot_attach_across_bottle_perspectives(tmp_path) -> None:
    db = await open_database(tmp_path / "scope.db")
    try:
        aria_id, user_id = await _scope(db, tmp_path, name="aria")
        fraud_id, same_user = await _scope(db, tmp_path, name="frauderick")
        assert same_user == user_id
        aria_candidate = await _candidate(
            db, bot_id=aria_id, user_id=user_id, text="Aria is in the squad",
        )
        aria_memory = await approve_memory_candidate(
            db, candidate_id=aria_candidate, actor="tester",
        )
        fraud_candidate = await _candidate(
            db, bot_id=fraud_id, user_id=user_id, text="Mikoolo drinks coffee",
        )
        with pytest.raises(ValueError, match="share a Bottle and user"):
            await attach_memory_candidate(
                db, candidate_id=fraud_candidate, memory_id=aria_memory,
                actor="tester",
            )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_proposal_acceptance_is_the_only_merge_step(tmp_path) -> None:
    db = await open_database(tmp_path / "proposal.db")
    try:
        bot_id, user_id = await _scope(db, tmp_path)
        memory_ids = []
        for text in ("Aria is squad quartermaster", "Aria handles squad supplies"):
            candidate_id = await _candidate(
                db, bot_id=bot_id, user_id=user_id, text=text,
            )
            memory_ids.append(await approve_memory_candidate(
                db, candidate_id=candidate_id, actor="tester",
            ))
        proposal_id = await create_consolidation_proposal(
            db, memory_ids=memory_ids,
            proposed_text="Aria is the squad quartermaster",
            proposed_type="relationship", proposed_confidence=0.85,
            rationale="Both describe the same squad responsibility",
            actor="scanner",
        )
        before = await list_user_memories(db, bot_id=bot_id, user_id=user_id)
        assert len(before) == 2
        assert await accept_consolidation_proposal(
            db, proposal_id=proposal_id, actor="operator",
        ) == memory_ids[0]
        after = await list_user_memories(db, bot_id=bot_id, user_id=user_id)
        proposal = await get_consolidation_proposal(
            db, proposal_id=proposal_id,
        )
        assert len(after) == 1
        assert proposal is not None and proposal.status == "accepted"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rejected_group_is_not_proposed_again(tmp_path, monkeypatch) -> None:
    db = await open_database(tmp_path / "rejected.db")
    try:
        bot_id, user_id = await _scope(db, tmp_path)
        memory_ids = []
        for text in ("Aria is quartermaster", "Aria manages supplies"):
            candidate_id = await _candidate(
                db, bot_id=bot_id, user_id=user_id, text=text,
            )
            memory_ids.append(await approve_memory_candidate(
                db, candidate_id=candidate_id, actor="tester",
            ))
        proposal_id = await create_consolidation_proposal(
            db, memory_ids=memory_ids, proposed_text="Aria is quartermaster",
            proposed_type="relationship", proposed_confidence=0.8,
            rationale="same role", actor="scanner",
        )
        await reject_consolidation_proposal(
            db, proposal_id=proposal_id, actor="operator",
        )

        async def fake_complete(_profile, _messages) -> str:
            return (
                '{"groups":[{"memory_ids":['
                f"{memory_ids[0]},{memory_ids[1]}"
                '],"proposed_text":"Aria is quartermaster",'
                '"proposed_type":"relationship","proposed_confidence":0.8,'
                '"rationale":"same role"}]}'
            )

        monkeypatch.setattr("cellar.memory_consolidation.complete", fake_complete)
        bottle = await load_bottle(db, bot_id)
        assert await scan_consolidation_proposals(
            db, profile=bottle.llm, bot_id=bot_id, user_id=user_id,
            actor="scanner",
        ) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_retrieval_prefers_relevant_old_memory_over_recent_noise(tmp_path) -> None:
    db = await open_database(tmp_path / "retrieval.db")
    try:
        bot_id, user_id = await _scope(db, tmp_path)
        relevant = await _candidate(
            db, bot_id=bot_id, user_id=user_id,
            text="Mikoolo repairs the brass telescope", memory_type="project",
        )
        await approve_memory_candidate(db, candidate_id=relevant, actor="tester")
        for index in range(12):
            candidate_id = await _candidate(
                db, bot_id=bot_id, user_id=user_id,
                text=f"Mikoolo owns numbered trinket {index}",
                memory_type="preference",
            )
            await approve_memory_candidate(
                db, candidate_id=candidate_id, actor="tester",
            )
        retrieved = await approved_memory_texts(
            db, bot_id=bot_id, user_id=user_id,
            query_text="How is that brass telescope repair going?",
        )
        assert retrieved[0] == "project: Mikoolo repairs the brass telescope"
        assert len(retrieved) == 10
    finally:
        await db.close()
