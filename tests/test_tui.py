import pytest
from textual.widgets import Checkbox, DataTable, Input, Select

from cellar.identity import resolve_user
from cellar.memory_store import list_memory_candidates, store_memory_candidates
from cellar.module_store import module_states
from cellar.models import (
    ExtractedMemory,
    IRCMessage,
    IRCProfile,
    IncomingIRCMessage,
    LLMProfile,
)
from cellar.storage import create_bottle, log_message, open_database
from tui.app import BottledGhostsApp
from tui.data import dashboard_bottles, recent_bottle_messages


@pytest.mark.asyncio
async def test_dashboard_queries_bottle_and_recent_activity(tmp_path) -> None:
    database = tmp_path / "dashboard.db"
    db = await open_database(database)
    try:
        bottle_id = await create_bottle(
            db, name="moss", soul_prompt_path=tmp_path / "soul.md",
            irc=IRCProfile(network="local", host="irc.example", nick="moss",
                           username="moss", realname="Moss", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="alice",
                           body="hello", bot_id=bottle_id),
        )
        user_id = await resolve_user(
            db, network="local",
            identity=IncomingIRCMessage(nick="alice", hostmask="u@h", account=None,
                                        target="#test", body="I like tea"),
        )
        source_id = await log_message(
            db, IRCMessage(network="local", channel="#test", speaker="alice",
                           body="I like tea", bot_id=bottle_id, user_id=user_id),
        )
        await store_memory_candidates(
            db, user_id=user_id, source_message_id=source_id,
            candidates=[ExtractedMemory(
                text="Likes tea", type="preference", confidence=0.9,
            )],
        )
        candidate_id = (await list_memory_candidates(db))[0].id
        bottles = await dashboard_bottles(db)
        assert [(item.name, item.last_activity is not None) for item in bottles] == [
            ("moss", True)
        ]
        assert [item.body for item in await recent_bottle_messages(
            db, bottle_id=bottle_id
        )] == ["hello", "I like tea"]
    finally:
        await db.close()

    app = BottledGhostsApp(database, actor="tui-test")
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        assert app.query_one("#bottles", DataTable).row_count == 1
        assert app.query_one("#sediment", DataTable).row_count == 1
        app.selected_candidate_id = candidate_id
        await app.action_approve_candidate()
        await pilot.pause()
        assert app.query_one("#sediment", DataTable).row_count == 0
        assert app.query_one("#memories", DataTable).row_count == 1
        app.query_one("#memory-text", Input).value = "Prefers green tea"
        app.query_one("#memory-type", Select).value = "preference"
        app.query_one("#memory-confidence", Input).value = "0.8"
        await app.action_save_memory()
        await pilot.pause()
        app.selected_bottle_id = bottle_id
        app.selected_module_name = "channel_context"
        await app.action_toggle_extraction()
        await app.action_toggle_module()
        await app.action_toggle_bottle()
        await pilot.pause()
        app.query_one("#log-search-query", Input).value = "tea"
        app.query_one("#log-search-scope", Checkbox).value = True
        await app.action_search_logs()
        await pilot.pause()
        assert app.query_one("#log-results", DataTable).row_count == 1

    db = await open_database(database)
    try:
        candidate = await (await db.execute(
            "SELECT status FROM memory_candidates WHERE id = ?", (candidate_id,)
        )).fetchone()
        audit = await (await db.execute(
            "SELECT action, actor FROM audit_events ORDER BY id"
        )).fetchall()
        memory = await (await db.execute(
            "SELECT memory_text, confidence FROM user_memories"
        )).fetchone()
        bottle_state = await (await db.execute(
            "SELECT enabled, extract_memories FROM bots WHERE id = ?", (bottle_id,)
        )).fetchone()
        assert tuple(candidate) == ("approved",)
        assert [tuple(row) for row in audit] == [
            ("approve", "tui-test"),
            ("edit", "tui-test"),
        ]
        assert tuple(memory) == ("Prefers green tea", 0.8)
        assert tuple(bottle_state) == (0, 1)
        assert await module_states(db, bottle_id=bottle_id) == {"channel_context": True}
    finally:
        await db.close()
