from uuid import uuid4

import aiosqlite

from cellar.models import IncomingIRCMessage


async def resolve_user(
    db: aiosqlite.Connection, *, network: str, identity: IncomingIRCMessage
) -> str:
    """Resolve strongest available IRC identity, creating a UUID when unknown."""
    row = None
    confidence = 0.5
    if identity.account:
        row = await _first(
            db,
            """SELECT user_id FROM user_identities
               WHERE network = ? AND account = ? COLLATE NOCASE
               ORDER BY last_seen DESC LIMIT 1""",
            (network, identity.account),
        )
        confidence = 1.0
    if row is None and identity.hostmask:
        row = await _first(
            db,
            """SELECT user_id FROM user_identities
               WHERE network = ? AND hostmask = ? COLLATE NOCASE
               ORDER BY last_seen DESC LIMIT 1""",
            (network, identity.hostmask),
        )
        confidence = 0.8
    if row is None:
        account_clause = "AND account IS NULL" if identity.account else ""
        row = await _first(
            db,
            f"""SELECT user_id FROM user_identities
                WHERE network = ? AND nick = ? COLLATE NOCASE {account_clause}
                ORDER BY last_seen DESC LIMIT 1""",
            (network, identity.nick),
        )
        confidence = 0.5

    user_id = str(row["user_id"]) if row else str(uuid4())
    if row is None:
        await db.execute(
            "INSERT INTO users(id, canonical_name) VALUES (?, ?)",
            (user_id, identity.nick),
        )

    exact = await _first(
        db,
        """SELECT id FROM user_identities
           WHERE user_id = ? AND network = ? AND nick = ? COLLATE NOCASE
             AND COALESCE(account, '') = COALESCE(?, '') COLLATE NOCASE
             AND COALESCE(hostmask, '') = COALESCE(?, '') COLLATE NOCASE
           LIMIT 1""",
        (user_id, network, identity.nick, identity.account, identity.hostmask),
    )
    if exact:
        await db.execute(
            "UPDATE user_identities SET last_seen = CURRENT_TIMESTAMP, confidence = ? WHERE id = ?",
            (confidence, exact["id"]),
        )
    else:
        await db.execute(
            """INSERT INTO user_identities(
                   user_id, network, nick, account, hostmask, confidence
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, network, identity.nick, identity.account, identity.hostmask, confidence),
        )
    await db.commit()
    return user_id


async def merge_users(db: aiosqlite.Connection, *, keep_id: str, merge_id: str) -> None:
    """Explicitly merge a duplicate UUID into the canonical UUID."""
    if keep_id == merge_id:
        return
    try:
        cursor = await db.execute("SELECT 1 FROM users WHERE id = ?", (keep_id,))
        if await cursor.fetchone() is None:
            raise LookupError(f"user {keep_id} does not exist")
        cursor = await db.execute("SELECT 1 FROM users WHERE id = ?", (merge_id,))
        if await cursor.fetchone() is None:
            raise LookupError(f"user {merge_id} does not exist")
        await db.execute("UPDATE user_identities SET user_id = ? WHERE user_id = ?", (keep_id, merge_id))
        await db.execute("UPDATE messages SET user_id = ? WHERE user_id = ?", (keep_id, merge_id))
        await db.execute(
            "UPDATE memory_candidates SET user_id = ? WHERE user_id = ?", (keep_id, merge_id)
        )
        await db.execute("UPDATE user_memories SET user_id = ? WHERE user_id = ?", (keep_id, merge_id))
        await db.execute("DELETE FROM users WHERE id = ?", (merge_id,))
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def _first(
    db: aiosqlite.Connection, query: str, parameters: tuple[object, ...]
) -> aiosqlite.Row | None:
    return await (await db.execute(query, parameters)).fetchone()
