import pytest

from cellar.models import IRCMessage, IRCProfile, LLMProfile
from cellar.prompt import build_prompt
from cellar.recollections import recollect
from cellar.self_memory import (
    SelfNote, archive_self_memory, list_self_memories, relevant_self_memories,
    usable_self_notes,
)
from cellar.storage import (
    create_bottle, load_bottle, log_message, open_database, set_recollections_enabled,
)


async def _bottle_with_chunk(db, tmp_path, *, channel: str, lines):
    bottle_id = await create_bottle(
        db, name="ghost", soul_prompt_path=tmp_path / "soul.md",
        irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                       username="ghost", realname="Ghost", channels=["#one"]),
        llm=LLMProfile(endpoint="http://localhost", model="test"),
    )
    await set_recollections_enabled(db, bottle_id=bottle_id, enabled=True)
    await db.execute("INSERT INTO users(id, canonical_name) VALUES ('alice', 'alice')")
    for index, (speaker, body) in enumerate(lines):
        message_id = await log_message(
            db, IRCMessage(network="local", channel=channel, speaker=speaker, body=body,
                           bot_id=bottle_id,
                           user_id=None if speaker == "ghost" else "alice"),
        )
        await db.execute(
            "UPDATE messages SET timestamp = ? WHERE id = ?",
            (f"2020-01-01 00:00:0{index}", message_id),
        )
    await db.commit()
    return await load_bottle(db, bottle_id)


@pytest.mark.asyncio
async def test_self_notes_are_stored_even_when_the_chunk_is_discarded(
    tmp_path, monkeypatch,
) -> None:
    db = await open_database(tmp_path / "self.db")
    try:
        bottle = await _bottle_with_chunk(db, tmp_path, channel="#one", lines=[
            ("alice", "ghost, cilantro?"),
            ("ghost", "absolutely not, cilantro tastes like soap to me"),
        ])
        prompts: list[str] = []

        async def fake_complete(_profile, prompt) -> str:
            prompts.append(prompt[0]["content"])
            return (
                '{"keep":false,"summary":null,"self_notes":['
                '{"text":"I think cilantro tastes like soap","type":"preference"},'
                '{"text":"Alice likes cilantro","type":"gossip"}]}'
            )

        monkeypatch.setattr("cellar.recollections.complete", fake_complete)
        assert await recollect(db, bottle=bottle) == 1
        assert "self_notes" in prompts[0]
        assert await relevant_self_memories(
            db, bot_id=bottle.id, query_text="what about cilantro",
        ) == ["2020-01-01: I think cilantro tastes like soap"]
        rows = await list_self_memories(db, bot_id=bottle.id)
        assert [row["memory_type"] for row in rows] == ["preference"]

        await archive_self_memory(db, self_memory_id=rows[0]["id"], actor="tester")
        assert await relevant_self_memories(
            db, bot_id=bottle.id, query_text="cilantro",
        ) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_private_conversations_never_produce_self_notes(tmp_path, monkeypatch) -> None:
    db = await open_database(tmp_path / "private.db")
    try:
        bottle = await _bottle_with_chunk(db, tmp_path, channel="@alice", lines=[
            ("alice", "tell me a secret"),
            ("ghost", "i secretly love cilantro"),
        ])
        prompts: list[str] = []

        async def fake_complete(_profile, prompt) -> str:
            prompts.append(prompt[0]["content"])
            return (
                '{"keep":false,"summary":null,"self_notes":['
                '{"text":"I secretly love cilantro","type":"preference"}]}'
            )

        monkeypatch.setattr("cellar.recollections.complete", fake_complete)
        assert await recollect(db, bottle=bottle) == 1
        assert "self_notes" not in prompts[0]
        assert await list_self_memories(db, bot_id=bottle.id) == []
    finally:
        await db.close()


def test_malformed_notes_are_dropped_individually() -> None:
    notes = usable_self_notes([
        SelfNote(text="  I   collect  vintage keyboards ", type="Preference"),
        SelfNote(text="x" * 201, type="identity"),
        SelfNote(text="I am tired", type="temporary_state"),
        SelfNote(text="!!!", type="identity"),
        SelfNote(text="I promised alice a poem", type="relationship"),
        SelfNote(text="I run on coffee", type="identity"),
        SelfNote(text="fourth usable note", type="identity"),
    ])
    assert [note.text for note in notes] == [
        "I collect vintage keyboards", "I promised alice a poem", "I run on coffee",
    ]
    assert notes[0].type == "preference"


def test_self_memories_appear_in_prompt_only_when_present() -> None:
    common = dict(
        soul="soul", module_state=[], memories=[], dreams=[], relevant=[],
        history=[], speaker="alice", body="cilantro?",
    )
    assert "said about yourself" not in build_prompt(**common)[-1]["content"]
    content = build_prompt(
        **common, self_memories=["2020-01-01: I think cilantro tastes like soap"],
    )[-1]["content"]
    assert "said about yourself" in content
    assert "cilantro tastes like soap" in content
