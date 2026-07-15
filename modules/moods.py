"""Persistent two-axis mood simulation.

Mood is global per Bottle. Incoming interaction raises social satisfaction,
while sustained interaction can raise irritability. Between interactions the
state drifts toward the configured baseline, incurs a bounded quiet-time cost,
and receives small random perturbations. All changes are lazy: IRC messages
drive updates, so the module creates no hidden scheduler.
"""

import math
import random
from dataclasses import dataclass

import aiosqlite

from cellar.module_api import ModuleContext, NightlyContext, RoomBreakRequest

_MAX_ELAPSED_HOURS = 168.0


@dataclass(frozen=True)
class Profile:
    baseline_valence: float
    baseline_irritability: float
    volatility: float
    sociability: float


PROFILES: dict[str, Profile] = {
    "balanced": Profile(0.15, -0.35, 0.08, 1.0),
    "frauderick": Profile(-0.10, 0.15, 0.07, 0.8),
    "aria": Profile(0.45, -0.55, 0.06, 1.0),
    "dog": Profile(0.55, -0.15, 0.14, 1.4),
    "rumi": Profile(0.05, -0.20, 0.12, 0.9),
}


@dataclass(frozen=True)
class Settings:
    baseline_valence: float
    baseline_irritability: float
    volatility: float
    sociability: float
    reversion_per_hour: float
    attention_gain: float
    quiet_loss_per_hour: float
    quiet_grace_hours: float
    heat_half_life_hours: float
    comfort_heat: float
    overload_gain: float
    ambient_sample_rate: float


@dataclass(frozen=True)
class Mood:
    valence: float
    irritability: float
    interaction_heat: float


def _clamp(value: float, minimum: float = -1.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _number(raw: dict[str, object], key: str, default: float, low: float, high: float) -> float:
    value = raw.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"moods {key} must be a number")
    result = float(value)
    if not low <= result <= high:
        raise ValueError(f"moods {key} must be between {low} and {high}")
    return result


def _settings(ctx: ModuleContext) -> Settings:
    raw = ctx.module_settings.get("moods", {})
    profile_name = raw.get("profile", "balanced")
    if not isinstance(profile_name, str) or profile_name not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        raise ValueError(f"moods profile must be one of: {known}")
    profile = PROFILES[profile_name]
    return Settings(
        baseline_valence=_number(raw, "baseline_valence", profile.baseline_valence, -1.0, 1.0),
        baseline_irritability=_number(
            raw, "baseline_irritability", profile.baseline_irritability, -1.0, 1.0,
        ),
        volatility=_number(raw, "volatility", profile.volatility, 0.0, 0.5),
        sociability=_number(raw, "sociability", profile.sociability, 0.0, 2.0),
        reversion_per_hour=_number(raw, "reversion_per_hour", 0.08, 0.0, 1.0),
        attention_gain=_number(raw, "attention_gain", 0.035, 0.0, 0.25),
        quiet_loss_per_hour=_number(raw, "quiet_loss_per_hour", 0.006, 0.0, 0.1),
        quiet_grace_hours=_number(raw, "quiet_grace_hours", 3.0, 0.0, 72.0),
        heat_half_life_hours=_number(raw, "heat_half_life_hours", 0.5, 0.05, 24.0),
        comfort_heat=_number(raw, "comfort_heat", 8.0, 0.0, 20.0),
        overload_gain=_number(raw, "overload_gain", 0.012, 0.0, 0.2),
        ambient_sample_rate=_number(raw, "ambient_sample_rate", 0.05, 0.0, 1.0),
    )


def _initial(settings: Settings) -> Mood:
    # Gaussian draws cluster near the configured baseline rather than filling
    # the full square uniformly.
    return Mood(
        valence=_clamp(random.gauss(settings.baseline_valence, settings.volatility)),
        irritability=_clamp(
            random.gauss(settings.baseline_irritability, settings.volatility)
        ),
        interaction_heat=0.0,
    )


def _advance(mood: Mood, settings: Settings, *, elapsed_hours: float) -> Mood:
    hours = max(0.0, min(_MAX_ELAPSED_HOURS, elapsed_hours))
    if hours == 0.0:
        return mood
    reversion = 1.0 - math.exp(-settings.reversion_per_hour * hours)
    valence = mood.valence + (settings.baseline_valence - mood.valence) * reversion
    irritability = mood.irritability + (
        settings.baseline_irritability - mood.irritability
    ) * reversion
    quiet_hours = max(0.0, hours - settings.quiet_grace_hours)
    valence -= min(0.35, quiet_hours * settings.quiet_loss_per_hour * settings.sociability)
    noise = settings.volatility * min(1.0, math.sqrt(hours / 6.0))
    valence += random.gauss(0.0, noise)
    irritability += random.gauss(0.0, noise)
    heat = mood.interaction_heat * math.pow(0.5, hours / settings.heat_half_life_hours)
    return Mood(_clamp(valence), _clamp(irritability), max(0.0, heat))


def _interact(mood: Mood, settings: Settings, *, intensity: float = 1.0) -> Mood:
    effect = max(0.0, min(1.0, intensity))
    heat = min(20.0, mood.interaction_heat + effect)
    # Attention has diminishing returns near the positive end of the scale.
    attention = (
        settings.attention_gain * settings.sociability
        * (1.0 - max(0.0, mood.valence)) * effect
    )
    overload = settings.overload_gain * max(0.0, heat - settings.comfort_heat) * effect
    return Mood(
        _clamp(mood.valence + attention),
        _clamp(mood.irritability + overload),
        heat,
    )


async def _update(ctx: ModuleContext, settings: Settings, *, intensity: float = 1.0) -> Mood:
    row = await (await ctx.db.execute(
        """SELECT valence, irritability, interaction_heat,
                  (julianday('now') - julianday(last_interaction_at)) * 24.0
           FROM mood_state WHERE bot_id = ?""",
        (ctx.bottle.id,),
    )).fetchone()
    if row is None:
        previous = _initial(settings)
        elapsed = 0.0
    else:
        previous = Mood(float(row[0]), float(row[1]), float(row[2]))
        elapsed = max(0.0, float(row[3] or 0.0))
    advanced = _advance(previous, settings, elapsed_hours=elapsed)
    current = _interact(advanced, settings, intensity=intensity)
    await ctx.db.execute(
        """INSERT INTO mood_state(
               bot_id, valence, irritability, interaction_heat,
               last_interaction_at, updated_at, last_event, last_valence_delta,
               last_irritability_delta
           ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                     'interaction', ?, ?)
           ON CONFLICT(bot_id) DO UPDATE SET
               valence = excluded.valence,
               irritability = excluded.irritability,
               interaction_heat = excluded.interaction_heat,
               last_interaction_at = excluded.last_interaction_at,
               updated_at = excluded.updated_at,
               last_event = excluded.last_event,
               last_valence_delta = excluded.last_valence_delta,
               last_irritability_delta = excluded.last_irritability_delta""",
        (
            ctx.bottle.id, current.valence, current.irritability,
            current.interaction_heat, current.valence - previous.valence,
            current.irritability - previous.irritability,
        ),
    )
    await ctx.db.commit()
    return current


async def _current(ctx: ModuleContext, settings: Settings) -> Mood:
    row = await (await ctx.db.execute(
        "SELECT valence, irritability, interaction_heat FROM mood_state WHERE bot_id = ?",
        (ctx.bottle.id,),
    )).fetchone()
    if row is not None:
        return Mood(float(row[0]), float(row[1]), float(row[2]))
    mood = _initial(settings)
    await ctx.db.execute(
        """INSERT INTO mood_state(
               bot_id, valence, irritability, interaction_heat,
               last_interaction_at, updated_at, last_event,
               last_valence_delta, last_irritability_delta
           ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                     'initial', 0.0, 0.0)
           ON CONFLICT(bot_id) DO NOTHING""",
        (ctx.bottle.id, mood.valence, mood.irritability, mood.interaction_heat),
    )
    await ctx.db.commit()
    return mood


def _label(value: float, *, low: str, middle: str, high: str) -> str:
    if value <= -0.55:
        return f"strongly {low}"
    if value < -0.15:
        return low
    if value < 0.15:
        return middle
    if value < 0.55:
        return high
    return f"strongly {high}"


def _axis_bar(value: float) -> str:
    """Render a -1..+1 value as a fixed-width ASCII position bar.

    A center tick marks the neutral point and `|` marks the value, so the
    whole status block stays aligned in a monospace Discord code block.
    """
    span = 12  # six cells either side of center
    pos = round((value + 1.0) / 2.0 * (2 * span))
    pos = max(0, min(2 * span, int(pos)))
    center = span
    bar = ["─"] * (2 * span + 1)
    bar[center] = "·"
    bar[pos] = "■"
    return f"[{''.join(bar)}]"


def _heat_bar(heat: float) -> str:
    """Render interaction heat (0..20) as a filled bar for quick scanning."""
    cells = 12
    filled = round(heat / 20.0 * cells)
    filled = max(0, min(cells, int(filled)))
    return f"[{'█' * filled}{'░' * (cells - filled)}]"


async def mood_status_line(db: aiosqlite.Connection, bottle_id: int) -> str | None:
    """Discord status block for the mood compass, or None when unavailable.

    Returns None when the moods module is disabled or has not yet produced a
    reading, so callers can omit the section without emitting a misleading
    zero reading.
    """
    row = await (await db.execute(
        """SELECT ms.valence, ms.irritability, ms.interaction_heat
           FROM mood_state ms
           JOIN bot_modules bm
             ON bm.bot_id = ms.bot_id
            AND bm.module_name = 'moods'
            AND bm.enabled = 1
           WHERE ms.bot_id = ?""",
        (bottle_id,),
    )).fetchone()
    if row is None:
        return None
    mood = Mood(float(row[0]), float(row[1]), float(row[2]))
    valence_label = _label(
        mood.valence, low="depressed", middle="content", high="happy",
    )
    temper_label = _label(
        mood.irritability, low="calm", middle="even-tempered", high="irritable",
    )
    return (
        "```\n"
        "mood:\n"
        f"  valence      {_axis_bar(mood.valence)} {mood.valence:+.2f}  {valence_label}\n"
        f"  irritability {_axis_bar(mood.irritability)} {mood.irritability:+.2f}  {temper_label}\n"
        f"  heat         {_heat_bar(mood.interaction_heat)} {mood.interaction_heat:.1f}/20\n"
        "```"
    )


def _format_note(mood: Mood) -> str:
    valence = _label(
        mood.valence, low="depressed", middle="loosely content", high="happy",
    )
    temper = _label(mood.irritability, low="calm", middle="even-tempered", high="irritable")
    return (
        f"Internal mood: {valence} and {temper} "
        f"(valence {mood.valence:+.2f}, irritability {mood.irritability:+.2f}). "
        "Let this affect tone subtly, but do not announce, explain, or make a topic of your mood."
    )


class Module:
    async def on_message(self, ctx: ModuleContext) -> None:
        settings = _settings(ctx)
        # Addressed messages always count as interaction. Ambient channel
        # chatter only samples a configurable fraction, so a busy channel
        # does not pin heat at the cap and drive irritability to 1.0.
        if ctx.response_reason == "ambient" and random.random() >= settings.ambient_sample_rate:
            return
        intensity = settings.ambient_sample_rate if ctx.response_reason == "ambient" else 1.0
        mood = await _update(ctx, settings, intensity=intensity)
        # The runtime owns IRC membership and persists the actual break.  This
        # module only makes the explicit request after its inspectable state
        # reaches the hard ceiling.
        if (
            mood.irritability >= 1.0
            and ctx.conversation is not None
            and ctx.message.target.startswith(("#", "&"))
        ):
            ctx.room_break = RoomBreakRequest(
                channel=ctx.message.target,
                duration_seconds=30 * 60,
                baseline_valence=settings.baseline_valence,
                baseline_irritability=settings.baseline_irritability,
            )
            ctx.suppress_automatic_response = True

    async def before_prompt(self, ctx: ModuleContext) -> None:
        ctx.prompt_sections.append(_format_note(await _current(ctx, _settings(ctx))))

    async def after_response(self, _ctx: ModuleContext) -> None:
        return None

    async def nightly(self, _ctx: NightlyContext) -> None:
        return None
