import aiosqlite


async def set_module_enabled(
    db: aiosqlite.Connection, *, bottle_id: int, module_name: str, enabled: bool
) -> None:
    bottle = await (await db.execute("SELECT 1 FROM bots WHERE id = ?", (bottle_id,))).fetchone()
    if bottle is None:
        raise LookupError(f"Bottle {bottle_id} does not exist")
    await db.execute(
        """INSERT INTO bot_modules(bot_id, module_name, enabled)
           VALUES (?, ?, ?)
           ON CONFLICT(bot_id, module_name) DO UPDATE SET enabled = excluded.enabled""",
        (bottle_id, module_name, enabled),
    )
    await db.commit()


async def module_states(db: aiosqlite.Connection, *, bottle_id: int) -> dict[str, bool]:
    cursor = await db.execute(
        "SELECT module_name, enabled FROM bot_modules WHERE bot_id = ? ORDER BY module_name",
        (bottle_id,),
    )
    return {str(row["module_name"]): bool(row["enabled"]) for row in await cursor.fetchall()}
