import json
from pathlib import Path

import aiosqlite
from pydantic import BaseModel, Field


class BottleSettings(BaseModel):
    id: int
    name: str = Field(min_length=1)
    soul_prompt_path: Path
    network: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    tls: bool
    nick: str = Field(min_length=1)
    username: str = Field(min_length=1)
    realname: str = Field(min_length=1)
    channels: list[str] = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1)
    max_lines: int = Field(ge=1)
    max_chars: int = Field(ge=1, le=450)
    cooldown_seconds: float = Field(ge=0)


async def load_bottle_settings(
    db: aiosqlite.Connection, *, bottle_id: int
) -> BottleSettings:
    row = await (await db.execute(
        """SELECT b.id, b.name, b.soul_prompt_path, b.max_lines, b.max_chars,
                  b.cooldown_seconds, i.network, i.host, i.port, i.tls, i.nick,
                  i.username, i.realname, i.channels, l.endpoint, l.model,
                  l.temperature, l.max_tokens
           FROM bots b JOIN irc_profiles i ON i.id = b.irc_profile_id
           JOIN llm_profiles l ON l.id = b.llm_profile_id WHERE b.id = ?""",
        (bottle_id,),
    )).fetchone()
    if row is None:
        raise LookupError(f"Bottle {bottle_id} does not exist")
    values = dict(row)
    values["tls"] = bool(values["tls"])
    values["channels"] = json.loads(values["channels"])
    return BottleSettings(**values)


async def save_bottle_settings(
    db: aiosqlite.Connection, *, settings: BottleSettings, actor: str
) -> bool:
    actor = actor.strip()
    if not actor:
        raise ValueError("configuration actor cannot be empty")
    current = await load_bottle_settings(db, bottle_id=settings.id)
    changed = sorted(
        field for field in BottleSettings.model_fields
        if field != "id" and getattr(current, field) != getattr(settings, field)
    )
    if not changed:
        return False
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """UPDATE bots SET name = ?, soul_prompt_path = ?, max_lines = ?,
                   max_chars = ?, cooldown_seconds = ? WHERE id = ?""",
            (settings.name, str(settings.soul_prompt_path), settings.max_lines,
             settings.max_chars, settings.cooldown_seconds, settings.id),
        )
        await db.execute(
            """UPDATE irc_profiles SET network = ?, host = ?, port = ?, tls = ?, nick = ?,
                   username = ?, realname = ?, channels = ?
               WHERE id = (SELECT irc_profile_id FROM bots WHERE id = ?)""",
            (settings.network, settings.host, settings.port, settings.tls, settings.nick,
             settings.username, settings.realname, json.dumps(settings.channels), settings.id),
        )
        await db.execute(
            """UPDATE llm_profiles SET endpoint = ?, model = ?, temperature = ?, max_tokens = ?
               WHERE id = (SELECT llm_profile_id FROM bots WHERE id = ?)""",
            (settings.endpoint, settings.model, settings.temperature,
             settings.max_tokens, settings.id),
        )
        await db.execute(
            """INSERT INTO configuration_events(bot_id, actor, changed_fields)
               VALUES (?, ?, ?)""", (settings.id, actor, ",".join(changed)),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return True
