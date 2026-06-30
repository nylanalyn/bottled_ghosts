import pytest
from textual.widgets import DataTable

from cellar.models import IRCMessage, IRCProfile, LLMProfile
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
        bottles = await dashboard_bottles(db)
        assert [(item.name, item.last_activity is not None) for item in bottles] == [
            ("moss", True)
        ]
        assert [item.body for item in await recent_bottle_messages(
            db, bottle_id=bottle_id
        )] == ["hello"]
    finally:
        await db.close()

    app = BottledGhostsApp(database)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        assert app.query_one("#bottles", DataTable).row_count == 1
