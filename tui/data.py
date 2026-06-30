import json

import aiosqlite
from pydantic import BaseModel


class DashboardBottle(BaseModel):
    id: int
    name: str
    enabled: bool
    extract_memories: bool
    network: str
    nick: str
    channels: list[str]
    pending_candidates: int
    last_activity: str | None
    enabled_modules: str | None


class DashboardMessage(BaseModel):
    timestamp: str
    channel: str
    speaker: str
    body: str


class DashboardAuditEvent(BaseModel):
    event_key: str
    created_at: str
    actor: str
    category: str
    action: str
    target: str
    details: str


async def dashboard_bottles(db: aiosqlite.Connection) -> list[DashboardBottle]:
    cursor = await db.execute(
        """SELECT b.id, b.name, b.enabled, b.extract_memories,
                  i.network, i.nick, i.channels,
                  (SELECT COUNT(*) FROM memory_candidates c
                   JOIN messages cm ON cm.id = c.source_message_id
                   WHERE cm.bot_id = b.id AND c.status = 'pending') AS pending_candidates,
                  (SELECT MAX(m.timestamp) FROM messages m WHERE m.bot_id = b.id) AS last_activity,
                  (SELECT GROUP_CONCAT(bm.module_name, ',') FROM bot_modules bm
                   WHERE bm.bot_id = b.id AND bm.enabled = 1) AS enabled_modules
           FROM bots b JOIN irc_profiles i ON i.id = b.irc_profile_id
           ORDER BY b.id"""
    )
    return [
        DashboardBottle(
            id=row["id"], name=row["name"], enabled=bool(row["enabled"]),
            extract_memories=bool(row["extract_memories"]), network=row["network"],
            nick=row["nick"], channels=json.loads(row["channels"]),
            pending_candidates=row["pending_candidates"], last_activity=row["last_activity"],
            enabled_modules=row["enabled_modules"],
        )
        for row in await cursor.fetchall()
    ]


async def recent_bottle_messages(
    db: aiosqlite.Connection, *, bottle_id: int, limit: int = 30
) -> list[DashboardMessage]:
    cursor = await db.execute(
        """SELECT timestamp, channel, speaker, body FROM (
               SELECT id, timestamp, channel, speaker, body FROM messages
               WHERE bot_id = ? ORDER BY id DESC LIMIT ?
           ) ORDER BY id""", (bottle_id, limit),
    )
    return [DashboardMessage(**dict(row)) for row in await cursor.fetchall()]


async def dashboard_audit_events(
    db: aiosqlite.Connection, *, limit: int = 100
) -> list[DashboardAuditEvent]:
    cursor = await db.execute(
        """SELECT * FROM (
               SELECT 'memory-' || id AS event_key, created_at, actor,
                      'memory' AS category, action,
                      entity_type || ' ' || entity_id AS target,
                      TRIM(
                          COALESCE(old_status || ' -> ' || new_status, '') ||
                          CASE WHEN new_text IS NOT NULL
                               THEN ' ' || COALESCE(old_text, '') || ' -> ' || new_text
                               ELSE '' END ||
                          CASE WHEN old_expires_at IS NOT NULL OR new_expires_at IS NOT NULL
                               THEN ' expiry: ' || COALESCE(old_expires_at, 'never') ||
                                    ' -> ' || COALESCE(new_expires_at, 'never')
                               ELSE '' END
                      ) AS details
               FROM audit_events
               UNION ALL
               SELECT 'configuration-' || id, created_at, actor,
                      'configuration', 'change', 'Bottle ' || bot_id,
                      'changed: ' || changed_fields
               FROM configuration_events
           ) ORDER BY created_at DESC, event_key DESC LIMIT ?""", (limit,),
    )
    return [DashboardAuditEvent(**dict(row)) for row in await cursor.fetchall()]
