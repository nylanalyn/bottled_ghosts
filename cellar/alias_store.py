import json
import re

import aiosqlite

from cellar.irc import IRC_NICK_CHARACTERS, irc_casefold

ALIAS_PATTERN = re.compile(rf"^[{IRC_NICK_CHARACTERS}]+$")


async def list_aliases(db: aiosqlite.Connection, *, bottle_id: int) -> list[str]:
    rows = await (await db.execute(
        "SELECT alias FROM bot_aliases WHERE bot_id = ? ORDER BY alias_key",
        (bottle_id,),
    )).fetchall()
    return [str(row["alias"]) for row in rows]


async def add_alias(
    db: aiosqlite.Connection, *, bottle_id: int, alias: str, actor: str = "operator",
) -> bool:
    alias = alias.strip()
    actor = actor.strip()
    if not actor:
        raise ValueError("configuration actor cannot be empty")
    if not alias or not ALIAS_PATTERN.fullmatch(alias):
        raise ValueError("alias must use valid IRC nickname characters")
    alias_key = irc_casefold(alias)
    try:
        await db.execute("BEGIN IMMEDIATE")
        bottle = await (await db.execute(
            """SELECT i.nick FROM bots b JOIN irc_profiles i ON i.id = b.irc_profile_id
               WHERE b.id = ?""",
            (bottle_id,),
        )).fetchone()
        if bottle is None:
            raise LookupError(f"Bottle {bottle_id} does not exist")
        if irc_casefold(str(bottle["nick"])) == alias_key:
            raise ValueError("alias duplicates the Bottle's IRC nickname")
        cursor = await db.execute(
            "INSERT OR IGNORE INTO bot_aliases(bot_id, alias, alias_key) VALUES (?, ?, ?)",
            (bottle_id, alias, alias_key),
        )
        if cursor.rowcount == 0:
            await db.commit()
            return False
        await db.execute(
            """INSERT INTO configuration_events(
                   bot_id, actor, changed_fields, new_value
               ) VALUES (?, ?, 'alias:add', ?)""",
            (bottle_id, actor, json.dumps(alias)),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def delete_alias(
    db: aiosqlite.Connection, *, bottle_id: int, alias: str, actor: str = "operator",
) -> bool:
    actor = actor.strip()
    if not actor:
        raise ValueError("configuration actor cannot be empty")
    alias_key = irc_casefold(alias.strip())
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute(
            "SELECT alias FROM bot_aliases WHERE bot_id = ? AND alias_key = ?",
            (bottle_id, alias_key),
        )).fetchone()
        if row is None:
            await db.commit()
            return False
        stored_alias = str(row["alias"])
        await db.execute(
            "DELETE FROM bot_aliases WHERE bot_id = ? AND alias_key = ?",
            (bottle_id, alias_key),
        )
        await db.execute(
            """INSERT INTO configuration_events(
                   bot_id, actor, changed_fields, old_value
               ) VALUES (?, ?, 'alias:delete', ?)""",
            (bottle_id, actor, json.dumps(stored_alias)),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise
