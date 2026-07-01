import asyncio

import aiosqlite
import pytest
from textual.widgets import Checkbox, DataTable, Input, Select

from cellar.identity import resolve_user
from cellar.memory_store import list_memory_candidates, store_memory_candidates
from cellar.module_store import module_settings, module_states
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
async def test_dashboard_queries_bottle_and_recent_activity(monkeypatch, tmp_path) -> None:
    database = tmp_path / "dashboard.db"
    soul_path = tmp_path / "soul.md"
    soul_path.write_text("Be mossy.", encoding="utf-8")
    db = await open_database(database)
    try:
        bottle_id = await create_bottle(
            db, name="moss", soul_prompt_path=soul_path,
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
            db, user_id=user_id, source_message_ids=[source_id],
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

    runtime_started = asyncio.Event()

    async def fake_run_bottle(_database, _bottle) -> None:
        runtime_started.set()
        await asyncio.Future()

    monkeypatch.setattr("tui.app.run_bottle_from_database", fake_run_bottle)
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
        app.query_one("#ignore-network", Input).value = "local"
        app.query_one("#ignore-match-type", Select).value = "account"
        app.query_one("#ignore-match-value", Input).value = "otherbot"
        app.query_one("#ignore-action", Select).value = "no_response"
        await app.action_add_ignore_rule()
        assert app.query_one("#ignore-list", DataTable).row_count == 1
        await app.action_delete_ignore_rule()
        assert app.query_one("#ignore-list", DataTable).row_count == 0
        await app.action_toggle_runtime()
        await runtime_started.wait()
        assert bottle_id in app.running_bottles
        await app.action_toggle_runtime()
        assert bottle_id not in app.running_bottles
        await app.action_toggle_extraction()
        app.query_one("#module-list", DataTable).move_cursor(row=1)
        await pilot.pause()
        assert app.selected_module_name == "channel_context"
        await app.action_toggle_module()
        app.query_one("#module-settings", Input).value = '{"label":"quiet room"}'
        await app.action_save_module_settings()
        await app.action_toggle_bottle()
        await pilot.pause()
        app.query_one("#log-search-query", Input).value = "tea"
        app.query_one("#log-search-scope", Checkbox).value = True
        await app.action_search_logs()
        await pilot.pause()
        assert app.query_one("#log-results", DataTable).row_count == 1
        app.query_one("#config-name", Input).value = "mossy"
        app.query_one("#config-model", Input).value = "new-model"
        app.query_one("#config-user-modes", Input).value = "+B"
        await app.action_save_configuration()
        await pilot.pause()
        app.action_new_configuration()
        new_values = {
            "config-name": "fern",
            "config-soul": str(soul_path),
            "config-network": "local",
            "config-host": "irc.example",
            "config-port": "6697",
            "config-nick": "fern",
            "config-username": "fern",
            "config-realname": "Fern",
            "config-user-modes": "+B",
            "config-channels": "#test",
            "config-endpoint": "http://localhost/chat",
            "config-model": "new-model",
        }
        for field_id, value in new_values.items():
            app.query_one(f"#{field_id}", Input).value = value
        await app.action_save_configuration()
        await pilot.pause()
        assert app.query_one("#bottles", DataTable).row_count == 2
        assert app.query_one("#audit-list", DataTable).row_count == 11

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
            "SELECT enabled, extract_memories, name FROM bots WHERE id = ?", (bottle_id,)
        )).fetchone()
        model = await (await db.execute(
            """SELECT l.model FROM llm_profiles l JOIN bots b ON b.llm_profile_id = l.id
               WHERE b.id = ?""", (bottle_id,)
        )).fetchone()
        user_modes = await (await db.execute(
            """SELECT i.user_modes FROM irc_profiles i
               JOIN bots b ON b.irc_profile_id = i.id WHERE b.id = ?""", (bottle_id,)
        )).fetchone()
        configuration_events = await (await db.execute(
            "SELECT actor, changed_fields FROM configuration_events ORDER BY id"
        )).fetchall()
        bottle_count = await (await db.execute("SELECT COUNT(*) FROM bots")).fetchone()
        assert candidate is not None
        assert memory is not None
        assert bottle_state is not None
        assert model is not None
        assert user_modes is not None
        assert bottle_count is not None
        assert tuple(candidate) == ("approved",)
        assert [tuple(row) for row in audit] == [
            ("approve", "tui-test"),
            ("edit", "tui-test"),
        ]
        assert tuple(memory) == ("Prefers green tea", 0.8)
        assert tuple(bottle_state) == (0, 1, "mossy")
        assert tuple(model) == ("new-model",)
        assert tuple(user_modes) == ("+B",)
        assert [tuple(row) for row in configuration_events] == [
            ("operator", "created"),
            ("tui-test", "ignore_rule:added"),
            ("tui-test", "ignore_rule:deleted"),
            ("tui-test", "extract_memories"),
            ("tui-test", "module:channel_context:enabled"),
            ("tui-test", "module:channel_context:settings"),
            ("tui-test", "enabled"),
            ("tui-test", "model,name,user_modes"),
            ("tui-test", "created"),
        ]
        assert bottle_count[0] == 2
        assert await module_states(db, bottle_id=bottle_id) == {"channel_context": True}
        assert await module_settings(db, bottle_id=bottle_id) == {
            "channel_context": {"label": "quiet room"}
        }
        with pytest.raises(aiosqlite.IntegrityError, match="append-only"):
            await db.execute("DELETE FROM configuration_events")
        await db.rollback()
    finally:
        await db.close()
