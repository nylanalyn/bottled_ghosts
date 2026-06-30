import json
from pathlib import Path

import aiosqlite

from cellar.migrations import migrate
from cellar.models import Bottle, IRCMessage, IRCProfile, LLMProfile


async def open_database(path: Path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await migrate(db)
    return db


async def load_bottle(db: aiosqlite.Connection, bottle_id: int) -> Bottle:
    cursor = await db.execute(
        """SELECT b.*, i.network, i.host, i.port, i.tls, i.nick, i.username,
                  i.realname, i.channels, i.password, l.endpoint, l.model,
                  l.api_key, l.temperature, l.max_tokens
           FROM bots b JOIN irc_profiles i ON i.id = b.irc_profile_id
           JOIN llm_profiles l ON l.id = b.llm_profile_id
           WHERE b.id = ? AND b.enabled = 1""",
        (bottle_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise LookupError(f"enabled bottle {bottle_id} does not exist")
    return Bottle(
        id=row["id"], name=row["name"], soul_prompt_path=Path(row["soul_prompt_path"]),
        max_lines=row["max_lines"], max_chars=row["max_chars"],
        cooldown_seconds=row["cooldown_seconds"],
        irc=IRCProfile(network=row["network"], host=row["host"], port=row["port"],
            tls=bool(row["tls"]), nick=row["nick"], username=row["username"],
            realname=row["realname"], channels=json.loads(row["channels"]),
            password=row["password"]),
        llm=LLMProfile(endpoint=row["endpoint"], model=row["model"],
            api_key=row["api_key"], temperature=row["temperature"], max_tokens=row["max_tokens"]),
    )


async def log_message(db: aiosqlite.Connection, message: IRCMessage) -> None:
    await db.execute(
        "INSERT INTO messages(network, channel, speaker, body, bot_id) VALUES (?, ?, ?, ?, ?)",
        (message.network, message.channel, message.speaker, message.body, message.bot_id),
    )
    await db.commit()


async def recent_messages(
    db: aiosqlite.Connection, *, bot_id: int, network: str, channel: str, limit: int = 20
) -> list[tuple[str, str]]:
    cursor = await db.execute(
        "SELECT speaker, body FROM messages WHERE bot_id = ? AND network = ? AND channel = ? "
        "ORDER BY id DESC LIMIT ?", (bot_id, network, channel, limit),
    )
    rows = await cursor.fetchall()
    return [(row["speaker"], row["body"]) for row in reversed(rows)]
