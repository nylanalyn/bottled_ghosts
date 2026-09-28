import random

import pytest

from cellar.identity import resolve_user
from cellar.module_api import ModuleContext
from cellar.models import IncomingIRCMessage, IRCProfile, LLMProfile
from cellar.storage import create_bottle, load_bottle, open_database
from modules.affinity import Module, _settings, current_warmth


async def _bottle(db, tmp_path, *, name: str = "aria"):
    bot_id = await create_bottle(
        db, name=name, soul_prompt_path=tmp_path / f"{name}.md",
        irc=IRCProfile(
            network="local", host="irc.example", nick=name,
            username=name, realname=name.title(), channels=["#test"],
        ),
        llm=LLMProfile(endpoint="http://localhost", model="test"),
    )
    return await load_bottle(db, bot_id)


async def _user(db, *, nick: str = "carol") -> str:
    return await resolve_user(
        db, network="local",
        identity=IncomingIRCMessage(
            nick=nick, hostmask="u@h", account=nick,
            target="#test", body="hello",
        ),
    )


def _context(
    bottle, db, *, user_id: str, reason: str = "addressed", nick: str = "carol",
) -> ModuleContext:
    return ModuleContext(
        db=db, bottle=bottle,
        message=IncomingIRCMessage(
            nick=nick, hostmask=None, account=None, target="#test", body="hi",
        ),
        user_id=user_id, source_message_id=1,
        response_reason=reason,  # type: ignore[arg-type]
    )


def _rated(bottle, db, *, user_id: str, tone: str) -> ModuleContext:
    ctx = _context(bottle, db, user_id=user_id)
    ctx.tones = {user_id: tone}
    return ctx


@pytest.mark.asyncio
async def test_warm_exchanges_warm_and_saturate(tmp_path) -> None:
    random.seed(7)
    db = await open_database(tmp_path / "warm.db")
    try:
        bottle = await _bottle(db, tmp_path)
        user_id = await _user(db)
        module = Module()
        for _ in range(40):
            await module.after_response(_rated(bottle, db, user_id=user_id, tone="warm"))
        warmth = await current_warmth(db, bot_id=bottle.id, user_id=user_id)
        assert 0.6 < warmth <= 1.0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hostile_exchanges_cool_a_friendship_faster_than_warmth_built_it(
    tmp_path,
) -> None:
    random.seed(7)
    db = await open_database(tmp_path / "hostile.db")
    try:
        bottle = await _bottle(db, tmp_path)
        user_id = await _user(db)
        module = Module()
        for _ in range(3):
            await module.after_response(_rated(bottle, db, user_id=user_id, tone="warm"))
        warm = await current_warmth(db, bot_id=bottle.id, user_id=user_id)
        await module.after_response(_rated(bottle, db, user_id=user_id, tone="hostile"))
        after = await current_warmth(db, bot_id=bottle.id, user_id=user_id)
        assert warm > 0.15
        assert after < warm - 0.15
        for _ in range(30):
            await module.after_response(_rated(bottle, db, user_id=user_id, tone="hostile"))
        assert await current_warmth(db, bot_id=bottle.id, user_id=user_id) < -0.55
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_unrated_exchanges_and_raw_messages_do_not_count(tmp_path) -> None:
    db = await open_database(tmp_path / "ambient.db")
    try:
        bottle = await _bottle(db, tmp_path)
        user_id = await _user(db)
        await Module().on_message(_context(bottle, db, user_id=user_id))
        await Module().after_response(_context(bottle, db, user_id=user_id))
        assert await current_warmth(db, bot_id=bottle.id, user_id=user_id) == 0.0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_affinity_requests_a_tone_rating(tmp_path) -> None:
    db = await open_database(tmp_path / "request.db")
    try:
        bottle = await _bottle(db, tmp_path)
        ctx = _context(bottle, db, user_id=await _user(db))
        await Module().before_prompt(ctx)
        assert ctx.request_tone
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_silence_decays_warmth_toward_neutral(tmp_path) -> None:
    random.seed(7)
    db = await open_database(tmp_path / "decay.db")
    try:
        bottle = await _bottle(db, tmp_path)
        user_id = await _user(db)
        await db.execute(
            """INSERT INTO user_affinity(bot_id, user_id, warmth, updated_at)
               VALUES (?, ?, 0.8, datetime('now', '-24 hours'))""",
            (bottle.id, user_id),
        )
        await db.commit()
        await Module().after_response(
            _rated(bottle, db, user_id=user_id, tone="neutral")
        )
        warmth = await current_warmth(db, bot_id=bottle.id, user_id=user_id)
        # A full day of silence cools 0.8 to roughly 0.4 before the new gain.
        assert 0.2 < warmth < 0.6
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_prompt_note_appears_only_above_threshold(tmp_path) -> None:
    db = await open_database(tmp_path / "note.db")
    try:
        bottle = await _bottle(db, tmp_path)
        user_id = await _user(db)
        module = Module()
        ctx = _context(bottle, db, user_id=user_id)
        await module.before_prompt(ctx)
        assert ctx.prompt_sections == []

        await db.execute(
            "INSERT INTO user_affinity(bot_id, user_id, warmth) VALUES (?, ?, 0.6)",
            (bottle.id, user_id),
        )
        await db.commit()
        ctx = _context(bottle, db, user_id=user_id)
        await module.before_prompt(ctx)
        assert len(ctx.prompt_sections) == 1
        assert "Standing impression of carol" in ctx.prompt_sections[0]
        assert "glad to see" in ctx.prompt_sections[0]

        await db.execute(
            "UPDATE user_affinity SET warmth = -0.6 WHERE bot_id = ? AND user_id = ?",
            (bottle.id, user_id),
        )
        await db.commit()
        ctx = _context(bottle, db, user_id=user_id)
        await module.before_prompt(ctx)
        assert "dread" in ctx.prompt_sections[0]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settings_reject_invalid_values(tmp_path) -> None:
    db = await open_database(tmp_path / "settings.db")
    try:
        bottle = await _bottle(db, tmp_path)
        ctx = _context(bottle, db, user_id="test-user")
        ctx.module_settings = {"affinity": {"gain": "lots"}}
        with pytest.raises(ValueError, match="gain"):
            _settings(ctx)
        ctx = _context(bottle, db, user_id="test-user")
        ctx.module_settings = {"affinity": {"note_threshold": 2.0}}
        with pytest.raises(ValueError, match="note_threshold"):
            _settings(ctx)
    finally:
        await db.close()
