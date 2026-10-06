import asyncio
import json
import time

import pytest

import modules.fishing as fishing
from cellar.models import IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleContext, RuntimeContext, RuntimeState
from cellar.module_loader import load_modules
from cellar.module_store import set_module_enabled, set_module_settings
from cellar.storage import create_bottle, load_bottle, open_database


async def _setup(tmp_path, settings: dict[str, object] | None = None):
    db = await open_database(tmp_path / "fishing.db")
    soul = tmp_path / "SOUL.md"
    soul.write_text("You are a ghost who loves the lake.", encoding="utf-8")
    bottle_id = await create_bottle(
        db, name="angler", soul_prompt_path=soul,
        irc=IRCProfile(network="test", host="localhost", nick="ghost",
                       username="ghost", realname="Ghost", channels=["#fish"]),
        llm=LLMProfile(endpoint="http://localhost", model="test"),
    )
    await set_module_enabled(db, bottle_id=bottle_id, module_name="fishing", enabled=True)
    await set_module_settings(
        db, bottle_id=bottle_id, module_name="fishing",
        settings={"channels": ["#fish"], "ai_choices": False, **(settings or {})},
        actor="test",
    )
    bottle = await load_bottle(db, bottle_id)
    runner = await load_modules(db, bottle_id=bottle_id)

    async def send(nick: str, body: str, target: str = "#fish") -> ModuleContext:
        context = ModuleContext(
            db=db, bottle=bottle,
            message=IncomingIRCMessage(
                nick=nick, hostmask=None, account=None, target=target, body=body,
            ),
            user_id=nick, source_message_id=1,
        )
        await runner.on_message(context)
        return context

    return db, bottle, runner, send


async def _row(db):
    return await (await db.execute("SELECT * FROM fishing_schedule")).fetchone()


@pytest.mark.asyncio
async def test_fishing_recasts_once_a_day_and_keeps_the_outcome(tmp_path) -> None:
    db, _bottle, _runner, send = await _setup(tmp_path)
    try:
        assert (await send("alice", "hello", target="#other")).commands == []
        first = await send("alice", "anyone fishing?")
        assert first.commands == []
        row = await _row(db)
        assert row["next_recast_at"] > time.time()

        await db.execute("UPDATE fishing_schedule SET next_recast_at = 0")
        await db.commit()
        due = await send("bob", "how is the water?")
        assert [command.body for command in due.commands] == ["!recast"]

        await send("Jeeves", "ghost reels in a Bluegill (1.2 lbs)!")
        await send("Jeeves", "ghost casts 9m into the Pond.")
        await send("Jeeves", "carol reels in a boot.")
        row = await _row(db)
        assert row["last_command"] == "!recast"
        assert row["last_outcome"] == (
            "ghost reels in a Bluegill (1.2 lbs)! / ghost casts 9m into the Pond."
        )
        hours = (row["next_recast_at"] - row["last_command_at"]) / 3600
        assert fishing.DEFAULT_MIN_RECAST_HOURS <= hours <= fishing.DEFAULT_MAX_RECAST_HOURS
        assert (await send("bob", "nice")).commands == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fishing_too_soon_reply_retries_after_the_line_soaks(tmp_path) -> None:
    db, _bottle, _runner, send = await _setup(tmp_path)
    try:
        await send("alice", "activity")
        await db.execute("UPDATE fishing_schedule SET next_recast_at = 0")
        await db.commit()
        await send("alice", "more activity")
        await send(
            "Jeeves",
            "ghost, your line has only been out 12m; give it at least an hour before "
            "recasting (!reel still works if you really want it in).",
        )
        row = await _row(db)
        wait = row["next_recast_at"] - time.time()
        assert fishing.TOO_SOON_RETRY_SECONDS - 5 <= wait <= fishing.TOO_SOON_RETRY_SECONDS
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fishing_planned_chum_and_dynamite_then_ban_goes_quiet(tmp_path) -> None:
    db, _bottle, _runner, send = await _setup(tmp_path)
    try:
        await send("alice", "activity")
        await db.execute(
            """UPDATE fishing_schedule SET next_recast_at = 0,
                   planned_recast = '!recast chum', planned_dynamite = 1"""
        )
        await db.commit()
        recast = await send("alice", "activity")
        assert [command.body for command in recast.commands] == ["!recast chum"]
        row = await _row(db)
        assert row["planned_recast"] is None and row["dynamite_due_at"] is not None

        await db.execute("UPDATE fishing_schedule SET dynamite_due_at = 0")
        await db.commit()
        boom = await send("alice", "activity")
        assert [command.body for command in boom.commands] == ["!dynamite"]
        await send(
            "Jeeves",
            "ghost lights the dynamite with their remaining hand. No hands remain — "
            "a 7-day fishing ban has been issued.",
        )
        row = await _row(db)
        assert row["banned_until"] is not None
        assert row["last_dynamite_at"] is not None
        assert row["next_recast_at"] >= row["banned_until"]
        await db.execute("UPDATE fishing_schedule SET next_recast_at = 0")
        await db.commit()
        assert (await send("alice", "more activity")).commands == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fishing_dynamite_waits_out_its_cooldown(tmp_path) -> None:
    db, _bottle, _runner, send = await _setup(tmp_path)
    try:
        await send("alice", "activity")
        await db.execute(
            """UPDATE fishing_schedule SET next_recast_at = 0, planned_recast = '!recast',
                   planned_dynamite = 1, last_dynamite_at = ?""",
            (int(time.time()) - 86400,),
        )
        await db.commit()
        await send("alice", "activity")
        assert (await _row(db))["dynamite_due_at"] is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fishing_lets_the_bottle_choose_before_the_recast(
    monkeypatch, tmp_path,
) -> None:
    db, bottle, runner, send = await _setup(tmp_path, {"ai_choices": True})
    seen: list[list[dict[str, str]]] = []

    async def fake_complete(profile, messages, **_kwargs):
        seen.append(messages)
        return '```json\n{"recast": "lure chum", "dynamite": false}\n```'

    monkeypatch.setattr(fishing, "complete", fake_complete)
    try:
        await runner.start(RuntimeContext(
            db=db, bottle=bottle, database_lock=asyncio.Lock(), state=RuntimeState(),
        ))
        await send("alice", "activity")
        await db.execute("UPDATE fishing_schedule SET next_recast_at = ?",
                         (int(time.time()) + 600,))
        await db.commit()
        await send("alice", "activity")
        module = dict(runner._named_modules)["fishing"]
        await asyncio.gather(*module._planning.values())

        assert seen and seen[0][0]["content"] == "You are a ghost who loves the lake."
        assert "dynamite" in seen[0][1]["content"]
        assert (await _row(db))["planned_recast"] == "!recast lure chum"

        await db.execute("UPDATE fishing_schedule SET next_recast_at = 0")
        await db.commit()
        recast = await send("alice", "activity")
        assert [command.body for command in recast.commands] == ["!recast lure chum"]
    finally:
        await db.close()


def test_parse_choice_only_accepts_offered_options() -> None:
    choice = fishing.parse_choice(
        json.dumps({"recast": "Lure  Chum", "dynamite": True}), dynamite_ok=False,
    )
    assert choice == fishing.Choice(recast="!recast lure chum", dynamite=False)
    assert fishing.parse_choice('{"dynamite": true}', dynamite_ok=True) == fishing.Choice(
        recast="!recast", dynamite=True,
    )
    with pytest.raises(ValueError):
        fishing.parse_choice('{"recast": "!dynamite"}', dynamite_ok=True)
