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
