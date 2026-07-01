import json
from typing import Literal, cast

import aiosqlite

from cellar.irc import irc_casefold
from cellar.models import IgnoreRule, IncomingIRCMessage

IgnoreAction = Literal["drop", "no_response"]
MatchType = Literal["account", "hostmask", "nick"]


async def matching_ignore_action(
    db: aiosqlite.Connection, *, bottle_id: int, network: str,
    identity: IncomingIRCMessage,
) -> IgnoreAction | None:
    cursor = await db.execute(
        """SELECT match_type, match_value, action FROM irc_ignore_rules
           WHERE bot_id = ? AND network = ? COLLATE NOCASE
           ORDER BY CASE action WHEN 'drop' THEN 0 ELSE 1 END, id""",
        (bottle_id, network),
    )
    observed = {
        "account": identity.account,
        "hostmask": identity.hostmask,
        "nick": identity.nick,
    }
    for row in await cursor.fetchall():
        value = observed[str(row["match_type"])]
        if value is not None and irc_casefold(value) == irc_casefold(row["match_value"]):
            return cast(IgnoreAction, str(row["action"]))
    return None


async def list_ignore_rules(
    db: aiosqlite.Connection, *, bottle_id: int
) -> list[IgnoreRule]:
    cursor = await db.execute(
        "SELECT * FROM irc_ignore_rules WHERE bot_id = ? ORDER BY network, id",
        (bottle_id,),
    )
    return [IgnoreRule(**dict(row)) for row in await cursor.fetchall()]


async def add_ignore_rule(
    db: aiosqlite.Connection, *, bottle_id: int, network: str,
    match_type: MatchType, match_value: str, action: IgnoreAction,
    actor: str = "operator",
) -> tuple[int, bool]:
    network = irc_casefold(network.strip())
    match_value = irc_casefold(match_value.strip())
    actor = actor.strip()
    if not network or not match_value:
        raise ValueError("ignore network and match value are required")
    if not actor:
        raise ValueError("configuration actor cannot be empty")
    try:
        await db.execute("BEGIN IMMEDIATE")
        bottle = await (await db.execute(
            "SELECT 1 FROM bots WHERE id = ?", (bottle_id,)
        )).fetchone()
        if bottle is None:
            raise LookupError(f"Bottle {bottle_id} does not exist")
        existing_rows = await (await db.execute(
            """SELECT id, match_value FROM irc_ignore_rules
               WHERE bot_id = ? AND network = ? COLLATE NOCASE
                 AND match_type = ? AND action = ?""",
            (bottle_id, network, match_type, action),
        )).fetchall()
        for existing in existing_rows:
            if irc_casefold(existing["match_value"]) == irc_casefold(match_value):
                await db.commit()
                return int(existing["id"]), False
        cursor = await db.execute(
            """INSERT INTO irc_ignore_rules(
                   bot_id, network, match_type, match_value, action
               ) VALUES (?, ?, ?, ?, ?)""",
            (bottle_id, network, match_type, match_value, action),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an ignore rule id")
        rule_id = cursor.lastrowid
        await db.execute(
            """INSERT INTO configuration_events(
                   bot_id, actor, changed_fields, new_value
               ) VALUES (?, ?, 'ignore_rule:added', ?)""",
            (bottle_id, actor, json.dumps({
                "id": rule_id, "network": network, "match_type": match_type,
                "match_value": match_value, "action": action,
            }, sort_keys=True)),
        )
        await db.commit()
        return rule_id, True
    except Exception:
        await db.rollback()
        raise


async def delete_ignore_rule(
    db: aiosqlite.Connection, *, bottle_id: int, rule_id: int,
    actor: str = "operator",
) -> None:
    actor = actor.strip()
    if not actor:
        raise ValueError("configuration actor cannot be empty")
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute(
            "SELECT * FROM irc_ignore_rules WHERE id = ? AND bot_id = ?",
            (rule_id, bottle_id),
        )).fetchone()
        if row is None:
            raise LookupError(f"ignore rule {rule_id} does not exist for Bottle {bottle_id}")
        await db.execute("DELETE FROM irc_ignore_rules WHERE id = ?", (rule_id,))
        await db.execute(
            """INSERT INTO configuration_events(
                   bot_id, actor, changed_fields, old_value
               ) VALUES (?, ?, 'ignore_rule:deleted', ?)""",
            (bottle_id, actor, json.dumps({
                "id": row["id"], "network": row["network"],
                "match_type": row["match_type"], "match_value": row["match_value"],
                "action": row["action"],
            }, sort_keys=True)),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
