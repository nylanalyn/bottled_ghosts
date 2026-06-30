import pytest

from cellar.dream_store import list_dreams, recent_dream_texts
from cellar.dreams import run_dream
from cellar.models import IRCMessage, IRCProfile, LLMProfile
from cellar.storage import create_bottle, load_bottle, log_message, open_database


@pytest.mark.asyncio
async def test_dream_is_stored_without_private_reasoning(monkeypatch, tmp_path) -> None:
    soul = tmp_path / "soul.md"
    soul.write_text("Be a quiet archivist.", encoding="utf-8")
    db = await open_database(tmp_path / "dreams.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=soul,
            irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="alice",
                           body="The telescope is repaired", bot_id=bottle_id),
        )

        async def fake_complete(_profile, messages) -> str:
            assert "Be a quiet archivist." in messages[0]["content"]
            assert "telescope is repaired" in messages[1]["content"]
            return "<think>private notes</think>\nThe telescope returned to service."

        monkeypatch.setattr("cellar.dreams.complete", fake_complete)
        summary = await run_dream(db, bottle=await load_bottle(db, bottle_id), hours=24)
        assert summary is not None
        assert summary.summary == "The telescope returned to service."
        assert [item.id for item in await list_dreams(db, bot_id=bottle_id)] == [summary.id]
        assert "telescope returned" in (await recent_dream_texts(db, bot_id=bottle_id))[0]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dream_skips_empty_period(tmp_path) -> None:
    soul = tmp_path / "soul.md"
    soul.write_text("Be quiet.", encoding="utf-8")
    db = await open_database(tmp_path / "empty.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=soul,
            irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        assert await run_dream(db, bottle=await load_bottle(db, bottle_id)) is None
    finally:
        await db.close()
