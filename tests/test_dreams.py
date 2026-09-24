import pytest

from cellar.dream_store import list_dreams, messages_for_dream, recent_dream_texts
from cellar.admin_store import is_quiet, response_enabled, set_quiet
from cellar.dreams import run_dream, run_sleeping_dream
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
        await log_message(
            db, IRCMessage(network="local", channel="@private-user", speaker="alice",
                           body="A private secret", bot_id=bottle_id),
        )
        await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="JeevesBot",
                           body="[Fishing] alice caught a trout", bot_id=bottle_id),
        )
        await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="alice",
                           body="!reel", bot_id=bottle_id),
        )
        await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="alice",
                           body="styx, [Fishing] I caught a Quantum Carp", bot_id=bottle_id),
        )
        await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="alice",
                           body="[11:08:23] <alice> !cast", bot_id=bottle_id),
        )
        await db.execute(
            """INSERT INTO summaries(bot_id, period_start, period_end, summary)
               VALUES (?, '2020-01-01', '2020-01-02', 'historical mixed summary')""",
            (bottle_id,),
        )
        await db.commit()
        assert await recent_dream_texts(db, bot_id=bottle_id) == []
        selected = await messages_for_dream(
            db, bot_id=bottle_id, period_start="2020-01-01",
            period_end="2100-01-01", limit=1,
        )
        assert len(selected) == 1
        assert selected[0][3] == "The telescope is repaired"

        async def fake_complete(_profile, messages) -> str:
            assert "Be a quiet archivist." in messages[0]["content"]
            assert "telescope is repaired" in messages[1]["content"]
            assert "private secret" not in messages[1]["content"]
            assert "[Fishing]" not in messages[1]["content"]
            assert "!reel" not in messages[1]["content"]
            assert "Quantum Carp" not in messages[1]["content"]
            assert "11:08:23" not in messages[1]["content"]
            return "<think>private notes</think>\nThe telescope returned to service."

        monkeypatch.setattr("cellar.dreams.complete", fake_complete)
        summary = await run_dream(db, bottle=await load_bottle(db, bottle_id), hours=24)
        assert summary is not None
        assert summary.summary == "The telescope returned to service."
        assert summary.public_safe
        assert [item.id for item in await list_dreams(db, bot_id=bottle_id)] == [summary.id, 1]
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
        await log_message(
            db, IRCMessage(network="local", channel="@private-user", speaker="alice",
                           body="Only a private conversation", bot_id=bottle_id),
        )
        await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="JeevesBot",
                           body="[Fishing] alice caught a trout", bot_id=bottle_id),
        )
        assert await run_dream(db, bottle=await load_bottle(db, bottle_id)) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sleeping_dream_restores_response_state(tmp_path, monkeypatch) -> None:
    soul = tmp_path / "soul.md"
    soul.write_text("Be a sleepy archivist.", encoding="utf-8")
    db = await open_database(tmp_path / "sleep.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=soul,
            irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_quiet(db, bottle_id=bottle_id, enabled=True, actor="test")
        await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="alice",
                           body="The telescope is ready for morning", bot_id=bottle_id),
        )

        async def fake_complete(_profile, _messages) -> str:
            assert not await response_enabled(db, bottle_id=bottle_id)
            return "A peaceful night."

        monkeypatch.setattr("cellar.dreams.complete", fake_complete)
        summary = await run_sleeping_dream(
            db, bottle=await load_bottle(db, bottle_id), actor="systemd-test",
        )
        assert summary is not None
        assert await response_enabled(db, bottle_id=bottle_id)
        assert await is_quiet(db, bottle_id=bottle_id)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sleeping_dream_restores_state_after_failure(tmp_path, monkeypatch) -> None:
    soul = tmp_path / "soul.md"
    soul.write_text("Be a sleepy archivist.", encoding="utf-8")
    db = await open_database(tmp_path / "sleep-failure.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=soul,
            irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="alice",
                           body="The telescope is ready for morning", bot_id=bottle_id),
        )

        async def failing_complete(_profile, _messages) -> str:
            assert not await response_enabled(db, bottle_id=bottle_id)
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr("cellar.dreams.complete", failing_complete)
        with pytest.raises(RuntimeError, match="LLM unavailable"):
            await run_sleeping_dream(
                db, bottle=await load_bottle(db, bottle_id), actor="systemd-test",
            )
        assert await response_enabled(db, bottle_id=bottle_id)
        assert not await is_quiet(db, bottle_id=bottle_id)
    finally:
        await db.close()
