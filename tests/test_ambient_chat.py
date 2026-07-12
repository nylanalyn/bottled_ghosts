import pytest

from cellar.models import IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleContext
from cellar.module_loader import load_modules
from cellar.module_store import set_module_enabled, set_module_settings
from cellar.storage import create_bottle, load_bottle, open_database


@pytest.mark.asyncio
async def test_ambient_chat_persists_threshold_and_respects_eligibility(tmp_path) -> None:
    database = tmp_path / "ambient.db"
    db = await open_database(database)
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(
            db, bottle_id=bottle_id, module_name="ambient_chat", enabled=True,
        )
        await set_module_settings(
            db, bottle_id=bottle_id, module_name="ambient_chat",
            settings={"min_lines": 3, "max_lines": 3}, actor="tester",
        )
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)

        ignored = ModuleContext(
            db=db, bottle=bottle,
            message=IncomingIRCMessage(nick="otherbot", hostmask=None, account=None,
                                       target="#test", body="ignored"),
            user_id="ignored", source_message_id=1, response_allowed=False,
        )
        await runner.on_message(ignored)
        direct = ModuleContext(
            db=db, bottle=bottle,
            message=IncomingIRCMessage(nick="alice", hostmask=None, account=None,
                                       target="ghost", body="private"),
            user_id="alice", source_message_id=2,
        )
        await runner.on_message(direct)
        assert await (await db.execute("SELECT 1 FROM ambient_chat_state")).fetchone() is None

        contexts: list[ModuleContext] = []
        for index in range(3):
            context = ModuleContext(
                db=db, bottle=bottle,
                message=IncomingIRCMessage(nick="alice", hostmask=None, account=None,
                                           target="#test", body=f"line {index}"),
                user_id="alice", source_message_id=index + 3,
            )
            contexts.append(context)
            await runner.on_message(context)
        assert [context.request_response for context in contexts] == [False, False, True]
        state = await (await db.execute(
            "SELECT eligible_lines_seen, next_trigger_line FROM ambient_chat_state"
        )).fetchone()
        assert state is not None and tuple(state) == (0, 3)

        contexts[-1].response_reason = "ambient"
        await runner.before_prompt(contexts[-1])
        assert "ambient contribution" in contexts[-1].prompt_sections[-1]
        await runner.after_response(contexts[-1])
        unchanged = await (await db.execute(
            "SELECT eligible_lines_seen, next_trigger_line FROM ambient_chat_state"
        )).fetchone()
        assert unchanged is not None and tuple(unchanged) == (0, 3)

        contexts[-1].response_reason = "addressed"
        await runner.after_response(contexts[-1])
        reset = await (await db.execute(
            "SELECT eligible_lines_seen, next_trigger_line FROM ambient_chat_state"
        )).fetchone()
        assert reset is not None and tuple(reset) == (0, 3)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ambient_chat_paces_utility_bot_events(tmp_path) -> None:
    database = tmp_path / "utility.db"
    db = await open_database(database)
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(
            db, bottle_id=bottle_id, module_name="ambient_chat", enabled=True,
        )
        await set_module_settings(
            db, bottle_id=bottle_id, module_name="ambient_chat",
            settings={
                "min_lines": 20, "max_lines": 40,
                "utility_bot_nicks": ["Jeeves"],
                "utility_min_lines": 2, "utility_max_lines": 2,
            },
            actor="tester",
        )
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)

        def ctx_for(nick: str, target: str, body: str) -> ModuleContext:
            return ModuleContext(
                db=db, bottle=bottle, bot_nick="ghost",
                message=IncomingIRCMessage(nick=nick, hostmask=None, account=None,
                                           target=target, body=body),
                user_id=nick, source_message_id=1,
            )

        # 1. Unaddressed Jeeves announcement: veto set, no request, no state row.
        announcement = ctx_for("Jeeves", "#test", "a roadtrip passes through town")
        await runner.on_message(announcement)
        assert announcement.suppress_automatic_response is True
        assert announcement.request_response is False
        assert await (await db.execute("SELECT 1 FROM ambient_chat_state")).fetchone() is None

        # 2. First Bottle-naming event: veto set, no request, utility_lines_seen == 1.
        first = ctx_for("Jeeves", "#test", "ghost, you caught a fish!")
        await runner.on_message(first)
        assert first.suppress_automatic_response is True
        assert first.request_response is False
        state = await (await db.execute(
            "SELECT eligible_lines_seen, next_trigger_line, "
            "utility_lines_seen, next_utility_trigger_line FROM ambient_chat_state"
        )).fetchone()
        assert state is not None
        assert state["eligible_lines_seen"] == 0
        assert 20 <= state["next_trigger_line"] <= 40
        assert state["utility_lines_seen"] == 1
        assert state["next_utility_trigger_line"] == 2

        # 3. Second matching event: one utility_event response, progress reset, normal untouched.
        second = ctx_for("Jeeves", "#test", "ghost reeled in a big one")
        await runner.on_message(second)
        assert second.suppress_automatic_response is True
        assert second.request_response is True
        assert second.response_reason == "utility_event"
        state = await (await db.execute(
            "SELECT eligible_lines_seen, next_trigger_line, "
            "utility_lines_seen, next_utility_trigger_line FROM ambient_chat_state"
        )).fetchone()
        assert state is not None
        assert state["eligible_lines_seen"] == 0
        assert 20 <= state["next_trigger_line"] <= 40
        assert state["utility_lines_seen"] == 0
        assert state["next_utility_trigger_line"] == 2

        # 4. utility_event prompt instruction; after_response must not reset normal cadence.
        await runner.before_prompt(second)
        assert any("occasional reaction" in s for s in second.prompt_sections)
        before = await (await db.execute(
            "SELECT eligible_lines_seen, next_trigger_line FROM ambient_chat_state"
        )).fetchone()
        await runner.after_response(second)
        after = await (await db.execute(
            "SELECT eligible_lines_seen, next_trigger_line FROM ambient_chat_state"
        )).fetchone()
        assert tuple(before) == tuple(after)

        # 5a. Human direct channel address is not vetoed.
        human = ctx_for("alice", "#test", "ghost: hello there")
        await runner.on_message(human)
        assert human.suppress_automatic_response is False

        # 5b. Jeeves private message is not vetoed (guarded before the utility branch).
        private = ctx_for("Jeeves", "ghost", "ghost, you caught a fish!")
        await runner.on_message(private)
        assert private.suppress_automatic_response is False
    finally:
        await db.close()
