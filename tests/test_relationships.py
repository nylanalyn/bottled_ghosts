import pytest

from cellar.models import IRCMessage, IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleContext
from cellar.module_loader import available_modules
from cellar.storage import create_bottle, load_bottle, log_message, open_database
from modules.relationships import Module, _settings

PEOPLE = {
    "Frauderick": "your grumpy friend; you tease him about Arch.",
    "bork": "the pug. You would die for him.",
    "aria": "that's you.",
    "Mikoolo": "gave you Egress; you are fond of him.",
}


async def _setup(db, tmp_path):
    bottle_id = await create_bottle(
        db, name="aria", soul_prompt_path=tmp_path / "soul.md",
        irc=IRCProfile(network="local", host="irc.example", nick="aria",
                       username="aria", realname="Aria", channels=["#one"]),
        llm=LLMProfile(endpoint="http://localhost", model="test"),
    )
    return await load_bottle(db, bottle_id)


def _context(db, bottle, *, nick: str, body: str) -> ModuleContext:
    return ModuleContext(
        db=db, bottle=bottle,
        message=IncomingIRCMessage(nick=nick, hostmask=None, account=None,
                                   target="#one", body=body),
        user_id="u", source_message_id=1, conversation="#one", bot_nick="aria",
        module_settings={"relationships": {"people": PEOPLE}},
    )


@pytest.mark.asyncio
async def test_notes_follow_who_is_present_named_or_recent(tmp_path) -> None:
    db = await open_database(tmp_path / "rel.db")
    try:
        bottle = await _setup(db, tmp_path)
        for speaker in ("stranger", "BORK", "aria"):
            await log_message(db, IRCMessage(
                network="local", channel="#one", speaker=speaker, body="hi",
                bot_id=bottle.id,
            ))
        ctx = _context(db, bottle, nick="frauderick", body="mikoolo says hi")
        await Module().before_prompt(ctx)
        sections = ctx.prompt_sections
        assert [section.split(":")[0] for section in sections] == [
            "Your relationship with Frauderick",
            "Your relationship with Mikoolo",
            "Your relationship with bork",
        ]
        assert not any("that's you" in section for section in sections)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_absent_people_add_nothing(tmp_path) -> None:
    db = await open_database(tmp_path / "absent.db")
    try:
        bottle = await _setup(db, tmp_path)
        ctx = _context(db, bottle, nick="stranger", body="nice weather")
        await Module().before_prompt(ctx)
        assert ctx.prompt_sections == []
    finally:
        await db.close()


def test_settings_are_validated() -> None:
    ctx = ModuleContext(
        db=None,  # type: ignore[arg-type]
        bottle=None,  # type: ignore[arg-type]
        message=IncomingIRCMessage(nick="a", hostmask=None, account=None,
                                   target="#one", body="hi"),
        user_id="u", source_message_id=1,
    )
    ctx.module_settings = {"relationships": {"people": {"bob": ""}}}
    with pytest.raises(ValueError, match="bob"):
        _settings(ctx)
    ctx.module_settings = {"relationships": {"people": {"bob": "x" * 501}}}
    with pytest.raises(ValueError, match="500"):
        _settings(ctx)
    ctx.module_settings = {"relationships": {"lookback_lines": -1}}
    with pytest.raises(ValueError, match="lookback_lines"):
        _settings(ctx)


def test_module_is_registered() -> None:
    assert "relationships" in available_modules()


@pytest.mark.asyncio
async def test_set_and_remove_one_note_keeps_the_rest(tmp_path, capsys) -> None:
    import argparse

    from cellar.cli import async_main
    from cellar.module_store import module_settings, set_module_settings
    from modules.relationships import list_relationship_notes, update_relationship_note

    database = tmp_path / "notes.db"
    db = await open_database(database)
    try:
        bottle = await _setup(db, tmp_path)
        await set_module_settings(
            db, bottle_id=bottle.id, module_name="relationships",
            settings={"people": {"Bork": "the pug", "aria": "you"}, "lookback_lines": 20},
            actor="tester",
        )
        # Case-insensitive: replaces "Bork" rather than adding "bork" beside it.
        assert await update_relationship_note(
            db, bottle_id=bottle.id, nick="bork", note="  the  best pug ", actor="tester",
        )
        assert await update_relationship_note(
            db, bottle_id=bottle.id, nick="styx", note="a regular", actor="tester",
        )
        assert await list_relationship_notes(db, bottle_id=bottle.id) == [
            ("aria", "you"), ("bork", "the best pug"), ("styx", "a regular"),
        ]
        assert (await module_settings(db, bottle_id=bottle.id))["relationships"][
            "lookback_lines"] == 20
        assert await update_relationship_note(
            db, bottle_id=bottle.id, nick="ARIA", note=None, actor="tester",
        )
        assert not await update_relationship_note(
            db, bottle_id=bottle.id, nick="nobody", note=None, actor="tester",
        )
        assert [nick for nick, _ in await list_relationship_notes(db, bottle_id=bottle.id)] == [
            "bork", "styx",
        ]
        with pytest.raises(ValueError, match="500"):
            await update_relationship_note(
                db, bottle_id=bottle.id, nick="styx", note="x" * 501, actor="tester",
            )
        with pytest.raises(ValueError, match="single word"):
            await update_relationship_note(
                db, bottle_id=bottle.id, nick="two words", note="hi", actor="tester",
            )
        audits = await (await db.execute(
            """SELECT COUNT(*) FROM configuration_events
               WHERE changed_fields = 'module:relationships:settings'"""
        )).fetchone()
        assert audits[0] == 4
    finally:
        await db.close()

    base = {"database": database, "bottle_id": bottle.id, "actor": "tester"}
    await async_main(argparse.Namespace(
        command="relationship-set", nick="Mikoolo", note="gave you a dog", **base,
    ))
    await async_main(argparse.Namespace(command="relationships", **base))
    output = capsys.readouterr().out
    assert "Set Mikoolo for Bottle" in output
    assert "relationships module is off" in output
    assert "Mikoolo\tgave you a dog" in output
    assert "styx\ta regular" in output
