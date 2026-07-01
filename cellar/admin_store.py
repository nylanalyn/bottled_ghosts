import json

import aiosqlite


async def admin_api_token(db: aiosqlite.Connection, *, bottle_id: int) -> str | None:
    row = await (await db.execute(
        "SELECT token FROM admin_api_credentials WHERE bot_id = ?", (bottle_id,),
    )).fetchone()
    return None if row is None else str(row["token"])


async def set_admin_api_token(
    db: aiosqlite.Connection, *, bottle_id: int, token: str,
    actor: str = "operator",
) -> bool:
    token = token.strip()
    actor = actor.strip()
    if not token:
        raise ValueError("admin API token is required")
    if not actor:
        raise ValueError("configuration actor cannot be empty")
    try:
        await db.execute("BEGIN IMMEDIATE")
        exists = await (await db.execute(
            "SELECT 1 FROM bots WHERE id = ?", (bottle_id,),
        )).fetchone()
        if exists is None:
            raise LookupError(f"Bottle {bottle_id} does not exist")
        current = await admin_api_token(db, bottle_id=bottle_id)
        if current == token:
            await db.commit()
            return False
        await db.execute(
            """INSERT INTO admin_api_credentials(bot_id, token, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(bot_id) DO UPDATE SET
                   token = excluded.token, updated_at = CURRENT_TIMESTAMP""",
            (bottle_id, token),
        )
        await db.execute(
            """INSERT INTO configuration_events(bot_id, actor, changed_fields)
               VALUES (?, ?, 'admin_api_token')""",
            (bottle_id, actor),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def response_enabled(db: aiosqlite.Connection, *, bottle_id: int) -> bool:
    row = await (await db.execute(
        "SELECT response_enabled FROM bot_runtime_control WHERE bot_id = ?",
        (bottle_id,),
    )).fetchone()
    return True if row is None else bool(row["response_enabled"])


async def set_response_enabled(
    db: aiosqlite.Connection, *, bottle_id: int, enabled: bool,
    actor: str = "discord-admin",
) -> bool:
    actor = actor.strip()
    if not actor:
        raise ValueError("control actor cannot be empty")
    try:
        await db.execute("BEGIN IMMEDIATE")
        exists = await (await db.execute(
            "SELECT 1 FROM bots WHERE id = ?", (bottle_id,),
        )).fetchone()
        if exists is None:
            raise LookupError(f"Bottle {bottle_id} does not exist")
        old = await response_enabled(db, bottle_id=bottle_id)
        if old == enabled:
            await db.commit()
            return False
        await db.execute(
            """INSERT INTO bot_runtime_control(bot_id, response_enabled, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(bot_id) DO UPDATE SET
                   response_enabled = excluded.response_enabled,
                   updated_at = CURRENT_TIMESTAMP""",
            (bottle_id, enabled),
        )
        await db.execute(
            """INSERT INTO configuration_events(
                   bot_id, actor, changed_fields, old_value, new_value
               ) VALUES (?, ?, 'response_enabled', ?, ?)""",
            (bottle_id, actor, json.dumps(old), json.dumps(enabled)),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def enqueue_admin_event(
    db: aiosqlite.Connection, *, bottle_id: int, event_type: str,
    message: str, source_message_id: int | None = None,
) -> bool:
    cursor = await db.execute(
        """INSERT OR IGNORE INTO admin_events(
               bot_id, event_type, message, source_message_id
           ) VALUES (?, ?, ?, ?)""",
        (bottle_id, event_type, message, source_message_id),
    )
    await db.commit()
    return cursor.rowcount == 1


async def consume_admin_events(
    db: aiosqlite.Connection, *, bottle_id: int, since: int = 0,
) -> list[dict[str, object]]:
    try:
        await db.execute("BEGIN IMMEDIATE")
        rows = await (await db.execute(
            """SELECT id, message FROM admin_events
               WHERE bot_id = ? AND id > ? AND delivered_at IS NULL
               ORDER BY id LIMIT 100""",
            (bottle_id, since),
        )).fetchall()
        if rows:
            placeholders = ",".join("?" for _ in rows)
            await db.execute(
                f"UPDATE admin_events SET delivered_at = CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders})",
                tuple(int(row["id"]) for row in rows),
            )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return [{"id": int(row["id"]), "message": str(row["message"])} for row in rows]
