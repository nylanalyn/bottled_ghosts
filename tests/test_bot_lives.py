import pytest

from cellar.models import IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleContext
from cellar.module_loader import load_modules
from cellar.module_store import set_module_enabled, set_module_settings
from cellar.storage import create_bottle, load_bottle, open_database
from modules.bot_lives import (
    DEFAULT_MAX_MINUTES,
    DEFAULT_MIN_MINUTES,
    PROFILES,
    _pick_activity,
    _resolve_activities,
    _settings,
)


# --- Pure-function unit tests ---


def test_pick_activity_returns_a_pool_member() -> None:
    pool = ("a", "b", "c")
    assert _pick_activity(pool) in pool


def test_pick_activity_excludes_when_pool_allows() -> None:
    pool = ("a", "b", "c")
    for _ in range(20):
        assert _pick_activity(pool, exclude="a") != "a"


def test_pick_activity_returns_exclude_when_pool_is_singleton() -> None:
    # A one-element pool can't avoid the excluded value; we still return something.
    assert _pick_activity(("only",), exclude="only") == "only"


def test_resolve_activities_custom_wins_over_profile() -> None:
    resolved = _resolve_activities({"profile": "human", "activities": ["custom A", "custom B"]})
    assert resolved == ("custom A", "custom B")


def test_resolve_activities_profile_lookup() -> None:
    assert _resolve_activities({"profile": "dog"}) == PROFILES["dog"]


def test_resolve_activities_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError):
        _resolve_activities({"profile": "bird"})


def test_resolve_activities_requires_profile_or_custom() -> None:
    with pytest.raises(ValueError):
        _resolve_activities({})


def test_resolve_activities_rejects_empty_or_non_string_entries() -> None:
    with pytest.raises(ValueError):
        _resolve_activities({"activities": []})
    with pytest.raises(ValueError):
        _resolve_activities({"activities": ["ok", "", "  "]})
    with pytest.raises(ValueError):
        _resolve_activities({"activities": ["ok", 5]})


# --- Integration tests ---


def _context(db, bottle, *, message_id: int = 1) -> ModuleContext:
    return ModuleContext(
        db=db, bottle=bottle,
        message=IncomingIRCMessage(nick="alice", hostmask=None, account=None,
                                   target="#test", body="incoming"),
        user_id="alice", source_message_id=message_id,
    )


async def _enable(db, bottle_id: int, *, settings: dict) -> None:
    await set_module_enabled(db, bottle_id=bottle_id, module_name="bot_lives", enabled=True)
    await set_module_settings(
        db, bottle_id=bottle_id, module_name="bot_lives",
        settings=settings, actor="tester",
    )


@pytest.mark.asyncio
async def test_before_prompt_seeds_when_no_state(tmp_path) -> None:
    db = await open_database(tmp_path / "bl.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await _enable(db, bottle_id, settings={"profile": "human", "min_minutes": 5, "max_minutes": 5})
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        ctx = _context(db, bottle)
        await runner.before_prompt(ctx)
        assert len(ctx.prompt_sections) == 1
        # The injected note names some human-pool activity and frames it as off-channel.
        note = ctx.prompt_sections[0]
        assert any(activity in note for activity in PROFILES["human"])
        assert "Off-channel" in note
        # State row was seeded with a future expiry.
        row = await (await db.execute(
            "SELECT current_activity, chosen_at, expires_at FROM bot_lives_state"
        )).fetchone()
        assert row is not None
        assert row["current_activity"] in PROFILES["human"]
        assert row["expires_at"] > row["chosen_at"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_before_prompt_keeps_unexpired_activity(tmp_path) -> None:
    db = await open_database(tmp_path / "bl.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await _enable(db, bottle_id, settings={"profile": "dog", "min_minutes": 5, "max_minutes": 5})
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        # First call seeds.
        ctx1 = _context(db, bottle)
        await runner.before_prompt(ctx1)
        row = await (await db.execute(
            "SELECT current_activity, expires_at FROM bot_lives_state"
        )).fetchone()
        assert row is not None
        seeded_activity = row["current_activity"]
        seeded_expiry = row["expires_at"]
        # Second call: state is fresh, activity and expiry must be unchanged.
        ctx2 = _context(db, bottle, message_id=2)
        await runner.before_prompt(ctx2)
        row2 = await (await db.execute(
            "SELECT current_activity, expires_at FROM bot_lives_state"
        )).fetchone()
        assert row2 is not None
        assert row2["current_activity"] == seeded_activity
        assert row2["expires_at"] == seeded_expiry
        assert row2["current_activity"] in ctx2.prompt_sections[0]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_before_prompt_rotates_when_expired(tmp_path) -> None:
    db = await open_database(tmp_path / "bl.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        # Two-item custom pool makes "never repeat the previous" deterministic.
        await _enable(db, bottle_id, settings={
            "activities": ["alpha", "beta"], "min_minutes": 5, "max_minutes": 5,
        })
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        ctx1 = _context(db, bottle)
        await runner.before_prompt(ctx1)
        row = await (await db.execute(
            "SELECT current_activity FROM bot_lives_state"
        )).fetchone()
        assert row is not None
        first = row["current_activity"]
        # Force expiry by backdating chosen_at and expires_at.
        await db.execute(
            "UPDATE bot_lives_state SET expires_at = '2000-01-01 00:00:00'"
        )
        await db.commit()
        ctx2 = _context(db, bottle, message_id=2)
        await runner.before_prompt(ctx2)
        row2 = await (await db.execute(
            "SELECT current_activity, expires_at FROM bot_lives_state"
        )).fetchone()
        assert row2 is not None
        # The rotation avoids immediate repeat, so with a 2-item pool it must flip.
        assert row2["current_activity"] != first
        assert row2["current_activity"] in {"alpha", "beta"}
        # Expiry was reset into the future.
        assert row2["expires_at"] > "2000-01-01 00:00:00"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_profile_selection_gates_pool(tmp_path) -> None:
    # A dog-profile bottle only ever yields dog-pool activities across many draws.
    db = await open_database(tmp_path / "bl.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await _enable(db, bottle_id, settings={"profile": "dog", "min_minutes": 1, "max_minutes": 1})
        bottle = await load_bottle(db, bottle_id)
        for _ in range(20):
            runner = await load_modules(db, bottle_id=bottle_id)
            ctx = _context(db, bottle)
            await runner.before_prompt(ctx)
            row = await (await db.execute(
                "SELECT current_activity FROM bot_lives_state"
            )).fetchone()
            assert row is not None and row["current_activity"] in PROFILES["dog"]
            # Force expiry for the next iteration.
            await db.execute(
                "UPDATE bot_lives_state SET expires_at = '2000-01-01 00:00:00'"
            )
            await db.commit()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_custom_activities_override_profile(tmp_path) -> None:
    db = await open_database(tmp_path / "bl.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        await _enable(db, bottle_id, settings={
            "profile": "human",  # should be ignored
            "activities": ["only activity A", "only activity B"],
            "min_minutes": 5, "max_minutes": 5,
        })
        bottle = await load_bottle(db, bottle_id)
        runner = await load_modules(db, bottle_id=bottle_id)
        ctx = _context(db, bottle)
        await runner.before_prompt(ctx)
        row = await (await db.execute(
            "SELECT current_activity FROM bot_lives_state"
        )).fetchone()
        assert row is not None
        assert row["current_activity"] in {"only activity A", "only activity B"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_invalid_settings_raise_value_error(tmp_path) -> None:
    # The runtime's module isolation swallows these via ModuleRunner (it disables
    # the module and logs the error rather than propagating), so we exercise the
    # validator directly, matching the anti_repeat test approach.
    db = await open_database(tmp_path / "bl.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="test", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        bottle = await load_bottle(db, bottle_id)
        for bad_settings in (
            {"min_minutes": 0, "profile": "human"},
            {"min_minutes": 1441, "profile": "human"},
            {"max_minutes": 0, "profile": "human"},
            {"profile": "bird"},
            {},
            {"activities": []},
            {"activities": ["ok", ""]},
        ):
            ctx = ModuleContext(
                db=db, bottle=bottle,
                message=IncomingIRCMessage(nick="alice", hostmask=None, account=None,
                                           target="#test", body="x"),
                user_id="alice", source_message_id=1,
                module_settings={"bot_lives": bad_settings},
            )
            with pytest.raises(ValueError):
                _settings(ctx)
        # Bad min/max ordering: min > max.
        ctx = ModuleContext(
            db=db, bottle=bottle,
            message=IncomingIRCMessage(nick="alice", hostmask=None, account=None,
                                       target="#test", body="x"),
            user_id="alice", source_message_id=1,
            module_settings={"bot_lives": {"profile": "human", "min_minutes": 60, "max_minutes": 30}},
        )
        with pytest.raises(ValueError):
            _settings(ctx)
    finally:
        await db.close()


def test_settings_defaults_when_only_profile_given() -> None:
    # Sanity: a profile-only config falls back to the documented minute defaults.
    class _StubBottle:
        pass
    ctx = ModuleContext(
        db=None,  # type: ignore[arg-type]
        bottle=_StubBottle(),  # type: ignore[arg-type]
        message=IncomingIRCMessage(nick="alice", hostmask=None, account=None,
                                   target="#test", body="x"),
        user_id="alice", source_message_id=1,
        module_settings={"bot_lives": {"profile": "human"}},
    )
    resolved = _settings(ctx)
    assert resolved.min_minutes == DEFAULT_MIN_MINUTES
    assert resolved.max_minutes == DEFAULT_MAX_MINUTES
    assert resolved.activities == PROFILES["human"]
