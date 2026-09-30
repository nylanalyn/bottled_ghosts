import asyncio

import pytest

from cellar.models import IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import IdleContext, ModuleContext, ModuleRunner
from cellar.module_loader import available_modules
from cellar.runtime import run_bottle_once
from cellar.storage import create_bottle, load_bottle, open_database
from modules.initiative import Module

SETTINGS = {"initiative": {"min_quiet_minutes": 30, "max_quiet_minutes": 30}}


async def _setup(db, tmp_path, *, nick: str = "ghost", channels=("#one",)):
    (tmp_path / "soul.md").write_text("Be concise.", encoding="utf-8")
    bottle_id = await create_bottle(
        db, name=nick, soul_prompt_path=tmp_path / "soul.md",
        irc=IRCProfile(network="local", host="irc.example", nick=nick,
                       username=nick, realname=nick, channels=list(channels)),
        llm=LLMProfile(endpoint="http://localhost", model="test"),
        cooldown_seconds=0,
    )
    return await load_bottle(db, bottle_id)


async def _say(db, bottle, speaker: str, minutes_ago: int, channel: str = "#one") -> None:
    await db.execute(
        """INSERT INTO messages(network, channel, speaker, body, bot_id, timestamp)
           VALUES ('local', ?, ?, 'hello there', ?, datetime('now', ?))""",
        (channel, speaker, bottle.id, f"-{minutes_ago} minutes"),
    )
    await db.commit()


def _idle(db, bottle, *, channel: str = "#one", settings=SETTINGS) -> IdleContext:
    return IdleContext(db=db, bottle=bottle, channel=channel, bot_nick="ghost",
                       module_settings=settings)


@pytest.mark.asyncio
async def test_offers_after_a_lull_with_a_recent_human_then_waits_for_a_human(
    tmp_path,
) -> None:
    db = await open_database(tmp_path / "offer.db")
    try:
        bottle = await _setup(db, tmp_path)
        await _say(db, bottle, "alice", 60)
        module = Module()
        ctx = _idle(db, bottle)
        await module.on_idle(ctx)
        assert ctx.initiative_note is not None
        assert "about 60 minutes" in ctx.initiative_note
        assert "[pass]" in ctx.initiative_note

        # The Bottle spoke (or passed); with no new human line it never
        # offers again, even after another long lull.
        await _say(db, bottle, "ghost", 45)
        again = _idle(db, bottle)
        await module.on_idle(again)
        assert again.initiative_note is None

        # Pretend the offer was an hour ago, then a human spoke after it.
        await db.execute(
            "UPDATE initiative_state SET last_initiative_at = datetime('now', '-50 minutes')"
        )
        await db.execute(
            "UPDATE initiative_events SET created_at = datetime('now', '-50 minutes')"
        )
        await _say(db, bottle, "alice", 40)
        after_human = _idle(db, bottle)
        await module.on_idle(after_human)
        assert after_human.initiative_note is not None
        events = await (await db.execute(
            "SELECT outcome FROM initiative_events ORDER BY id"
        )).fetchall()
        assert [row[0] for row in events] == ["offered", "offered"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_no_offer_in_a_busy_dead_or_bot_only_room(tmp_path) -> None:
    db = await open_database(tmp_path / "guards.db")
    try:
        bottle = await _setup(db, tmp_path)
        await _setup(db, tmp_path, nick="frauderick")
        module = Module()

        await _say(db, bottle, "alice", 5)
        busy = _idle(db, bottle)
        await module.on_idle(busy)
        assert busy.initiative_note is None

        await db.execute("DELETE FROM messages")
        await _say(db, bottle, "alice", 20 * 60)
        dead = _idle(db, bottle)
        await module.on_idle(dead)
        assert dead.initiative_note is None

        # Only sibling Bottles and system events recently: nobody real is around.
        await _say(db, bottle, "JeevesBot", 120)
        await _say(db, bottle, "Domilijn", 100)
        await _say(db, bottle, "frauderick", 90)
        await _say(db, bottle, "IRC runtime", 60)
        bots_only = _idle(db, bottle, settings={
            "initiative": {**SETTINGS["initiative"], "other_bots": ["domilijn"]},
            "ambient_chat": {"utility_bot_nicks": ["JeevesBot"]},
        })
        await module.on_idle(bots_only)
        assert bots_only.initiative_note is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_daily_cap_and_channel_filter(tmp_path) -> None:
    db = await open_database(tmp_path / "cap.db")
    try:
        bottle = await _setup(db, tmp_path, channels=("#one", "#two"))
        module = Module()
        settings = {"initiative": {**SETTINGS["initiative"], "max_per_day": 1,
                                   "channels": ["#ONE"]}}
        await _say(db, bottle, "alice", 60, channel="#two")
        filtered = _idle(db, bottle, channel="#two", settings=settings)
        await module.on_idle(filtered)
        assert filtered.initiative_note is None

        await _say(db, bottle, "alice", 60)
        first = _idle(db, bottle, settings=settings)
        await module.on_idle(first)
        assert first.initiative_note is not None
        await _say(db, bottle, "alice", 50)
        capped = _idle(db, bottle, settings=settings)
        await module.on_idle(capped)
        assert capped.initiative_note is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pass_and_spoken_outcomes_are_recorded(tmp_path) -> None:
    db = await open_database(tmp_path / "outcome.db")
    try:
        bottle = await _setup(db, tmp_path)
        await _say(db, bottle, "alice", 60)
        module = Module()
        await module.on_idle(_idle(db, bottle))

        def reply(response: str) -> ModuleContext:
            return ModuleContext(
                db=db, bottle=bottle,
                message=IncomingIRCMessage(nick="", hostmask=None, account=None,
                                           target="#one", body=""),
                user_id="", source_message_id=1, conversation="#one",
                response_reason="initiative", response=response,
            )

        passed = reply("*[PASS]*")
        await module.after_response(passed)
        assert passed.response is None
        outcome = await (await db.execute("SELECT outcome FROM initiative_events")).fetchone()
        assert outcome[0] == "passed"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_runtime_sends_an_opener_in_a_quiet_room(monkeypatch, tmp_path) -> None:
    db = await open_database(tmp_path / "runtime.db")
    try:
        bottle = await _setup(db, tmp_path)
        await _say(db, bottle, "alice", 60)
        sent: list[tuple[str, str]] = []
        prompts: list[str] = []

        class FakeIRCClient:
            def __init__(self, _profile, handler) -> None:
                self.current_nick = "ghost"
                self.connection_state_handler = None
                self.join_channels: list[str] = []

            async def run(self) -> None:
                self.connection_state_handler(True)
                await asyncio.sleep(0.1)
                self.connection_state_handler(False)

            async def send_message(self, target: str, body: str) -> None:
                sent.append((target, body))

        async def fake_complete(_profile, prompt) -> str:
            prompts.append(prompt[-1]["content"])
            return "did anyone ever get that router working?"

        monkeypatch.setattr("cellar.runtime.IRCClient", FakeIRCClient)
        monkeypatch.setattr("cellar.runtime.complete", fake_complete)
        monkeypatch.setattr("cellar.runtime.IDLE_TICK_SECONDS", 0.01)
        runner = ModuleRunner([("initiative", Module())], SETTINGS)
        await run_bottle_once(db, bottle, runner)

        assert sent == [("#one", "did anyone ever get that router working?")]
        assert "Situation: This channel has been quiet" in prompts[0]
        assert "--- begin quoted IRC message ---" not in prompts[0]
        outcome = await (await db.execute("SELECT outcome FROM initiative_events")).fetchone()
        assert outcome[0] == "spoke"
    finally:
        await db.close()


def test_module_is_registered() -> None:
    assert "initiative" in available_modules()


@pytest.mark.asyncio
async def test_one_lull_gets_one_opener_across_all_bottles(tmp_path) -> None:
    db = await open_database(tmp_path / "chain.db")
    try:
        ghost = await _setup(db, tmp_path)
        other = await _setup(db, tmp_path, nick="bork")
        await _say(db, ghost, "alice", 60)
        await _say(db, other, "alice", 60)
        module = Module()
        first = _idle(db, ghost)
        await module.on_idle(first)
        assert first.initiative_note is not None

        # Bork has never offered here, but ghost already opened this lull.
        second = IdleContext(db=db, bottle=other, channel="#one", bot_nick="bork",
                             module_settings=SETTINGS)
        await module.on_idle(second)
        assert second.initiative_note is None
    finally:
        await db.close()
