"""Per-user warmth drift.

The moods module models the Bottle's global mood; this module keeps a small
warmth score per person, so a regular who has been around all week feels
different from a stranger or someone who has been rubbing the Bottle the
wrong way. Addressed exchanges warm the score slightly (diminishing returns
near the ends, plus a whisper of jitter so identical patterns diverge);
silence cools it back toward neutral on an exponential schedule. When the
score crosses the note threshold, the prompt mentions the standing impression
and asks for subtle coloring — the same contract as moods: state is lazy,
inspectable, and never announced to the room.
"""

import logging
import math
import random
from dataclasses import dataclass

import aiosqlite

from cellar.module_api import ModuleContext, NightlyContext

logger = logging.getLogger(__name__)
_WARM_JITTER = 0.01
_MAX_ELAPSED_HOURS = 24.0 * 14


@dataclass(frozen=True)
class Settings:
    gain: float
    decay_per_hour: float
    note_threshold: float


def _number(raw: dict[str, object], key: str, default: float, low: float, high: float) -> float:
    value = raw.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"affinity {key} must be a number")
    result = float(value)
    if not low <= result <= high:
        raise ValueError(f"affinity {key} must be between {low} and {high}")
    return result


def _settings(ctx: ModuleContext) -> Settings:
    raw = ctx.module_settings.get("affinity", {})
    return Settings(
        gain=_number(raw, "gain", 0.04, 0.0, 0.5),
        decay_per_hour=_number(raw, "decay_per_hour", 0.03, 0.0, 1.0),
        note_threshold=_number(raw, "note_threshold", 0.25, 0.05, 1.0),
    )


def _clamp(value: float, minimum: float = -1.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def warmed(current: float, settings: Settings) -> float:
    """One addressed exchange applied to a decayed warmth score."""
    gain = settings.gain * (1.0 - abs(current))
    return _clamp(current + gain + random.gauss(0.0, _WARM_JITTER))


def warmth_label(warmth: float, threshold: float = 0.25) -> str:
    """Human wording for a warmth score. Shared with the admin API."""
    if warmth >= 0.55:
        return "someone you are genuinely glad to see"
    if warmth >= threshold:
        return "a familiar, welcome presence"
    if warmth <= -0.55:
        return "someone you actively dread dealing with"
    return "someone who has been wearing on you a little"


async def _update(
    ctx: ModuleContext, settings: Settings,
) -> float:
    row = await (await ctx.db.execute(
        """SELECT warmth, (julianday('now') - julianday(updated_at)) * 24.0
           FROM user_affinity WHERE bot_id = ? AND user_id = ?""",
        (ctx.bottle.id, ctx.user_id),
    )).fetchone()
    if row is None:
        current = 0.0
        elapsed = 0.0
    else:
        current = float(row[0])
        elapsed = max(0.0, min(_MAX_ELAPSED_HOURS, float(row[1] or 0.0)))
    decayed = current + (0.0 - current) * (
        1.0 - math.exp(-settings.decay_per_hour * elapsed)
    )
    warmth = warmed(decayed, settings)
    await ctx.db.execute(
        """INSERT INTO user_affinity(bot_id, user_id, warmth, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(bot_id, user_id) DO UPDATE SET
               warmth = excluded.warmth, updated_at = excluded.updated_at""",
        (ctx.bottle.id, ctx.user_id, warmth),
    )
    await ctx.db.commit()
    return warmth


async def current_warmth(
    db: aiosqlite.Connection, *, bot_id: int, user_id: str,
) -> float:
    row = await (await db.execute(
        "SELECT warmth FROM user_affinity WHERE bot_id = ? AND user_id = ?",
        (bot_id, user_id),
    )).fetchone()
    return float(row[0]) if row is not None else 0.0


def _format_note(nick: str, warmth: float, threshold: float) -> str:
    return (
        f"Standing impression of {nick}: {warmth_label(warmth, threshold)} "
        f"(warmth {warmth:+.2f}). Let this color how warmly you engage them, "
        "subtly. Never announce, explain, or make a topic of it."
    )


class Module:
    async def on_message(self, ctx: ModuleContext) -> None:
        if not ctx.response_allowed or ctx.response_reason != "addressed":
            return
        await _update(ctx, _settings(ctx))

    async def before_prompt(self, ctx: ModuleContext) -> None:
        settings = _settings(ctx)
        warmth = await current_warmth(ctx.db, bot_id=ctx.bottle.id, user_id=ctx.user_id)
        if abs(warmth) < settings.note_threshold:
            return
        ctx.prompt_sections.append(
            _format_note(ctx.message.nick, warmth, settings.note_threshold)
        )

    async def after_response(self, _ctx: ModuleContext) -> None:
        return None

    async def nightly(self, _ctx: NightlyContext) -> None:
        return None
