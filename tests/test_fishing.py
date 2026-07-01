import pytest

from cellar.models import IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleContext
from cellar.module_loader import load_modules
from cellar.module_store import set_module_enabled, set_module_settings
from cellar.storage import create_bottle, load_bottle, open_database


@pytest.mark.asyncio
async def test_fishing_casts_waits_reels_and_understands_missing_cast(
    monkeypatch, tmp_path,
) -> None:
    db = await open_database(tmp_path / "fishing.db")
    try:
        bottle_id = await create_bottle(
            db, name="angler", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#fish"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(
            db, bottle_id=bottle_id, module_name="fishing", enabled=True,
        )
        await set_module_settings(
            db, bottle_id=bottle_id, module_name="fishing",
            settings={
                "channels": ["#fish"], "game_nick": "Jeeves",
                "min_cast_lines": 1, "max_cast_lines": 1,
                "min_reel_hours": 1, "max_reel_hours": 1,
                "dynamite_chance": 0,
            }, actor="test",
        )
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)

        async def message(nick: str, target: str, body: str) -> ModuleContext:
            context = ModuleContext(
                db=db, bottle=bottle,
                message=IncomingIRCMessage(
                    nick=nick, hostmask=None, account=None, target=target, body=body,
                ),
                user_id=nick, source_message_id=1,
            )
            await runner.on_message(context)
            return context

        outside = await message("alice", "#other", "hello")
        assert outside.commands == []

        cast = await message("alice", "#fish", "anyone fishing?")
        assert [command.body for command in cast.commands] == ["!cast"]
        await message("Jeeves", "#fish", "ghost, You cast your line 8m into the Pond...")
        state = await (await db.execute(
            "SELECT phase, reel_after, cast_at FROM fishing_state"
        )).fetchone()
        assert state is not None and state["phase"] == "fishing"
        assert state["reel_after"] - state["cast_at"] == 3600

        await db.execute("UPDATE fishing_state SET reel_after = 0")
        await db.commit()
        reel = await message("bob", "#fish", "how is the water?")
        assert [command.body for command in reel.commands] == ["!reel"]
        await message("Jeeves", "#fish", "ghost, you don't have a line out. Use !cast first.")
        phase = await (await db.execute("SELECT phase FROM fishing_state")).fetchone()
        assert phase is not None and phase["phase"] == "idle"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fishing_dynamite_ban_is_persisted(monkeypatch, tmp_path) -> None:
    db = await open_database(tmp_path / "dynamite.db")
    try:
        bottle_id = await create_bottle(
            db, name="angler", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#fish"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(db, bottle_id=bottle_id, module_name="fishing", enabled=True)
        await set_module_settings(
            db, bottle_id=bottle_id, module_name="fishing",
            settings={"channels": ["#fish"], "min_cast_lines": 1,
                      "max_cast_lines": 1, "dynamite_chance": 1}, actor="test",
        )
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)

        async def send(nick: str, body: str) -> ModuleContext:
            context = ModuleContext(
                db=db, bottle=bottle,
                message=IncomingIRCMessage(nick=nick, hostmask=None, account=None,
                                           target="#fish", body=body),
                user_id=nick, source_message_id=1,
            )
            await runner.on_message(context)
            return context

        await send("alice", "activity")
        dynamite = await send("Jeeves", "ghost, no such spot. You can fish: Pond.")
        assert [command.body for command in dynamite.commands] == ["!dynamite"]
        await send("Jeeves", "ghost has no hands left and is banned for 7 days.")
        row = await (await db.execute(
            "SELECT phase, banned_until FROM fishing_state"
        )).fetchone()
        assert row is not None and row["phase"] == "banned"
        assert row["banned_until"] is not None
        quiet = await send("alice", "more activity")
        assert quiet.commands == []
    finally:
        await db.close()
