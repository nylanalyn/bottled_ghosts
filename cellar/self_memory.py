"""What a Bottle has said about itself, kept so it stays consistent.

Self-memories come from the recollection job: while summarizing a closed
public-channel chunk, the model also lists durable things the Bottle said
about itself. They are stored automatically, without an operator review
queue, because they only describe the character's own voice. They never
become facts about users. Operators can list and archive them.
"""

import json
import re

import aiosqlite
from pydantic import BaseModel

from cellar.storage import exact_search_query

SELF_MEMORY_TYPES = ("preference", "project", "relationship", "identity")
MAX_SELF_NOTES_PER_CHUNK = 3
MAX_SELF_NOTE_CHARS = 200


class SelfNote(BaseModel):
    text: str
    type: str = "identity"


def normalize_self_note(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def usable_self_notes(notes: list[SelfNote]) -> list[SelfNote]:
    """Keep well-formed notes only; a bad note never costs the whole chunk."""
    usable: list[SelfNote] = []
    for note in notes:
        text = " ".join(note.text.split())
        kind = note.type.strip().casefold()
        if not text or len(text) > MAX_SELF_NOTE_CHARS or kind not in SELF_MEMORY_TYPES:
            continue
        if not normalize_self_note(text):
            continue
        usable.append(SelfNote(text=text, type=kind))
        if len(usable) == MAX_SELF_NOTES_PER_CHUNK:
            break
    return usable


async def store_self_notes(
    db: aiosqlite.Connection, *, bot_id: int, recollection_id: int,
    said_at: str, notes: list[SelfNote],
) -> None:
    """Upsert notes inside the caller's transaction.

    A repeated statement bumps its count and date. An operator-archived note
    stays archived even if the Bottle says it again.
    """
    for note in notes:
        await db.execute(
            """INSERT INTO self_memories(
                   bot_id, memory_type, text, normalized_text, recollection_id,
                   first_said_at, last_said_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(bot_id, normalized_text) DO UPDATE SET
                   times_said = times_said + 1,
                   last_said_at = MAX(last_said_at, excluded.last_said_at),
                   recollection_id = excluded.recollection_id""",
            (bot_id, note.type, note.text, normalize_self_note(note.text),
             recollection_id, said_at, said_at),
        )


async def relevant_self_memories(
    db: aiosqlite.Connection, *, bot_id: int, query_text: str, limit: int = 3,
) -> list[str]:
    query = exact_search_query(query_text)
    if query is None:
        return []
    rows = await (await db.execute(
        """SELECT s.last_said_at, s.text FROM self_memories_fts f
           JOIN self_memories s ON s.id = f.rowid
           WHERE self_memories_fts MATCH ? AND s.bot_id = ? AND s.state = 'active'
           ORDER BY bm25(self_memories_fts), s.last_said_at DESC LIMIT ?""",
        (query, bot_id, limit),
    )).fetchall()
    return [f"{str(row['last_said_at'])[:10]}: {row['text']}" for row in rows]


async def list_self_memories(
    db: aiosqlite.Connection, *, bot_id: int, include_archived: bool = False,
    limit: int = 50,
) -> list[aiosqlite.Row]:
    return list(await (await db.execute(
        """SELECT id, memory_type, text, times_said, first_said_at, last_said_at,
                  state, recollection_id
           FROM self_memories WHERE bot_id = ? AND (? OR state = 'active')
           ORDER BY last_said_at DESC, id DESC LIMIT ?""",
        (bot_id, include_archived, limit),
    )).fetchall())


async def archive_self_memory(
    db: aiosqlite.Connection, *, self_memory_id: int, actor: str,
) -> None:
    if not actor.strip():
        raise ValueError("actor cannot be empty")
    try:
        await db.execute("BEGIN IMMEDIATE")
        result = await db.execute(
            """UPDATE self_memories SET state = 'archived', archived_at = CURRENT_TIMESTAMP
               WHERE id = ? AND state = 'active'""", (self_memory_id,),
        )
        if result.rowcount != 1:
            raise LookupError("active self-memory not found")
        await db.execute(
            """INSERT INTO maintenance_events(actor, action, details)
               VALUES (?, 'self_memory:archive', ?)""",
            (actor.strip(), json.dumps({"self_memory_id": self_memory_id})),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
