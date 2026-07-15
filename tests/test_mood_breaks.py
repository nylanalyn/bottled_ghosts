import time

import pytest

from cellar.models import IRCProfile, LLMProfile
from cellar.module_api import ModuleContext
from cellar.runtime import _finish_room_break, _start_room_break
from cellar.storage import create_bottle, load_bottle, open_database
from modules.moods import Module


@pytest.mark.asyncio
async def test_maximum_irritability_requests_a_thirty_minute_channel_break(tmp_path) -> None:
    db = await open_database(tmp_path / "break.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        bottle = await load_bottle(db, bottle_id)
        await db.execute(
            """INSERT INTO mood_state(
                   bot_id, valence, irritability, interaction_heat, last_event,
                   last_valence_delta, last_irritability_delta
               ) VALUES (?, 0.2, 1.0, 20.0, 'interaction', 0.0, 0.0)""",
            (bottle_id,),
        )
        await db.commit()
        from cellar.models import IncomingIRCMessage
        ctx = ModuleContext(
            db=db, bottle=bottle,
            message=IncomingIRCMessage(nick="alice", hostmask=None, account=None,
                                       target="#test", body="ghost?"),
            user_id="user", source_message_id=1, conversation="#test",
        )
        await Module().on_message(ctx)
        assert ctx.room_break is not None
        assert ctx.room_break.channel == "#test"
        assert ctx.room_break.duration_seconds == 30 * 60
        assert ctx.suppress_automatic_response
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_due_room_break_resets_defaults_and_completes(tmp_path) -> None:
    db = await open_database(tmp_path / "break.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        bottle = await load_bottle(db, bottle_id)
        await db.execute(
            """INSERT INTO mood_state(
                   bot_id, valence, irritability, interaction_heat, last_event,
                   last_valence_delta, last_irritability_delta
               ) VALUES (?, -0.8, 1.0, 20.0, 'interaction', 0.0, 0.0)""",
            (bottle_id,),
        )
        await db.commit()
        from cellar.module_api import RoomBreakRequest
        assert await _start_room_break(
            db, bottle=bottle,
            request=RoomBreakRequest("#test", 1800, 0.25, -0.4),
        )
        await db.execute(
            """UPDATE mood_room_breaks SET rejoin_at = ?
               WHERE bot_id = ? AND network = ? AND channel = ?""",
            (int(time.time()) - 1, bottle_id, "test", "#test"),
        )
        await db.commit()
        assert await _finish_room_break(db, bottle=bottle, channel="#test")
        mood = await (await db.execute(
            "SELECT valence, irritability, interaction_heat FROM mood_state WHERE bot_id = ?",
            (bottle_id,),
        )).fetchone()
        assert tuple(mood) == pytest.approx((0.25, -0.4, 0.0))
        state = await (await db.execute(
            "SELECT active, reset_at FROM mood_room_breaks WHERE bot_id = ?", (bottle_id,)
        )).fetchone()
        assert tuple(state)[0] == 0
        assert tuple(state)[1] is not None
    finally:
        await db.close()
