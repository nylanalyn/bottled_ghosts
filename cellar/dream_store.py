import aiosqlite

from cellar.models import DreamSummary


async def dream_window(
    db: aiosqlite.Connection, *, hours: int
) -> tuple[str, str]:
    row = await (await db.execute(
        "SELECT datetime('now', ?), CURRENT_TIMESTAMP", (f"-{hours} hours",)
    )).fetchone()
    if row is None:
        raise RuntimeError("SQLite did not return a dream window")
    return str(row[0]), str(row[1])


async def messages_for_dream(
    db: aiosqlite.Connection,
    *,
    bot_id: int,
    period_start: str,
    period_end: str,
    limit: int = 200,
) -> list[tuple[str, str, str, str]]:
    cursor = await db.execute(
        """SELECT timestamp, channel, speaker, body FROM (
               SELECT id, timestamp, channel, speaker, body FROM messages
               WHERE bot_id = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY id DESC LIMIT ?
           ) ORDER BY id""",
        (bot_id, period_start, period_end, limit),
    )
    return [
        (str(row[0]), str(row[1]), str(row[2]), str(row[3]))
        for row in await cursor.fetchall()
    ]


async def store_dream(
    db: aiosqlite.Connection,
    *,
    bot_id: int,
    period_start: str,
    period_end: str,
    summary: str,
) -> DreamSummary:
    cursor = await db.execute(
        """INSERT INTO summaries(bot_id, period_start, period_end, summary)
           VALUES (?, ?, ?, ?)""", (bot_id, period_start, period_end, summary),
    )
    await db.commit()
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a summary id")
    row = await (await db.execute(
        "SELECT * FROM summaries WHERE id = ?", (cursor.lastrowid,)
    )).fetchone()
    if row is None:
        raise RuntimeError("stored dream could not be reloaded")
    return DreamSummary(**dict(row))


async def recent_dream_texts(
    db: aiosqlite.Connection, *, bot_id: int, limit: int = 3
) -> list[str]:
    cursor = await db.execute(
        """SELECT period_start, period_end, summary FROM summaries
           WHERE bot_id = ? ORDER BY period_end DESC, id DESC LIMIT ?""", (bot_id, limit),
    )
    return [
        f"{row['period_start']} through {row['period_end']}: {row['summary']}"
        for row in await cursor.fetchall()
    ]


async def list_dreams(
    db: aiosqlite.Connection, *, bot_id: int, limit: int = 20
) -> list[DreamSummary]:
    cursor = await db.execute(
        """SELECT * FROM summaries WHERE bot_id = ?
           ORDER BY period_end DESC, id DESC LIMIT ?""", (bot_id, limit),
    )
    return [DreamSummary(**dict(row)) for row in await cursor.fetchall()]
