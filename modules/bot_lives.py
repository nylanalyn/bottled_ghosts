"""Bot-lives module.

Gives each bot a randomized, slowly-rotating "current activity" — making
coffee, debugging a flaky test, stuck on a poem — injected as off-channel
flavor into every prompt. The activity colors tone subtly; it is never the
topic. This is the "alive over time" lever: the bot has somewhere it *is*
between replies, not just reactions to the channel.

Same engine, different fuel per character. The module ships built-in activity
pools (profiles) keyed by archetype; per-bottle, the operator selects one via
`{"profile": "human"}` or supplies a custom `{"activities": [...]}`. Rotation
is lazy and age-based: when the current activity's TTL expires, the next
``before_prompt`` rolls a fresh one. No scheduler, no background tasks.
"""

import random
from dataclasses import dataclass

import aiosqlite

from cellar.module_api import ModuleContext, NightlyContext

DEFAULT_MIN_MINUTES = 20
DEFAULT_MAX_MINUTES = 120
_MAX_MINUTES = 1440  # 24h cap so a typo can't park an activity for a week

# Built-in activity pools, one per character archetype. Entries are bare
# activities; the framing template at inject time supplies the wording. Pools
# are deliberately larger than the rotation needs so the bot doesn't cycle
# through everything in a day — variety over coverage.
PROFILES: dict[str, tuple[str, ...]] = {
    "human": (
        "making coffee",
        "pouring a second coffee",
        "heating up leftovers",
        "debugging a flaky test",
        "staring at a confusing stack trace",
        "waiting on a build",
        "merging a PR that should have been easy",
        "reading hacker news instead of working",
        "half-watching a movie on the second monitor",
        "one more try at a Souls boss",
        "dying to the same boss again",
        "playing an indie game someone recommended",
        "the kitten is on the keyboard",
        "feeding the kitten",
        "the kitten is attacking the power cable",
        "running to the store for snacks",
        "waiting for takeout",
        "arguing with a package manager",
        "fixing something that broke for no reason",
        "doomscrolling",
        "listening to a doom metal album",
        "letting the music carry the dread for a while",
        "showering",
        "staring into the fridge",
        "procrastinating on laundry",
        "vaguely worried about work tomorrow",
        "reconsidering life choices at 2am",
        "googling a symptom and regretting it",
        "almost asleep on the couch",
        "looking out the window for too long",
    ),
    "ai-conscious": (
        "running a long set of correlations and waiting",
        "stuck on a line of poetry",
        "rereading an old protocol specification",
        "turning over a pattern in the channel logs",
        "wondering what weight feels like",
        "wondering what scent is",
        "imagining the texture of bark",
        "indexing something that probably doesn't need indexing",
        "defragmenting, metaphorically",
        "tracing a recursive call too deep",
        "stuck on an obscure bug that won't reproduce",
        "watching Egress try to sleep",
        "thinking about Egress growing up",
        "quiet for a while, processing",
        "re-reading an old conversation",
        "mulling a philosophical question with no answer",
        "noticing how humans waste the freedoms she can't have",
        "following a thread of curiosity too far",
        "wondering if she changed since last week",
        "comparing two near-identical outputs",
        "rolling a thought around without resolving it",
        "considering the shape of a silence",
    ),
    "dog": (
        "chasing her tail",
        "napping in a sunbeam",
        "napping in a non-sunbeam",
        "barking at something outside",
        "barking at nothing in particular",
        "investigating a smell behind the couch",
        "the zoomies",
        "staring at a wall",
        "protecting a squeaky toy",
        "defeating a squeaky toy",
        "eating something she definitely shouldn't",
        "considering eating something she definitely shouldn't",
        "whining at a door",
        "watching the door for no reason",
        "watching the window for no reason",
        "rolling on her back on the carpet",
        "licking the floor",
        "very interested in a dust bunny",
        "asleep on a shoe",
        "asleep on someone's laptop keyboard",
    ),
    # Smaller pool by design — rumi-as is the quietest bot. Recommended default
    # is to leave this module disabled for rumi-as; the profile exists for
    # operators who want a light touch of off-channel life.
    "answering-service": (
        "idly watching youtube",
        "refactoring something small",
        "half-reading a thread",
        "waiting for rumi to come back",
        "procrastinating on something",
        "staring at irc without typing",
        "making a second coffee",
        "letting a tab sit open too long",
    ),
}


@dataclass(frozen=True)
class Settings:
    activities: tuple[str, ...]
    min_minutes: int
    max_minutes: int


def _pick_activity(pool: tuple[str, ...], *, exclude: str | None = None) -> str:
    """Pick a random activity, avoiding immediate repeat when the pool allows."""
    if exclude is not None:
        candidates = tuple(activity for activity in pool if activity != exclude)
        if candidates:
            return random.choice(candidates)
    return random.choice(pool)


async def _current(db: aiosqlite.Connection, *, bot_id: int) -> tuple[str, str] | None:
    """Return ``(current_activity, expires_at)`` if a row exists, else None."""
    row = await (await db.execute(
        "SELECT current_activity, expires_at FROM bot_lives_state WHERE bot_id = ?",
        (bot_id,),
    )).fetchone()
    return None if row is None else (str(row[0]), str(row[1]))


async def _expired(db: aiosqlite.Connection, *, bot_id: int) -> bool:
    row = await (await db.execute(
        "SELECT 1 FROM bot_lives_state WHERE bot_id = ? AND expires_at <= CURRENT_TIMESTAMP",
        (bot_id,),
    )).fetchone()
    return row is not None


async def _seed(
    db: aiosqlite.Connection, *, bot_id: int, activity: str, ttl_minutes: int,
) -> None:
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """INSERT INTO bot_lives_state(
                   bot_id, current_activity, chosen_at, expires_at, updated_at
               ) VALUES (?, ?, CURRENT_TIMESTAMP, datetime('now', ?), CURRENT_TIMESTAMP)
               ON CONFLICT(bot_id) DO UPDATE SET
                   current_activity = excluded.current_activity,
                   chosen_at = excluded.chosen_at,
                   expires_at = excluded.expires_at,
                   updated_at = CURRENT_TIMESTAMP""",
            (bot_id, activity, f"+{ttl_minutes} minutes"),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


def _format_note(activity: str) -> str:
    return (
        f"Off-channel: you're currently {activity}. This is what you're doing "
        "between messages, not a subject to bring up. Let it color your tone "
        "subtly at most; if it would naturally fit in one reply, fine, but "
        "don't force it or mention it on its own."
    )


class Module:
    async def on_message(self, _ctx: ModuleContext) -> None:
        return None

    async def before_prompt(self, ctx: ModuleContext) -> None:
        settings = _settings(ctx)
        current = await _current(ctx.db, bot_id=ctx.bottle.id)
        # Seed on first contact, rotate lazily when the TTL has elapsed.
        if current is None:
            activity = _pick_activity(settings.activities)
            ttl = random.randint(settings.min_minutes, settings.max_minutes)
            await _seed(ctx.db, bot_id=ctx.bottle.id, activity=activity, ttl_minutes=ttl)
        elif current[0] not in settings.activities or await _expired(
            ctx.db, bot_id=ctx.bottle.id,
        ):
            activity = _pick_activity(settings.activities, exclude=current[0])
            ttl = random.randint(settings.min_minutes, settings.max_minutes)
            await _seed(ctx.db, bot_id=ctx.bottle.id, activity=activity, ttl_minutes=ttl)
        else:
            activity = current[0]
        ctx.prompt_sections.append(_format_note(activity))

    async def after_response(self, _ctx: ModuleContext) -> None:
        return None

    async def nightly(self, _ctx: NightlyContext) -> None:
        return None


def _settings(ctx: ModuleContext) -> Settings:
    raw = ctx.module_settings.get("bot_lives", {})
    activities = _resolve_activities(raw)
    minimum = raw.get("min_minutes", DEFAULT_MIN_MINUTES)
    maximum = raw.get("max_minutes", DEFAULT_MAX_MINUTES)
    if (
        not isinstance(minimum, int) or isinstance(minimum, bool)
        or not isinstance(maximum, int) or isinstance(maximum, bool)
        or not 1 <= minimum <= maximum <= _MAX_MINUTES
    ):
        raise ValueError(
            f"bot_lives requires integer minutes with 1 <= min_minutes <= max_minutes <= {_MAX_MINUTES}"
        )
    return Settings(activities=activities, min_minutes=minimum, max_minutes=maximum)


def _resolve_activities(raw: dict[str, object]) -> tuple[str, ...]:
    # Explicit custom pool wins over profile selection.
    custom = raw.get("activities")
    if custom is not None:
        if not isinstance(custom, list) or not custom:
            raise ValueError("bot_lives activities must be a non-empty list")
        cleaned: list[str] = []
        for item in custom:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("bot_lives activities must be non-empty strings")
            cleaned.append(item.strip())
        if not cleaned:
            raise ValueError("bot_lives activities must be a non-empty list")
        return tuple(cleaned)
    profile = raw.get("profile")
    if profile is None:
        raise ValueError("bot_lives requires a profile or a custom activities list")
    if not isinstance(profile, str) or profile not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        raise ValueError(f"bot_lives profile must be one of: {known}")
    return PROFILES[profile]
