"""Operator-written relationship notes, shown only when the person is present.

Each Bottle's settings map IRC nicks to a short note about how the character
sees that person, for example another Bottle:

    {"people": {"frauderick": "your grumpy friend; you tease him about Arch"}}

A note enters the prompt when that person is one of the current speakers,
spoke in the recent room history, or is mentioned in the current message.
Relationships are then felt in the moment instead of recited in every reply.
How the relationship is going lives in the affinity module; these notes are
the history and texture behind it. Notes are configuration, written once, and
never generated or changed by the model.
"""

from dataclasses import dataclass

import aiosqlite

from cellar.irc import irc_casefold, mentions_nick
from cellar.module_api import ModuleContext, NightlyContext
from cellar.module_store import module_settings, set_module_settings

DEFAULT_LOOKBACK_LINES = 15
MAX_LOOKBACK_LINES = 100
MAX_NOTE_CHARS = 500
MAX_NOTES_PER_PROMPT = 4


@dataclass(frozen=True)
class Settings:
    people: dict[str, tuple[str, str]]  # casefolded nick -> (display nick, note)
    lookback_lines: int


def _parse_people(raw: dict[str, object]) -> dict[str, tuple[str, str]]:
    people = raw.get("people", {})
    if not isinstance(people, dict):
        raise ValueError("relationships people must be an object of nick to note")
    parsed: dict[str, tuple[str, str]] = {}
    for nick, note in people.items():
        if not isinstance(nick, str) or not nick.strip():
            raise ValueError("relationships nicks must be non-empty strings")
        if not isinstance(note, str) or not note.strip():
            raise ValueError(f"relationships note for {nick} must be a non-empty string")
        if len(note) > MAX_NOTE_CHARS:
            raise ValueError(
                f"relationships note for {nick} must be {MAX_NOTE_CHARS} characters or fewer"
            )
        parsed[irc_casefold(nick.strip())] = (nick.strip(), " ".join(note.split()))
    return parsed


def _settings(ctx: ModuleContext) -> Settings:
    raw = ctx.module_settings.get("relationships", {})
    parsed = _parse_people(raw)
    lookback = raw.get("lookback_lines", DEFAULT_LOOKBACK_LINES)
    if (
        isinstance(lookback, bool) or not isinstance(lookback, int)
        or not 0 <= lookback <= MAX_LOOKBACK_LINES
    ):
        raise ValueError(
            f"relationships lookback_lines must be an integer from 0 to {MAX_LOOKBACK_LINES}"
        )
    return Settings(people=parsed, lookback_lines=lookback)


async def _recent_speakers(ctx: ModuleContext, limit: int) -> list[str]:
    """Casefolded speakers in the recent conversation, most recent first."""
    if limit == 0:
        return []
    conversation = ctx.conversation or ctx.message.target
    rows = await (await ctx.db.execute(
        """SELECT speaker FROM messages
           WHERE bot_id = ? AND network = ? AND channel = ?
           ORDER BY id DESC LIMIT ?""",
        (ctx.bottle.id, ctx.bottle.irc.network, conversation, limit),
    )).fetchall()
    return [irc_casefold(str(row["speaker"])) for row in rows]


def _format_note(nick: str, note: str) -> str:
    return (
        f"Your relationship with {nick}: {note} Let it shape how you treat them "
        "when it matters; do not recite it or bring it up for its own sake."
    )


class Module:
    async def on_message(self, _ctx: ModuleContext) -> None:
        return None

    async def before_prompt(self, ctx: ModuleContext) -> None:
        settings = _settings(ctx)
        if not settings.people:
            return
        # Priority: who is talking now, who is named, then who spoke lately.
        ordered = [irc_casefold(ctx.message.nick)]
        ordered.extend(
            folded for folded, (nick, _note) in settings.people.items()
            if mentions_nick(ctx.message.body, nick)
        )
        ordered.extend(await _recent_speakers(ctx, settings.lookback_lines))
        own_nick = irc_casefold(ctx.bot_nick or ctx.bottle.irc.nick)
        chosen: list[str] = []
        for folded in ordered:
            if folded in settings.people and folded != own_nick and folded not in chosen:
                chosen.append(folded)
            if len(chosen) == MAX_NOTES_PER_PROMPT:
                break
        for folded in chosen:
            nick, note = settings.people[folded]
            ctx.prompt_sections.append(_format_note(nick, note))

    async def after_response(self, _ctx: ModuleContext) -> None:
        return None

    async def nightly(self, _ctx: NightlyContext) -> None:
        return None


async def list_relationship_notes(
    db: aiosqlite.Connection, *, bottle_id: int,
) -> list[tuple[str, str]]:
    """``(nick, note)`` pairs in the Bottle's stored settings, sorted by nick."""
    raw = (await module_settings(db, bottle_id=bottle_id)).get("relationships", {})
    return sorted(_parse_people(raw).values(), key=lambda item: irc_casefold(item[0]))


async def update_relationship_note(
    db: aiosqlite.Connection, *, bottle_id: int, nick: str, note: str | None,
    actor: str,
) -> bool:
    """Set (or with ``note=None`` remove) one person's note, keeping the rest.

    Nicks match IRC-case-insensitively, so updating ``bork`` replaces a
    stored ``Bork`` entry instead of adding a second one. Every other setting
    and note is preserved, and the change is audited like module-settings.
    """
    nick = nick.strip()
    if not nick or any(character.isspace() for character in nick):
        raise ValueError("nick must be a single word")
    raw = dict((await module_settings(db, bottle_id=bottle_id)).get("relationships", {}))
    people = raw.get("people", {})
    if not isinstance(people, dict):
        raise ValueError("stored relationships people is not an object; fix it with module-settings")
    folded = irc_casefold(nick)
    kept = {key: value for key, value in people.items() if irc_casefold(str(key)) != folded}
    if note is None:
        if len(kept) == len(people):
            return False
        raw["people"] = kept
    else:
        raw["people"] = {**kept, nick: " ".join(note.split())}
    _parse_people(raw)  # same validation the running module applies
    await set_module_settings(
        db, bottle_id=bottle_id, module_name="relationships", settings=raw, actor=actor,
    )
    return True
