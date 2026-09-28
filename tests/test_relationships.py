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
