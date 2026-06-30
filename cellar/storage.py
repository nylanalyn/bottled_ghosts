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
                  i.realname, i.channels, i.password, i.sasl_username,
                  i.sasl_password, l.endpoint, l.model,
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
            password=row["password"], sasl_username=row["sasl_username"],
            sasl_password=row["sasl_password"]),
        llm=LLMProfile(endpoint=row["endpoint"], model=row["model"],
            api_key=row["api_key"], temperature=row["temperature"], max_tokens=row["max_tokens"]),
    )


async def create_bottle(
    db: aiosqlite.Connection,
    *,
    name: str,
    soul_prompt_path: Path,
    irc: IRCProfile,
    llm: LLMProfile,
    max_lines: int = 2,
    max_chars: int = 400,
    cooldown_seconds: float = 1.0,
) -> int:
    """Persist one complete Bottle configuration as a visible transaction."""
    try:
        irc_cursor = await db.execute(
            """INSERT INTO irc_profiles(
                   network, host, port, tls, nick, username, realname, channels, password,
                   sasl_username, sasl_password
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (irc.network, irc.host, irc.port, irc.tls, irc.nick, irc.username,
             irc.realname, json.dumps(irc.channels), irc.password,
             irc.sasl_username, irc.sasl_password),
        )
        llm_cursor = await db.execute(
            """INSERT INTO llm_profiles(
                   endpoint, model, api_key, temperature, max_tokens
               ) VALUES (?, ?, ?, ?, ?)""",
            (llm.endpoint, llm.model, llm.api_key, llm.temperature, llm.max_tokens),
        )
        bottle_cursor = await db.execute(
            """INSERT INTO bots(
                   name, soul_prompt_path, llm_profile_id, irc_profile_id,
                   max_lines, max_chars, cooldown_seconds
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, str(soul_prompt_path), llm_cursor.lastrowid, irc_cursor.lastrowid,
             max_lines, max_chars, cooldown_seconds),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    if bottle_cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a Bottle id")
    return bottle_cursor.lastrowid


async def set_sasl_credentials(
    db: aiosqlite.Connection, *, bottle_id: int, username: str, password: str
) -> None:
    cursor = await db.execute(
        """UPDATE irc_profiles SET sasl_username = ?, sasl_password = ?
           WHERE id = (SELECT irc_profile_id FROM bots WHERE id = ?)""",
        (username, password, bottle_id),
    )
    if cursor.rowcount != 1:
        await db.rollback()
        raise LookupError(f"Bottle {bottle_id} does not exist")
    await db.commit()


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
