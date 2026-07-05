"""Anti-repeat module.

Detects when the bot's reply is too similar to one of its recent lines in the
same channel and, on the *next* prompt, injects a short note telling the model
what it just said and asking for a different angle. The current response is
never suppressed — the model still decides what to say, but with explicit
awareness of its own recent voice. This catches the structural repetition that
token-frequency penalties miss: cases where the words differ but the shape
repeats (e.g. always framing observations as "oh, {user} is really doing
{thing} today").

Similarity is the Sørensen–Dice coefficient over token bigrams: fast, no
dependencies, robust to small word reorderings. This is the exact-search tier
per AGENTS.md Rule 6 — embeddings are intentionally not used.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from cellar.irc import irc_casefold
from cellar.module_api import ModuleContext, NightlyContext

DEFAULT_RECENT_COUNT = 8
DEFAULT_SIMILARITY_THRESHOLD = 0.70
DEFAULT_LOOKBACK_MESSAGES = 30
_MAX_RECENT_COUNT = 50
_MAX_LOOKBACK_MESSAGES = 200

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Settings:
    recent_count: int
    similarity_threshold: float
    lookback_messages: int


def tokenize(text: str) -> list[str]:
    """Lowercase, split on word boundaries, drop tokens shorter than 2 chars.

    Short tokens (ok, hi, lol, the) would otherwise dominate similarity and
    flag unrelated lines as duplicates.
    """
    return [token for token in _TOKEN_RE.findall(text.lower()) if len(token) >= 2]


def bigrams(tokens: list[str]) -> frozenset[tuple[str, str]]:
    if len(tokens) < 2:
        return frozenset()
    return frozenset((tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1))


def dice_ratio(a: str, b: str) -> float:
    """Sørensen–Dice over token bigrams. 1.0 = identical shape, 0.0 = disjoint."""
    set_a = bigrams(tokenize(a))
    set_b = bigrams(tokenize(b))
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    return 2.0 * intersection / (len(set_a) + len(set_b))


def is_duplicate(new_text: str, recent_texts: Iterable[str], threshold: float) -> bool:
    """True if new_text is at least `threshold` similar to any recent line."""
    return any(dice_ratio(new_text, recent) >= threshold for recent in recent_texts)


def _format_recent(recent: list[str]) -> str:
    bullets = "\n".join(f"- {line}" for line in recent) or "- (nothing recent)"
    return (
        "Your own recent replies in this channel, for your awareness (do not "
        f"repeat these):\n{bullets}"
    )


def _format_flagged(recent: list[str]) -> str:
    bullets = "\n".join(f"- {line}" for line in recent) or "- (nothing recent)"
    return (
        "Your previous reply was very similar to something you had just said. "
        "Find a genuinely different angle, change the framing, or stay quiet. "
        f"Do not repeat these:\n{bullets}"
    )


async def _recent_bot_replies(ctx: ModuleContext, *, limit: int) -> list[str]:
    """Return the bot's own recent replies in this channel, oldest-first.

    We filter by the runtime's active nick rather than every configured fallback.
    After a collision, another IRC user may legitimately hold the configured
    primary nick, so treating every addressable name as the bot would misattribute
    that user's speech.
    """
    bot_identity = irc_casefold(ctx.bot_nick or ctx.bottle.irc.nick)
    conversation = ctx.conversation or ctx.message.target
    rows = await (await ctx.db.execute(
        """SELECT speaker, body FROM (
               SELECT id, speaker, body FROM messages
               WHERE bot_id = ? AND network = ? AND channel = ?
               ORDER BY id DESC LIMIT ?
           ) ORDER BY id""",
        (ctx.bottle.id, ctx.bottle.irc.network, conversation, limit),
    )).fetchall()
    # Rows are oldest-first, which lets callers keep the most recent N with a
    # normal tail slice while presenting prompt context chronologically.
    return [
        str(row["body"]) for row in rows
        if irc_casefold(str(row["speaker"])) == bot_identity
    ]


async def _flag_state(ctx: ModuleContext) -> bool:
    conversation = ctx.conversation or ctx.message.target
    row = await (await ctx.db.execute(
        """SELECT flag_for_next_prompt FROM anti_repeat_state
           WHERE bot_id = ? AND network = ? AND channel = ?""",
        (ctx.bottle.id, ctx.bottle.irc.network, conversation),
    )).fetchone()
    return row is not None and bool(row["flag_for_next_prompt"])


async def _set_flag(ctx: ModuleContext, *, flagged: bool) -> None:
    conversation = ctx.conversation or ctx.message.target
    try:
        await ctx.db.execute("BEGIN IMMEDIATE")
        await ctx.db.execute(
            """INSERT INTO anti_repeat_state(
                   bot_id, network, channel, flag_for_next_prompt, updated_at
               ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(bot_id, network, channel) DO UPDATE SET
                   flag_for_next_prompt = excluded.flag_for_next_prompt,
                   updated_at = CURRENT_TIMESTAMP""",
            (ctx.bottle.id, ctx.bottle.irc.network, conversation, 1 if flagged else 0),
        )
        await ctx.db.commit()
    except Exception:
        await ctx.db.rollback()
        raise


async def _clear_flag(ctx: ModuleContext) -> None:
    await _set_flag(ctx, flagged=False)


class Module:
    async def on_message(self, _ctx: ModuleContext) -> None:
        return None

    async def before_prompt(self, ctx: ModuleContext) -> None:
        settings = _settings(ctx)
        recent = await _recent_bot_replies(ctx, limit=settings.lookback_messages)
        # Keep only the most recent N for both the awareness note and any
        # duplicate comparison the next after_response will perform.
        recent = recent[-settings.recent_count:]
        if not recent:
            return
        if await _flag_state(ctx):
            ctx.prompt_sections.append(_format_flagged(recent))
            await _clear_flag(ctx)
        else:
            ctx.prompt_sections.append(_format_recent(recent))

    async def after_response(self, ctx: ModuleContext) -> None:
        if ctx.response is None:
            return
        settings = _settings(ctx)
        recent = await _recent_bot_replies(ctx, limit=settings.lookback_messages)
        recent = recent[-settings.recent_count:]
        # Compare the *new* reply against the bot's prior replies. ctx.response
        # has not been logged yet (runtime logs it after after_response), so it
        # is genuinely new relative to `recent`.
        if recent and is_duplicate(ctx.response, recent, settings.similarity_threshold):
            await _set_flag(ctx, flagged=True)

    async def nightly(self, _ctx: NightlyContext) -> None:
        return None


def _settings(ctx: ModuleContext) -> Settings:
    raw = ctx.module_settings.get("anti_repeat", {})
    recent_count = raw.get("recent_count", DEFAULT_RECENT_COUNT)
    threshold = raw.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD)
    lookback = raw.get("lookback_messages", DEFAULT_LOOKBACK_MESSAGES)
    if (
        not isinstance(recent_count, int) or isinstance(recent_count, bool)
        or not 1 <= recent_count <= _MAX_RECENT_COUNT
    ):
        raise ValueError(
            f"anti_repeat recent_count must be an integer between 1 and {_MAX_RECENT_COUNT}"
        )
    if (
        not isinstance(threshold, (int, float)) or isinstance(threshold, bool)
        or not 0.0 < float(threshold) < 1.0
    ):
        raise ValueError("anti_repeat similarity_threshold must be between 0 and 1 (exclusive)")
    if (
        not isinstance(lookback, int) or isinstance(lookback, bool)
        or not 1 <= lookback <= _MAX_LOOKBACK_MESSAGES
    ):
        raise ValueError(
            f"anti_repeat lookback_messages must be an integer between 1 and {_MAX_LOOKBACK_MESSAGES}"
        )
    return Settings(
        recent_count=recent_count,
        similarity_threshold=float(threshold),
        lookback_messages=lookback,
    )
