import json

import aiosqlite


async def set_module_enabled(
    db: aiosqlite.Connection, *, bottle_id: int, module_name: str, enabled: bool,
    actor: str = "operator",
) -> bool:
    actor = actor.strip()
    if not actor:
        raise ValueError("configuration actor cannot be empty")
    try:
        await db.execute("BEGIN IMMEDIATE")
        bottle = await (await db.execute(
            "SELECT 1 FROM bots WHERE id = ?", (bottle_id,)
        )).fetchone()
        if bottle is None:
            raise LookupError(f"Bottle {bottle_id} does not exist")
        row = await (await db.execute(
            "SELECT enabled FROM bot_modules WHERE bot_id = ? AND module_name = ?",
            (bottle_id, module_name),
        )).fetchone()
        old_value = bool(row["enabled"]) if row is not None else False
        if old_value == enabled:
            await db.commit()
            return False
        await db.execute(
            """INSERT INTO bot_modules(bot_id, module_name, enabled)
               VALUES (?, ?, ?)
               ON CONFLICT(bot_id, module_name) DO UPDATE SET enabled = excluded.enabled""",
            (bottle_id, module_name, enabled),
        )
        await db.execute(
            """INSERT INTO configuration_events(
                   bot_id, actor, changed_fields, old_value, new_value
               ) VALUES (?, ?, ?, ?, ?)""",
            (bottle_id, actor, f"module:{module_name}:enabled",
             json.dumps(old_value), json.dumps(enabled)),
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        raise


async def module_states(db: aiosqlite.Connection, *, bottle_id: int) -> dict[str, bool]:
    cursor = await db.execute(
        "SELECT module_name, enabled FROM bot_modules WHERE bot_id = ? ORDER BY module_name",
        (bottle_id,),
    )
    return {str(row["module_name"]): bool(row["enabled"]) for row in await cursor.fetchall()}


async def module_settings(
    db: aiosqlite.Connection, *, bottle_id: int
) -> dict[str, dict[str, object]]:
    cursor = await db.execute(
        "SELECT module_name, settings_json FROM bot_modules WHERE bot_id = ? ORDER BY module_name",
        (bottle_id,),
    )
    settings: dict[str, dict[str, object]] = {}
    for row in await cursor.fetchall():
        value = json.loads(row["settings_json"])
        if not isinstance(value, dict):
            raise ValueError(f"settings for module {row['module_name']} are not a JSON object")
        settings[str(row["module_name"])] = value
    return settings


async def set_module_settings(
    db: aiosqlite.Connection, *, bottle_id: int, module_name: str,
    settings: dict[str, object], actor: str,
) -> None:
    actor = actor.strip()
    if not actor:
        raise ValueError("configuration actor cannot be empty")
    encoded = json.dumps(settings, sort_keys=True, separators=(",", ":"))
    try:
        await db.execute("BEGIN IMMEDIATE")
        bottle = await (await db.execute(
            "SELECT 1 FROM bots WHERE id = ?", (bottle_id,)
        )).fetchone()
        if bottle is None:
            raise LookupError(f"Bottle {bottle_id} does not exist")
        current = await (await db.execute(
            "SELECT settings_json FROM bot_modules WHERE bot_id = ? AND module_name = ?",
            (bottle_id, module_name),
        )).fetchone()
        old_encoded = str(current["settings_json"]) if current is not None else "{}"
        if old_encoded == encoded:
            await db.commit()
            return
        await db.execute(
            """INSERT INTO bot_modules(bot_id, module_name, enabled, settings_json)
               VALUES (?, ?, 0, ?)
               ON CONFLICT(bot_id, module_name)
               DO UPDATE SET settings_json = excluded.settings_json""",
            (bottle_id, module_name, encoded),
        )
        await db.execute(
            """INSERT INTO configuration_events(
                   bot_id, actor, changed_fields, old_value, new_value
               ) VALUES (?, ?, ?, ?, ?)""",
            (bottle_id, actor, f"module:{module_name}:settings", old_encoded, encoded),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
