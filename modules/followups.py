"""Carry one unfinished thread from each nightly dream into the next day.

After the dream summary is written, a small second extraction pass looks for
a single open question or loose end the character would plausibly want to
follow up on later ("did that build ever go green?"). The thread stays
pending for a bounded number of prompts or until it expires, whichever comes
first; each prompt inclusion counts as one chance. Whether and how to bring
it up stays the model's decision — the prompt offers the thread, it does not
command it. All state lives in ``dream_followups`` and is inspectable.
"""

import json
import logging
from dataclasses import dataclass

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cellar.llm import complete
from cellar.memory import FENCE_RE
from cellar.module_api import ModuleContext, NightlyContext
from cellar.models import LLMProfile

logger = logging.getLogger(__name__)
DEFAULT_VALID_HOURS = 36
DEFAULT_MAX_PROMPTS = 3
DEFAULT_MAX_PENDING = 2
MIN_VALID_HOURS = 1
MAX_VALID_HOURS = 24 * 14
MIN_MAX_PROMPTS = 1
MAX_MAX_PROMPTS = 10
MIN_MAX_PENDING = 1
MAX_MAX_PENDING = 5


class FollowupExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    followup: str | None = Field(default=None, max_length=300)


@dataclass(frozen=True)
class Settings:
    valid_hours: int
    max_prompts: int
    max_pending: int


def _int_setting(
    raw: dict[str, object], key: str, default: int, low: int, high: int,
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"followups {key} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"followups {key} must be between {low} and {high}")
    return value


def _settings(ctx: ModuleContext | NightlyContext) -> Settings:
    raw = ctx.module_settings.get("followups", {})
    return Settings(
        valid_hours=_int_setting(
            raw, "valid_hours", DEFAULT_VALID_HOURS, MIN_VALID_HOURS, MAX_VALID_HOURS,
        ),
        max_prompts=_int_setting(
            raw, "max_prompts", DEFAULT_MAX_PROMPTS, MIN_MAX_PROMPTS, MAX_MAX_PROMPTS,
        ),
        max_pending=_int_setting(
            raw, "max_pending", DEFAULT_MAX_PENDING, MIN_MAX_PENDING, MAX_MAX_PENDING,
        ),
    )


def _extraction_messages(summary: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "From this IRC period summary, identify at most one open question, "
                "loose end, or unfinished thread the character would naturally want "
                "to follow up on in conversation later. It must be about other "
                "people, projects, or channel events — not about the character's "
                "own mood or inner state. Phrase it the way the character would "
                "wonder about it, in under 200 characters. Do not invent facts. "
                'Return only JSON: {"followup": "..."} or {"followup": null}. '
                "Use null when nothing qualifies or you are unsure."
            ),
        },
        {"role": "user", "content": summary},
    ]


def _parse_extraction(raw: str) -> FollowupExtraction:
    cleaned = FENCE_RE.sub("", raw.strip())
    return FollowupExtraction.model_validate(json.loads(cleaned))


async def extract_followup(profile: LLMProfile, summary: str) -> str | None:
    extraction_profile = profile.model_copy(update={
        "temperature": 0.0, "max_tokens": 200,
        "frequency_penalty": 0.0, "presence_penalty": 0.0,
    })
    raw = await complete(extraction_profile, _extraction_messages(summary))
    try:
        parsed = _parse_extraction(raw)
    except (json.JSONDecodeError, ValidationError):
        retry_profile = extraction_profile.model_copy(update={"max_tokens": 400})
        parsed = _parse_extraction(await complete(retry_profile, _extraction_messages(summary)))
    text = (parsed.followup or "").strip()
    return text[:300] if text else None


async def store_followup(
    db: aiosqlite.Connection, *, bot_id: int, text: str,
    valid_hours: int, max_pending: int,
) -> int:
    """Store one pending follow-up, keeping at most max_pending pending rows."""
    try:
        await db.execute("BEGIN IMMEDIATE")
        summary_row = await (await db.execute(
            "SELECT id FROM summaries WHERE bot_id = ? ORDER BY id DESC LIMIT 1",
            (bot_id,),
        )).fetchone()
        pending = list(await (await db.execute(
            """SELECT id FROM dream_followups
               WHERE bot_id = ? AND status = 'pending' ORDER BY id""",
            (bot_id,),
        )).fetchall())
        overflow = len(pending) - (max_pending - 1)
        for row in pending[:max(0, overflow)]:
            await db.execute("DELETE FROM dream_followups WHERE id = ?", (row["id"],))
        cursor = await db.execute(
            """INSERT INTO dream_followups(bot_id, summary_id, followup_text, expires_at)
               VALUES (?, ?, ?, datetime('now', ?))""",
            (bot_id,
             int(summary_row["id"]) if summary_row is not None else None,
             text, f"+{valid_hours} hours"),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a follow-up id")
        await db.commit()
        return cursor.lastrowid
    except Exception:
        await db.rollback()
        raise


def _format_note(text: str) -> str:
    return (
        f"Sometime recently you noted an open thread you thought about following "
        f'up on: "{text}". If it fits the current conversation naturally, you may '
        "bring it up in your own words; otherwise ignore it completely and stay on "
        "topic. Do not mention that you were reminded, and never force it."
    )


class Module:
    async def on_message(self, _ctx: ModuleContext) -> None:
        return None

    async def before_prompt(self, ctx: ModuleContext) -> None:
        settings = _settings(ctx)
        row = await (await ctx.db.execute(
            """SELECT id, followup_text, times_shown FROM dream_followups
               WHERE bot_id = ? AND status = 'pending'
                 AND expires_at > CURRENT_TIMESTAMP
               ORDER BY id LIMIT 1""",
            (ctx.bottle.id,),
        )).fetchone()
        if row is None:
            return
        ctx.prompt_sections.append(_format_note(str(row["followup_text"])))
        shown = int(row["times_shown"]) + 1
        status = "asked" if shown >= settings.max_prompts else "pending"
        await ctx.db.execute(
            """UPDATE dream_followups SET times_shown = ?, status = ? WHERE id = ?""",
            (shown, status, int(row["id"])),
        )
        await ctx.db.commit()

    async def after_response(self, _ctx: ModuleContext) -> None:
        return None

    async def nightly(self, ctx: NightlyContext) -> None:
        settings = _settings(ctx)
        try:
            text = await extract_followup(ctx.bottle.llm, ctx.summary)
        except Exception:
            logger.exception(
                "follow-up extraction failed for Bottle %d; skipping",
                ctx.bottle.id,
            )
            return
        if not text:
            logger.info("no follow-up thread found for Bottle %d", ctx.bottle.id)
            return
        followup_id = await store_followup(
            ctx.db, bot_id=ctx.bottle.id, text=text,
            valid_hours=settings.valid_hours, max_pending=settings.max_pending,
        )
        logger.info(
            "stored follow-up %d for Bottle %d (%s)",
            followup_id, ctx.bottle.id, ctx.bottle.name,
        )
