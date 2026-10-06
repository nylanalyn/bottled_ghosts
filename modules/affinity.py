"""Per-user warmth drift.

The moods module models the Bottle's global mood; this module keeps a small
warmth score per person, so a regular who has been around all week feels
different from a stranger or someone who has been rubbing the Bottle the
wrong way. After each addressed exchange the replying model privately rates
how the person treated the Bottle (see cellar.tone). Warm exchanges raise the
score, cold and hostile ones lower it, and a neutral exchange adds a little
familiarity. Changes have diminishing returns near the ends plus a whisper of
jitter so identical patterns diverge; silence cools the score back toward
neutral on an exponential schedule. When the
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
from cellar.tone import TONE_SCORES

logger = logging.getLogger(__name__)
_WARM_JITTER = 0.01
# Multiples of the configured gain for one rated exchange. Neutral keeps the
# old familiarity drift; hostility lands harder than warmth, as it does in
# people.
_TONE_GAIN_MULTIPLIERS = {"warm": 2.0, "neutral": 0.5, "cold": -2.0, "hostile": -5.0}
assert set(_TONE_GAIN_MULTIPLIERS) == set(TONE_SCORES)
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


def warmed(current: float, settings: Settings, tone: str = "neutral") -> float:
    """One rated exchange applied to a decayed warmth score.

    Movement shrinks as the score approaches the end it is moving toward, so
    a long friendship survives one bad day and a feud thaws only gradually.
    """
    change = settings.gain * _TONE_GAIN_MULTIPLIERS[tone]
    headroom = 1.0 - current if change > 0 else 1.0 + current
    return _clamp(current + change * headroom + random.gauss(0.0, _WARM_JITTER))


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
    ctx: ModuleContext, settings: Settings, *, user_id: str, tone: str,
) -> float:
    row = await (await ctx.db.execute(
        """SELECT warmth, (julianday('now') - julianday(updated_at)) * 24.0
           FROM user_affinity WHERE bot_id = ? AND user_id = ?""",
        (ctx.bottle.id, user_id),
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
    warmth = warmed(decayed, settings, tone)
    await ctx.db.execute(
        """INSERT INTO user_affinity(bot_id, user_id, warmth, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(bot_id, user_id) DO UPDATE SET
               warmth = excluded.warmth, updated_at = excluded.updated_at""",
        (ctx.bottle.id, user_id, warmth),
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


async def decayed_warmth(
    db: aiosqlite.Connection, *, bot_id: int, user_id: str, decay_per_hour: float,
) -> float:
    """The warmth score as it stands now, after silence has cooled it."""
    row = await (await db.execute(
        """SELECT warmth, (julianday('now') - julianday(updated_at)) * 24.0
           FROM user_affinity WHERE bot_id = ? AND user_id = ?""",
        (bot_id, user_id),
    )).fetchone()
    if row is None:
        return 0.0
    elapsed = max(0.0, min(_MAX_ELAPSED_HOURS, float(row[1] or 0.0)))
    return float(row[0]) * math.exp(-decay_per_hour * elapsed)


def decay_setting(module_settings: dict[str, dict[str, object]]) -> float:
    """The affinity module's configured decay, for modules that read warmth."""
    return _number(module_settings.get("affinity", {}), "decay_per_hour", 0.03, 0.0, 1.0)


def _format_note(nick: str, warmth: float, threshold: float) -> str:
    return (
        f"Standing impression of {nick}: {warmth_label(warmth, threshold)} "
        f"(warmth {warmth:+.2f}). Let this color how warmly you engage them, "
        "subtly. Never announce, explain, or make a topic of it."
    )


class Module:
    async def on_message(self, _ctx: ModuleContext) -> None:
        return None

    async def before_prompt(self, ctx: ModuleContext) -> None:
        settings = _settings(ctx)
        ctx.request_tone = True
        warmth = await current_warmth(ctx.db, bot_id=ctx.bottle.id, user_id=ctx.user_id)
        if abs(warmth) < settings.note_threshold:
            return
        ctx.prompt_sections.append(
            _format_note(ctx.message.nick, warmth, settings.note_threshold)
        )

    async def after_response(self, ctx: ModuleContext) -> None:
        if not ctx.tones:
            return
        settings = _settings(ctx)
        for user_id, tone in ctx.tones.items():
            warmth = await _update(ctx, settings, user_id=user_id, tone=tone)
            logger.info(
                "Bottle %d rated an exchange with %s as %s (warmth %+.2f)",
                ctx.bottle.id, user_id, tone, warmth,
            )

    async def nightly(self, _ctx: NightlyContext) -> None:
        return None
