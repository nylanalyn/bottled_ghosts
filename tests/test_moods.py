import asyncio

import aiosqlite
import pytest

from cellar.models import IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleContext
from cellar.module_loader import available_modules, load_modules
from cellar.module_store import set_module_enabled, set_module_settings
from cellar.storage import create_bottle, load_bottle, open_database
from modules.moods import Mood, PROFILES, Settings, _advance, _interact, mood_status_line


def _settings(**overrides: float) -> Settings:
    values = {
        "baseline_valence": 0.2,
        "baseline_irritability": -0.3,
        "volatility": 0.0,
        "sociability": 1.0,
        "reversion_per_hour": 0.1,
        "attention_gain": 0.04,
        "quiet_loss_per_hour": 0.01,
        "quiet_grace_hours": 3.0,
        "heat_half_life_hours": 0.5,
        "comfort_heat": 2.0,
        "overload_gain": 0.05,
        "ambient_sample_rate": 1.0,
    }
    values.update(overrides)
    return Settings(**values)


def test_profiles_cover_initial_character_tuning() -> None:
    assert PROFILES["frauderick"].baseline_irritability > 0
    assert PROFILES["aria"].baseline_valence > 0
    assert PROFILES["aria"].baseline_irritability < 0
    assert PROFILES["dog"].volatility > PROFILES["aria"].volatility
    assert "rumi" in PROFILES


def test_quiet_time_reverts_then_reduces_valence(monkeypatch) -> None:
    monkeypatch.setattr("modules.moods.random.gauss", lambda _mean, _sigma: 0.0)
    mood = _advance(Mood(0.8, 0.8, 8.0), _settings(), elapsed_hours=10.0)
    assert mood.valence < 0.8
    assert mood.irritability < 0.8
    assert mood.interaction_heat < 0.001


def test_interaction_attention_has_diminishing_returns() -> None:
    settings = _settings()
    low = _interact(Mood(-0.5, -0.3, 0.0), settings)
    high = _interact(Mood(0.8, -0.3, 0.0), settings)
    assert low.valence - (-0.5) > high.valence - 0.8


def test_sustained_interaction_increases_irritability_above_comfort_heat() -> None:
    settings = _settings(comfort_heat=1.0)
    mood = Mood(0.0, -0.3, 0.0)
    mood = _interact(mood, settings)
    assert mood.irritability == pytest.approx(-0.3)
    mood = _interact(mood, settings)
    assert mood.irritability > -0.3


def test_default_ambient_sampling_does_not_pin_busy_channel_irritability(
    monkeypatch,
) -> None:
    monkeypatch.setattr("modules.moods.random.gauss", lambda _mean, _sigma: 0.0)
    settings = _settings(
        baseline_valence=PROFILES["balanced"].baseline_valence,
        baseline_irritability=PROFILES["balanced"].baseline_irritability,
        comfort_heat=8.0,
        overload_gain=0.012,
        ambient_sample_rate=0.05,
    )
    mood = Mood(settings.baseline_valence, settings.baseline_irritability, 0.0)
    last_sampled_at = 0.0
    for index in range(600):
        now = (index + 1) / 600.0
        if index % 20 == 0:
            mood = _advance(mood, settings, elapsed_hours=now - last_sampled_at)
            mood = _interact(mood, settings, intensity=settings.ambient_sample_rate)
            last_sampled_at = now

    assert mood.interaction_heat < settings.comfort_heat
    assert mood.irritability < 0.0


async def _configured_context(
    tmp_path, *, settings: dict[str, object],
) -> tuple[aiosqlite.Connection, ModuleContext]:
    db = await open_database(tmp_path / "moods.db")
    bottle_id = await create_bottle(
        db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
        irc=IRCProfile(
            network="test", host="localhost", nick="ghost", username="ghost",
            realname="Ghost", channels=["#test"],
        ),
        llm=LLMProfile(endpoint="http://localhost", model="test"),
    )
    await set_module_enabled(db, bottle_id=bottle_id, module_name="moods", enabled=True)
    await set_module_settings(
        db, bottle_id=bottle_id, module_name="moods", settings=settings, actor="test",
    )
    bottle = await load_bottle(db, bottle_id)
    return db, ModuleContext(
        db=db, bottle=bottle,
        message=IncomingIRCMessage(
            nick="alice", hostmask=None, account=None, target="#test", body="hello",
        ),
        user_id="user", source_message_id=1, conversation="#test",
    )


@pytest.mark.asyncio
async def test_message_persists_mood_and_prompt_reads_it(tmp_path) -> None:
    db, ctx = await _configured_context(tmp_path, settings={
        "profile": "aria", "volatility": 0.0,
    })
    try:
        runner = await load_modules(db, bottle_id=ctx.bottle.id)
        await asyncio.wait_for(runner.on_message(ctx), timeout=2)
        row = await (await db.execute(
            """SELECT valence, irritability, interaction_heat, last_event,
                      last_valence_delta, last_irritability_delta
               FROM mood_state WHERE bot_id = ?""",
            (ctx.bottle.id,),
        )).fetchone()
        assert row is not None
        assert row["valence"] > PROFILES["aria"].baseline_valence
        assert row["irritability"] == pytest.approx(
            PROFILES["aria"].baseline_irritability
        )
        assert row["interaction_heat"] == 1.0
        assert row["last_event"] == "interaction"
        assert row["last_valence_delta"] > 0
        assert row["last_irritability_delta"] == pytest.approx(0.0)

        await asyncio.wait_for(runner.before_prompt(ctx), timeout=2)
        assert len(ctx.prompt_sections) == 1
        assert "Internal mood:" in ctx.prompt_sections[0]
        assert "valence +" in ctx.prompt_sections[0]
        assert "do not announce" in ctx.prompt_sections[0]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_mood_status_line_renders_after_persistence(tmp_path) -> None:
    db, ctx = await _configured_context(tmp_path, settings={
        "profile": "aria", "volatility": 0.0,
    })
    try:
        # No row yet: status is unavailable, not a misleading zero reading.
        assert await mood_status_line(db, ctx.bottle.id) is None

        runner = await load_modules(db, bottle_id=ctx.bottle.id)
        await asyncio.wait_for(runner.on_message(ctx), timeout=2)
        block = await mood_status_line(db, ctx.bottle.id)
        assert block is not None
        assert block.startswith("```\n")
        assert block.endswith("```")
        # Each axis carries its bar, signed number, and human label.
        assert "valence" in block
        assert "irritability" in block
        assert "heat" in block
        assert block.count("┼") + block.count("·") >= 1  # center marker present
        assert "happy" in block  # aria interaction raises valence past baseline
        # The two compass axes align to the same bar width; heat is a
        # separate filled bar and may differ.
        axis_lines = [ln for ln in block.splitlines() if ln.lstrip().startswith(("valence", "irritability"))]
        assert len(axis_lines) == 2
        widths = {ln.index("]") - ln.index("[") for ln in axis_lines}
        assert len(widths) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_invalid_settings_disable_module_without_crashing_runner(tmp_path) -> None:
    db, ctx = await _configured_context(tmp_path, settings={"volatility": 2.0})
    try:
        runner = await load_modules(db, bottle_id=ctx.bottle.id)
        await runner.on_message(ctx)
        assert "moods" in runner.disabled
        row = await (await db.execute("SELECT 1 FROM mood_state")).fetchone()
        assert row is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ambient_chatter_is_sampled_and_addressed_always_counts(tmp_path) -> None:
    # Ambient chatter with sample rate 0 never persists a mood; addressed
    # messages always count regardless of the ambient sample rate.
    db, ctx = await _configured_context(tmp_path, settings={
        "profile": "aria", "volatility": 0.0, "ambient_sample_rate": 0.0,
    })
    try:
        runner = await load_modules(db, bottle_id=ctx.bottle.id)
        # Ambient: never sampled, so no row appears after many messages.
        ctx.response_reason = "ambient"
        for _ in range(20):
            await runner.on_message(ctx)
        row = await (await db.execute("SELECT 1 FROM mood_state")).fetchone()
        assert row is None

        # Addressed: always counts, even with ambient_sample_rate 0.
        ctx.response_reason = "addressed"
        await runner.on_message(ctx)
        row = await (await db.execute("SELECT interaction_heat FROM mood_state")).fetchone()
        assert row is not None
        assert row["interaction_heat"] == 1.0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ambient_sample_rate_one_persists_every_line(tmp_path) -> None:
    db, ctx = await _configured_context(tmp_path, settings={
        "profile": "aria", "volatility": 0.0, "ambient_sample_rate": 1.0,
    })
    try:
        runner = await load_modules(db, bottle_id=ctx.bottle.id)
        ctx.response_reason = "ambient"
        await runner.on_message(ctx)
        row = await (await db.execute("SELECT interaction_heat FROM mood_state")).fetchone()
        assert row is not None
        assert row["interaction_heat"] == 1.0
    finally:
        await db.close()


def test_moods_is_available_module() -> None:
    assert "moods" in available_modules()
