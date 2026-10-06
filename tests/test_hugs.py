import time

import pytest

import modules.hugs as hugs
from cellar.identity import resolve_user_identity
from cellar.models import IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleContext
from cellar.module_loader import available_modules, load_modules
from cellar.module_store import set_module_enabled, set_module_settings
from cellar.storage import create_bottle, load_bottle, open_database


async def _setup(tmp_path, settings: dict[str, object] | None = None):
    db = await open_database(tmp_path / "hugs.db")
    bottle_id = await create_bottle(
        db, name="ghost", soul_prompt_path=tmp_path / "SOUL.md",
        irc=IRCProfile(network="test", host="localhost", nick="ghost",
                       username="ghost", realname="Ghost", channels=["#lobby"]),
        llm=LLMProfile(endpoint="http://localhost", model="test"),
    )
    await set_module_enabled(db, bottle_id=bottle_id, module_name="hugs", enabled=True)
    await set_module_settings(
        db, bottle_id=bottle_id, module_name="hugs",
        settings={"channels": ["#lobby"], "chance": 1.0, **(settings or {})}, actor="test",
    )
    bottle = await load_bottle(db, bottle_id)
    runner = await load_modules(db, bottle_id=bottle_id)

    async def send(nick: str, body: str) -> ModuleContext:
        message = IncomingIRCMessage(
            nick=nick, hostmask=None, account=None, target="#lobby", body=body,
        )
        identity = await resolve_user_identity(db, network="test", identity=message)
        context = ModuleContext(
            db=db, bottle=bottle, message=message,
            user_id=identity.user_id, source_message_id=1,
        )
        await runner.on_message(context)
        return context

    async def set_warmth(nick: str, warmth: float) -> None:
        user_id = (await send(nick, "hi")).user_id
        await db.execute(
            """INSERT INTO user_affinity(bot_id, user_id, warmth) VALUES (?, ?, ?)
               ON CONFLICT(bot_id, user_id) DO UPDATE SET warmth = excluded.warmth,
                   updated_at = CURRENT_TIMESTAMP""",
            (bottle_id, user_id, warmth),
        )
        await db.commit()

    return db, send, set_warmth


async def _events(db) -> list[tuple[str, str]]:
    rows = await (await db.execute("SELECT kind, nick FROM hug_events ORDER BY id")).fetchall()
    return [(row["kind"], row["nick"]) for row in rows]


@pytest.mark.asyncio
async def test_hugs_a_warm_regular_after_a_delay_and_only_once_a_day(tmp_path) -> None:
    db, send, set_warmth = await _setup(tmp_path)
    try:
        assert "hugs" in available_modules()
        await set_warmth("stranger", 0.1)
        await send("stranger", "hello")
        assert await (await db.execute("SELECT * FROM hug_plans")).fetchone() is None

        await set_warmth("alice", 0.8)
        await send("alice", "morning all")
        plan = await (await db.execute("SELECT nick, due_at FROM hug_plans")).fetchone()
        assert plan["nick"] == "alice" and plan["due_at"] > time.time()
        assert (await send("bob", "too early")).commands == []

        await db.execute("UPDATE hug_plans SET due_at = ?", (int(time.time()) - 5,))
        await db.commit()
        due = await send("bob", "anyway")
        assert [command.body for command in due.commands] == ["!hug alice"]
        assert await _events(db) == [("hug", "alice")]

        await set_warmth("carol", 0.9)
        await send("carol", "hi ghost")
        assert await (await db.execute("SELECT * FROM hug_plans")).fetchone() is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_stale_plan_is_dropped_without_hugging(tmp_path) -> None:
    db, send, set_warmth = await _setup(tmp_path)
    try:
        await set_warmth("alice", 0.8)
        await send("alice", "morning")
        await db.execute("UPDATE hug_plans SET due_at = ?",
                         (int(time.time()) - hugs.STALE_PLAN_SECONDS - 60,))
        await db.commit()
        assert (await send("bob", "back from lunch")).commands == []
        assert await _events(db) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rejects_hugs_only_from_people_wearing_on_it(tmp_path) -> None:
    db, send, set_warmth = await _setup(tmp_path, {"chance": 0.0})
    try:
        await set_warmth("grump", -0.6)
        await set_warmth("pal", 0.3)
        reject = await send(
            "Jeeves",
            "grump advances on ghost with arms spread and no discernible plan. "
            "ghost may !reject.",
        )
        assert [command.body for command in reject.commands] == ["!reject"]
        accept = await send(
            "Jeeves",
            "p​al winds up an alarmingly sincere embrace aimed at ghost. ghost may !reject.",
        )
        assert accept.commands == []
        other = await send(
            "Jeeves", "grump has declared bob to be within hugging distance. bob may !reject.",
        )
        assert other.commands == []
        assert await _events(db) == [("reject", "grump"), ("accept", "pal")]
    finally:
        await db.close()
