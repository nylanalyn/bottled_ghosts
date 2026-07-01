import json

import aiosqlite

from cellar.irc import irc_casefold
from cellar.models import IRCProfile


async def set_alternate_nicks(
    db: aiosqlite.Connection, *, bottle_id: int, nicks: list[str], actor: str = "operator",
) -> bool:
    actor = actor.strip()
    if not actor:
        raise ValueError("configuration actor cannot be empty")
    row = await (await db.execute(
        """SELECT i.nick, i.alternate_nicks FROM bots b
           JOIN irc_profiles i ON i.id = b.irc_profile_id WHERE b.id = ?""",
        (bottle_id,),
    )).fetchone()
    if row is None:
        raise LookupError(f"Bottle {bottle_id} does not exist")
    normalized = [nick.strip() for nick in nicks if nick.strip()]
    IRCProfile(
        network="validation", host="validation", nick=str(row["nick"]),
        username="validation", realname="validation", channels=["#validation"],
        alternate_nicks=normalized,
    )
    # Enforce RFC1459 equivalence in addition to the model's basic validation.
    folded = [irc_casefold(str(row["nick"])), *(irc_casefold(nick) for nick in normalized)]
    if len(set(folded)) != len(folded):
        raise ValueError("alternate nicks must be unique and differ from the primary nick")
    old = json.loads(str(row["alternate_nicks"]))
    if old == normalized:
        return False
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """UPDATE irc_profiles SET alternate_nicks = ?
               WHERE id = (SELECT irc_profile_id FROM bots WHERE id = ?)""",
            (json.dumps(normalized), bottle_id),
        )
        await db.execute(
            """INSERT INTO configuration_events(
                   bot_id, actor, changed_fields, old_value, new_value
               ) VALUES (?, ?, 'alternate_nicks', ?, ?)""",
            (bottle_id, actor, json.dumps(old), json.dumps(normalized)),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return True
