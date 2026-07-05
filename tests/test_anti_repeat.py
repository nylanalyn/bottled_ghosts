import pytest

from cellar.models import IRCMessage, IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleContext
from cellar.module_loader import load_modules
from cellar.module_store import set_module_enabled
from cellar.storage import create_bottle, load_bottle, log_message, open_database
from modules.anti_repeat import (
    DEFAULT_SIMILARITY_THRESHOLD,
    dice_ratio,
    is_duplicate,
    tokenize,
)


# --- Pure-function detection tests ---


def test_tokenize_drops_short_tokens_and_lowercases() -> None:
    # 2-char tokens (ok, hi) survive; single-char tokens (a, I) are dropped.
    assert tokenize("OK hi, I a Ghost said!") == ["ok", "hi", "ghost", "said"]


def test_dice_ratio_identical_lines_is_one() -> None:
    assert dice_ratio("filing that under cursed", "filing that under cursed") == 1.0


def test_dice_ratio_disjoint_lines_is_zero() -> None:
    assert dice_ratio("totally different words here", "silver halide chemistry notes") == 0.0


def test_dice_ratio_known_partial_overlap() -> None:
    # Bigrams of "the ghost sleeps": {(the,ghost), (ghost,sleeps)}
    # Bigrams of "the ghost awakens": {(the,ghost), (ghost,awakens)}
    # Intersection = {(the,ghost)} = 1. |A|+|B| = 4. Ratio = 2/4 = 0.5.
    assert dice_ratio("the ghost sleeps", "the ghost awakens") == 0.5


def test_dice_ratio_short_or_empty_returns_zero() -> None:
    # Single-token and empty inputs have no bigrams, so they never flag.
    assert dice_ratio("ok", "ok") == 0.0
    assert dice_ratio("", "anything here") == 0.0
    assert dice_ratio("a", "a b c d") == 0.0


def test_is_duplicate_flags_paraphrase_above_threshold() -> None:
    recent = ["oh, alice is really doing the fishing thing again"]
    new = "oh, alice is really doing the fishing thing today"
    # Same bigram backbone; only the last word differs. Well above 0.70.
    assert is_duplicate(new, recent, DEFAULT_SIMILARITY_THRESHOLD) is True


def test_is_duplicate_passes_unrelated_text() -> None:
    recent = ["i'll file that under mildly cursed"]
    assert is_duplicate("anyone seen the new server today?", recent, 0.70) is False


# --- Integration tests ---


def _context(db, bottle, *, target: str = "#test", nick: str = "alice",
             response: str | None = None, message_id: int = 1,
             conversation: str | None = None, bot_nick: str | None = None) -> ModuleContext:
    return ModuleContext(
        db=db, bottle=bottle,
        message=IncomingIRCMessage(nick=nick, hostmask=None, account=None,
                                   target=target, body="incoming"),
        user_id=nick, source_message_id=message_id, response=response,
        conversation=conversation, bot_nick=bot_nick,
    )


async def _seed_bot_reply(db, bottle, body: str, *, channel: str = "#test") -> None:
    await log_message(db, IRCMessage(
        network=bottle.irc.network, channel=channel, speaker=bottle.irc.nick,
        body=body, bot_id=bottle.id,
    ))


@pytest.mark.asyncio
async def test_before_prompt_no_bot_replies_injects_nothing(tmp_path) -> None:
    db = await open_database(tmp_path / "ar.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(db, bottle_id=bottle_id, module_name="anti_repeat", enabled=True)
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        ctx = _context(db, bottle)
        await runner.before_prompt(ctx)
        assert ctx.prompt_sections == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_before_prompt_with_bot_replies_injects_awareness_note(tmp_path) -> None:
    db = await open_database(tmp_path / "ar.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(db, bottle_id=bottle_id, module_name="anti_repeat", enabled=True)
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        await _seed_bot_reply(db, bottle, "i'll file that under mildly cursed")
        ctx = _context(db, bottle)
        await runner.before_prompt(ctx)
        assert len(ctx.prompt_sections) == 1
        assert "i'll file that under mildly cursed" in ctx.prompt_sections[0]
        assert "do not repeat" in ctx.prompt_sections[0]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_before_prompt_keeps_newest_replies_in_chronological_order(tmp_path) -> None:
    db = await open_database(tmp_path / "ar.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(db, bottle_id=bottle_id, module_name="anti_repeat", enabled=True)
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        for number in range(10):
            await _seed_bot_reply(db, bottle, f"reply number {number}")
        ctx = _context(db, bottle)
        await runner.before_prompt(ctx)
        note = ctx.prompt_sections[0]
        assert "reply number 0" not in note
        assert "reply number 1" not in note
        assert note.index("reply number 2") < note.index("reply number 9")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_direct_message_uses_canonical_conversation_key(tmp_path) -> None:
    db = await open_database(tmp_path / "ar.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(db, bottle_id=bottle_id, module_name="anti_repeat", enabled=True)
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        conversation = "@stable-user-id"
        await _seed_bot_reply(
            db, bottle, "oh alice is really doing the fishing thing again",
            channel=conversation,
        )
        ctx = _context(
            db, bottle, target="ghost", conversation=conversation, bot_nick="ghost",
            response="oh alice is really doing the fishing thing today",
        )
        await runner.after_response(ctx)
        row = await (await db.execute(
            "SELECT channel, flag_for_next_prompt FROM anti_repeat_state"
        )).fetchone()
        assert row is not None
        assert tuple(row) == (conversation, 1)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_after_response_flags_near_duplicate_and_next_prompt_uses_stronger_note(
    tmp_path,
) -> None:
    db = await open_database(tmp_path / "ar.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(db, bottle_id=bottle_id, module_name="anti_repeat", enabled=True)
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        await _seed_bot_reply(db, bottle, "oh, alice is really doing the fishing thing again")
        # New reply shares the bigram backbone with the seeded line -> flagged.
        ctx = _context(db, bottle, response="oh, alice is really doing the fishing thing today")
        await runner.after_response(ctx)
        flagged = await (await db.execute(
            "SELECT flag_for_next_prompt FROM anti_repeat_state"
        )).fetchone()
        assert flagged is not None and flagged["flag_for_next_prompt"] == 1

        # The next before_prompt consumes the flag and emits the stronger note,
        # then clears the flag so it only fires once per detection.
        ctx2 = _context(db, bottle, message_id=2)
        await runner.before_prompt(ctx2)
        assert any("very similar" in s for s in ctx2.prompt_sections)
        flagged_after = await (await db.execute(
            "SELECT flag_for_next_prompt FROM anti_repeat_state"
        )).fetchone()
        assert flagged_after is not None and flagged_after["flag_for_next_prompt"] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_after_response_does_not_flag_novel_reply(tmp_path) -> None:
    db = await open_database(tmp_path / "ar.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(db, bottle_id=bottle_id, module_name="anti_repeat", enabled=True)
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        await _seed_bot_reply(db, bottle, "i'll file that under mildly cursed")
        ctx = _context(db, bottle, response="has anyone seen the telescope today?")
        await runner.after_response(ctx)
        row = await (await db.execute("SELECT 1 FROM anti_repeat_state")).fetchone()
        assert row is None  # no state row written when nothing flagged
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_channel_scoping_replies_in_one_channel_do_not_flag_another(tmp_path) -> None:
    db = await open_database(tmp_path / "ar.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#a", "#b"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await set_module_enabled(db, bottle_id=bottle_id, module_name="anti_repeat", enabled=True)
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        await _seed_bot_reply(db, bottle, "oh, alice is really doing the fishing thing again",
                              channel="#a")
        # Same reply text, but in channel #b where the seeded line doesn't exist.
        ctx = _context(db, bottle, target="#b",
                       response="oh, alice is really doing the fishing thing today")
        await runner.after_response(ctx)
        row = await (await db.execute("SELECT 1 FROM anti_repeat_state")).fetchone()
        assert row is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_invalid_settings_raise_value_error(tmp_path) -> None:
    # The runtime's module isolation swallows these via ModuleRunner (it
    # disables the module and logs the error rather than propagating), so we
    # exercise the validator directly.
    from modules.anti_repeat import _settings

    db = await open_database(tmp_path / "ar.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        bottle = await load_bottle(db, bottle_id)
        for key, value in (
            ("recent_count", 0),
            ("recent_count", 51),
            ("similarity_threshold", 0.0),
            ("similarity_threshold", 1.0),
            ("lookback_messages", 0),
            ("lookback_messages", 201),
        ):
            ctx = ModuleContext(
                db=db, bottle=bottle,
                message=IncomingIRCMessage(nick="alice", hostmask=None, account=None,
                                           target="#test", body="x"),
                user_id="alice", source_message_id=1,
                module_settings={"anti_repeat": {key: value}},
            )
            with pytest.raises(ValueError):
                _settings(ctx)
    finally:
        await db.close()
