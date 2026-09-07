import pytest

from cellar.module_api import ModuleContext, NightlyContext
from cellar.models import IncomingIRCMessage, IRCProfile, LLMProfile
from cellar.storage import create_bottle, load_bottle, open_database
from modules.followups import (
    Module,
    _parse_extraction,
    _settings,
    extract_followup,
    store_followup,
)


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


def _nightly(bottle, summary: str, settings: dict | None = None) -> NightlyContext:
    return NightlyContext(
        db=None, bottle=bottle, period_start="2026-09-05 00:00",
        period_end="2026-09-06 00:00", summary=summary,
        module_settings=settings or {},
    )


def _context(bottle, db) -> ModuleContext:
    return ModuleContext(
        db=db, bottle=bottle,
        message=IncomingIRCMessage(
            nick="carol", hostmask=None, account=None, target="#test", body="hi",
        ),
        user_id="user-1", source_message_id=1,
    )


def test_parse_extraction_tolerates_fences() -> None:
    parsed = _parse_extraction('```json\n{"followup": "did the build go green?"}\n```')
    assert parsed.followup == "did the build go green?"
    assert _parse_extraction('{"followup": null}').followup is None
    with pytest.raises(Exception):
        _parse_extraction('{"followup": "x", "extra": true}')


@pytest.mark.asyncio
async def test_extract_followup_uses_model_reply(tmp_path) -> None:
    async def fake_complete(_profile, _messages):
        return '```json\n{"followup": "did the canoe ever get finished?"}\n```'

    import modules.followups as followups_module
    original = followups_module.complete
    followups_module.complete = fake_complete
    try:
        text = await extract_followup(
            LLMProfile(endpoint="http://localhost", model="test"), "a summary",
        )
    finally:
        followups_module.complete = original
    assert text == "did the canoe ever get finished?"


@pytest.mark.asyncio
async def test_store_followup_caps_pending_rows(tmp_path) -> None:
    db = await open_database(tmp_path / "followups.db")
    try:
        bottle = await _bottle(db, tmp_path)
        for text in ("first", "second", "third"):
            await store_followup(
                db, bot_id=bottle.id, text=text, valid_hours=24, max_pending=2,
            )
        rows = list(await (await db.execute(
            "SELECT followup_text FROM dream_followups ORDER BY id"
        )).fetchall())
        assert [row[0] for row in rows] == ["second", "third"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_before_prompt_offers_thread_until_budget_spent(tmp_path) -> None:
    db = await open_database(tmp_path / "budget.db")
    try:
        bottle = await _bottle(db, tmp_path)
        await store_followup(
            db, bot_id=bottle.id, text="did anyone ever fix the bot?",
            valid_hours=24, max_pending=2,
        )
        module = Module()
        sections_seen: list[int] = []
        for _ in range(4):
            ctx = _context(bottle, db)
            await module.before_prompt(ctx)
            sections_seen.append(len(ctx.prompt_sections))
        assert sections_seen == [1, 1, 1, 0]
        row = await (await db.execute(
            "SELECT status, times_shown FROM dream_followups"
        )).fetchone()
        assert row is not None and row["status"] == "asked" and row["times_shown"] == 3
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_before_prompt_ignores_expired_threads(tmp_path) -> None:
    db = await open_database(tmp_path / "expired.db")
    try:
        bottle = await _bottle(db, tmp_path)
        await db.execute(
            """INSERT INTO dream_followups(bot_id, followup_text, expires_at)
               VALUES (?, 'stale', datetime('now', '-1 hour'))""",
            (bottle.id,),
        )
        await db.commit()
        ctx = _context(bottle, db)
        await Module().before_prompt(ctx)
        assert ctx.prompt_sections == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_nightly_stores_followup_from_summary(tmp_path, monkeypatch) -> None:
    db = await open_database(tmp_path / "nightly.db")
    try:
        bottle = await _bottle(db, tmp_path)
        await db.execute(
            """INSERT INTO summaries(bot_id, period_start, period_end, summary)
               VALUES (?, 'p1', 'p2', 'a day of fishing talk')""",
            (bottle.id,),
        )
        await db.commit()

        async def fake_complete(_profile, _messages):
            return '{"followup": "who won the fishing contest?"}'

        monkeypatch.setattr("modules.followups.complete", fake_complete)
        ctx = _nightly(bottle, "a day of fishing talk")
        ctx.db = db
        await Module().nightly(ctx)
        row = await (await db.execute(
            "SELECT followup_text, status, summary_id FROM dream_followups"
        )).fetchone()
        assert row is not None
        assert row["followup_text"] == "who won the fishing contest?"
        assert row["status"] == "pending"
        assert row["summary_id"] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settings_reject_out_of_range_values(tmp_path) -> None:
    db = await open_database(tmp_path / "settings.db")
    try:
        bottle = await _bottle(db, tmp_path)
        ctx = _nightly(bottle, "summary", settings={"followups": {"max_prompts": 0}})
        with pytest.raises(ValueError, match="max_prompts"):
            _settings(ctx)
        ctx = _nightly(bottle, "summary", settings={"followups": {"valid_hours": "x"}})
        with pytest.raises(ValueError, match="valid_hours"):
            _settings(ctx)
    finally:
        await db.close()
