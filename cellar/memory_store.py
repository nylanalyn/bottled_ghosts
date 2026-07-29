import json
import re
import unicodedata

import aiosqlite

from cellar.models import (
    ExtractedMemory,
    MemoryCandidateView,
    MemoryEvidenceView,
    MemorySource,
    MemorySuggestion,
    MemoryType,
    UserMemory,
    UserMemoryView,
)

TEMPORARY_MEMORY_HOURS = 24
MEMORY_SEARCH_STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "but", "for", "from",
    "has", "have", "into", "is", "its", "that", "the", "their", "they",
    "this", "user", "was", "were", "with",
}


async def store_memory_candidates(
    db: aiosqlite.Connection, *, bot_id: int, user_id: str,
    source_message_ids: list[int], candidates: list[ExtractedMemory],
) -> int:
    if not source_message_ids:
        raise ValueError("memory candidates require at least one source message")
    unique_source_ids = tuple(dict.fromkeys(source_message_ids))
    placeholders = ", ".join("?" for _ in unique_source_ids)
    source_rows = list(await (await db.execute(
        f"SELECT id, bot_id FROM messages WHERE id IN ({placeholders})",
        unique_source_ids,
    )).fetchall())
    if (
        len(source_rows) != len(unique_source_ids)
        or any(row["bot_id"] != bot_id for row in source_rows)
    ):
        raise ValueError("memory candidate sources must belong to the owning Bottle")
    source_message_id = source_message_ids[-1]
    inserted = 0
    try:
        for candidate in candidates:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO memory_candidates(
                       bot_id, user_id, source_message_id, candidate_text,
                       memory_type, confidence
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    bot_id, user_id, source_message_id, candidate.text,
                    candidate.type, candidate.confidence,
                ),
            )
            inserted += max(cursor.rowcount, 0)
            candidate_row = await (await db.execute(
                """SELECT id FROM memory_candidates
                   WHERE bot_id = ? AND user_id = ? AND source_message_id = ?
                     AND candidate_text = ?""",
                (bot_id, user_id, source_message_id, candidate.text),
            )).fetchone()
            if candidate_row is None:
                raise RuntimeError("stored memory candidate could not be reloaded")
            for ordinal, message_id in enumerate(source_message_ids):
                await db.execute(
                    """INSERT OR IGNORE INTO memory_candidate_sources(
                           candidate_id, message_id, ordinal
                       ) VALUES (?, ?, ?)""",
                    (candidate_row["id"], message_id, ordinal),
                )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return inserted


async def list_memory_candidates(
    db: aiosqlite.Connection, *, status: str = "pending"
) -> list[MemoryCandidateView]:
    cursor = await db.execute(
        """SELECT c.*, b.name AS bottle_name, u.canonical_name, m.body AS source_body,
                  (SELECT json_group_array(json_object(
                       'message_id', sources.message_id, 'body', sources.body
                   )) FROM (
                       SELECT sm.id AS message_id, sm.body
                       FROM memory_candidate_sources cs
                       JOIN messages sm ON sm.id = cs.message_id
                       WHERE cs.candidate_id = c.id ORDER BY cs.ordinal
                   ) AS sources) AS source_messages_json
           FROM memory_candidates c
           JOIN bots b ON b.id = c.bot_id
           JOIN users u ON u.id = c.user_id
           JOIN messages m ON m.id = c.source_message_id
           WHERE c.status = ? ORDER BY c.created_at, c.id""", (status,),
    )
    return [
        _candidate_from_row(row)
        for row in await cursor.fetchall()
    ]


async def get_memory_candidate(
    db: aiosqlite.Connection, *, candidate_id: int
) -> MemoryCandidateView | None:
    cursor = await db.execute(
        """SELECT c.*, b.name AS bottle_name, u.canonical_name, m.body AS source_body,
                  (SELECT json_group_array(json_object(
                       'message_id', sources.message_id, 'body', sources.body
                   )) FROM (
                       SELECT sm.id AS message_id, sm.body
                       FROM memory_candidate_sources cs
                       JOIN messages sm ON sm.id = cs.message_id
                       WHERE cs.candidate_id = c.id ORDER BY cs.ordinal
                   ) AS sources) AS source_messages_json
           FROM memory_candidates c
           JOIN bots b ON b.id = c.bot_id
           JOIN users u ON u.id = c.user_id
           JOIN messages m ON m.id = c.source_message_id
           WHERE c.id = ?""", (candidate_id,),
    )
    row = await cursor.fetchone()
    return _candidate_from_row(row) if row is not None else None


async def approve_memory_candidate(
    db: aiosqlite.Connection, *, candidate_id: int, actor: str = "operator"
) -> int:
    actor = _actor(actor)
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute(
            "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
        )).fetchone()
        if row is None:
            raise LookupError(f"memory candidate {candidate_id} does not exist")
        if row["status"] != "pending":
            raise ValueError(f"memory candidate {candidate_id} is already {row['status']}")
        existing_rows = await (await db.execute(
            """SELECT id, memory_text
               FROM user_memories
               WHERE bot_id = ? AND user_id = ? AND state = 'active'
                 AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
               ORDER BY id""",
            (row["bot_id"], row["user_id"]),
        )).fetchall()
        candidate_key = normalize_memory_text(str(row["candidate_text"]))
        exact_memory_id = next(
            (
                int(existing["id"])
                for existing in existing_rows
                if normalize_memory_text(str(existing["memory_text"])) == candidate_key
            ),
            None,
        )
        if exact_memory_id is not None:
            await db.execute(
                """INSERT INTO user_memory_evidence(memory_id, candidate_id, linked_by)
                   VALUES (?, ?, ?)""",
                (exact_memory_id, candidate_id, actor),
            )
            await db.execute(
                """UPDATE memory_candidates
                   SET status = 'approved', reviewed_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (candidate_id,),
            )
            await db.execute(
                """INSERT INTO audit_events(
                       action, entity_type, entity_id, related_entity_id, actor,
                       new_text, new_type, new_confidence, old_status, new_status
                   ) VALUES (
                       'attach', 'memory_candidate', ?, ?, ?, ?, ?, ?,
                       'pending', 'approved'
                   )""",
                (
                    candidate_id, exact_memory_id, actor, row["candidate_text"],
                    row["memory_type"], row["confidence"],
                ),
            )
            await db.commit()
            return exact_memory_id
        cursor = await db.execute(
            """INSERT INTO user_memories(
                   bot_id, user_id, memory_text, memory_type, confidence,
                   expires_at
               ) VALUES (?, ?, ?, ?, ?,
                   CASE WHEN ? = 'temporary_state' THEN datetime('now', '+24 hours') END
               )""",
            (
                row["bot_id"], row["user_id"], row["candidate_text"],
                row["memory_type"], row["confidence"], row["memory_type"],
            ),
        )
        memory_id = cursor.lastrowid
        if memory_id is None:
            raise RuntimeError("SQLite did not return a memory id")
        await db.execute(
            """INSERT INTO user_memory_evidence(memory_id, candidate_id, linked_by)
               VALUES (?, ?, ?)""",
            (memory_id, candidate_id, actor),
        )
        await db.execute(
            """UPDATE memory_candidates SET status = 'approved', reviewed_at = CURRENT_TIMESTAMP
               WHERE id = ?""", (candidate_id,),
        )
        await db.execute(
            """INSERT INTO audit_events(
                   action, entity_type, entity_id, related_entity_id, actor,
                   new_text, new_type, new_confidence, old_status, new_status,
                   new_expires_at
               ) VALUES (
                   'approve', 'memory_candidate', ?, ?, ?, ?, ?, ?, 'pending', 'approved',
                   (SELECT expires_at FROM user_memories WHERE id = ?)
               )""",
            (candidate_id, memory_id, actor, row["candidate_text"], row["memory_type"],
             row["confidence"], memory_id),
        )
        await db.commit()
        return memory_id
    except Exception:
        await db.rollback()
        raise


async def attach_memory_candidate(
    db: aiosqlite.Connection, *, candidate_id: int, memory_id: int,
    actor: str = "operator",
) -> int:
    actor = _actor(actor)
    try:
        await db.execute("BEGIN IMMEDIATE")
        candidate = await (await db.execute(
            "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
        )).fetchone()
        if candidate is None:
            raise LookupError(f"memory candidate {candidate_id} does not exist")
        if candidate["status"] != "pending":
            raise ValueError(
                f"memory candidate {candidate_id} is already {candidate['status']}"
            )
        memory = await (await db.execute(
            "SELECT * FROM user_memories WHERE id = ?", (memory_id,)
        )).fetchone()
        if memory is None:
            raise LookupError(f"memory {memory_id} does not exist")
        if memory["state"] != "active":
            raise ValueError(f"memory {memory_id} is {memory['state']}")
        if (
            memory["bot_id"] != candidate["bot_id"]
            or memory["user_id"] != candidate["user_id"]
        ):
            raise ValueError("candidate and memory must share a Bottle and user")
        await db.execute(
            """INSERT INTO user_memory_evidence(memory_id, candidate_id, linked_by)
               VALUES (?, ?, ?)""",
            (memory_id, candidate_id, actor),
        )
        await db.execute(
            """UPDATE memory_candidates
               SET status = 'approved', reviewed_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (candidate_id,),
        )
        await db.execute(
            """INSERT INTO audit_events(
                   action, entity_type, entity_id, related_entity_id, actor,
                   new_text, new_type, new_confidence, old_status, new_status
               ) VALUES (
                   'attach', 'memory_candidate', ?, ?, ?, ?, ?, ?,
                   'pending', 'approved'
               )""",
            (
                candidate_id, memory_id, actor, candidate["candidate_text"],
                candidate["memory_type"], candidate["confidence"],
            ),
        )
        await db.commit()
        return memory_id
    except Exception:
        await db.rollback()
        raise


async def reject_memory_candidate(
    db: aiosqlite.Connection, *, candidate_id: int, actor: str = "operator"
) -> None:
    actor = _actor(actor)
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute(
            "SELECT status FROM memory_candidates WHERE id = ?", (candidate_id,)
        )).fetchone()
        if row is None:
            raise LookupError(f"memory candidate {candidate_id} does not exist")
        if row["status"] != "pending":
            raise ValueError(f"memory candidate {candidate_id} is already {row['status']}")
        await db.execute(
            """UPDATE memory_candidates SET status = 'rejected', reviewed_at = CURRENT_TIMESTAMP
               WHERE id = ?""", (candidate_id,),
        )
        await db.execute(
            """INSERT INTO audit_events(
                   action, entity_type, entity_id, actor, old_status, new_status
               ) VALUES ('reject', 'memory_candidate', ?, ?, 'pending', 'rejected')""",
            (candidate_id, actor),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def list_user_memories(
    db: aiosqlite.Connection, *, bot_id: int, user_id: str,
    include_merged: bool = False,
) -> list[UserMemory]:
    state_filter = "" if include_merged else "AND state = 'active'"
    cursor = await db.execute(
        f"""SELECT id, bot_id, user_id, memory_text, memory_type, confidence,
                   state, merged_into_id, expires_at
            FROM user_memories
            WHERE bot_id = ? AND user_id = ? {state_filter}
            ORDER BY id""",
        (bot_id, user_id),
    )
    return [UserMemory(**dict(row)) for row in await cursor.fetchall()]


async def list_all_user_memories(
    db: aiosqlite.Connection, *, include_expired: bool = False,
    include_merged: bool = False,
) -> list[UserMemoryView]:
    filters: list[str] = []
    if not include_expired:
        filters.append(
            "(um.expires_at IS NULL OR um.expires_at > CURRENT_TIMESTAMP)"
        )
    if not include_merged:
        filters.append("um.state = 'active'")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    cursor = await db.execute(
        f"""SELECT um.id, um.bot_id, b.name AS bottle_name, um.user_id,
                  u.canonical_name, um.memory_text, um.memory_type, um.confidence,
                  um.state, um.merged_into_id, um.expires_at,
                  (SELECT COUNT(*) FROM user_memory_evidence e
                   WHERE e.memory_id = um.id) AS evidence_count
           FROM user_memories um
           JOIN bots b ON b.id = um.bot_id
           JOIN users u ON u.id = um.user_id
           {where}
           ORDER BY b.name COLLATE NOCASE, u.canonical_name COLLATE NOCASE, um.id"""
    )
    return [UserMemoryView(**dict(row)) for row in await cursor.fetchall()]


async def get_user_memory(
    db: aiosqlite.Connection, *, memory_id: int
) -> UserMemoryView | None:
    cursor = await db.execute(
        """SELECT um.id, um.bot_id, b.name AS bottle_name, um.user_id,
                  u.canonical_name, um.memory_text, um.memory_type, um.confidence,
                  um.state, um.merged_into_id, um.expires_at,
                  (SELECT COUNT(*) FROM user_memory_evidence e
                   WHERE e.memory_id = um.id) AS evidence_count
           FROM user_memories um
           JOIN bots b ON b.id = um.bot_id
           JOIN users u ON u.id = um.user_id
           WHERE um.id = ?""", (memory_id,),
    )
    row = await cursor.fetchone()
    return UserMemoryView(**dict(row)) if row is not None else None


async def list_memory_evidence(
    db: aiosqlite.Connection, *, memory_id: int
) -> list[MemoryEvidenceView]:
    cursor = await db.execute(
        """SELECT e.memory_id, e.candidate_id, c.candidate_text, c.memory_type,
                  c.confidence, e.linked_at, e.linked_by,
                  (SELECT json_group_array(json_object(
                       'message_id', sources.message_id, 'body', sources.body
                   )) FROM (
                       SELECT m.id AS message_id, m.body
                       FROM memory_candidate_sources cs
                       JOIN messages m ON m.id = cs.message_id
                       WHERE cs.candidate_id = c.id
                       ORDER BY cs.ordinal
                   ) AS sources) AS source_messages_json
           FROM user_memory_evidence e
           JOIN memory_candidates c ON c.id = e.candidate_id
           WHERE e.memory_id = ?
           ORDER BY e.linked_at, e.candidate_id""",
        (memory_id,),
    )
    evidence: list[MemoryEvidenceView] = []
    for row in await cursor.fetchall():
        values = dict(row)
        sources = json.loads(values.pop("source_messages_json") or "[]")
        values["source_messages"] = [MemorySource(**source) for source in sources]
        evidence.append(MemoryEvidenceView(**values))
    return evidence


async def suggest_memories_for_candidate(
    db: aiosqlite.Connection, *, candidate_id: int, limit: int = 5,
) -> list[MemorySuggestion]:
    candidate = await (await db.execute(
        "SELECT * FROM memory_candidates WHERE id = ?", (candidate_id,)
    )).fetchone()
    if candidate is None:
        raise LookupError(f"memory candidate {candidate_id} does not exist")
    rows = await (await db.execute(
        """SELECT um.id, um.memory_text, um.memory_type, um.confidence,
                  (SELECT COUNT(*) FROM user_memory_evidence e
                   WHERE e.memory_id = um.id) AS evidence_count
           FROM user_memories um
           WHERE um.bot_id = ? AND um.user_id = ? AND um.state = 'active'
             AND (um.expires_at IS NULL OR um.expires_at > CURRENT_TIMESTAMP)
           ORDER BY um.id DESC""",
        (candidate["bot_id"], candidate["user_id"]),
    )).fetchall()
    candidate_key = normalize_memory_text(str(candidate["candidate_text"]))
    candidate_tokens = _memory_tokens(str(candidate["candidate_text"]))

    def rank(row: aiosqlite.Row) -> tuple[float, int]:
        memory_key = normalize_memory_text(str(row["memory_text"]))
        memory_tokens = _memory_tokens(str(row["memory_text"]))
        if candidate_key == memory_key:
            score = 2.0
        elif candidate_tokens or memory_tokens:
            score = len(candidate_tokens & memory_tokens) / max(
                len(candidate_tokens | memory_tokens), 1,
            )
        else:
            score = 0.0
        if row["memory_type"] == candidate["memory_type"]:
            score += 0.1
        return score, int(row["id"])

    ranked = sorted(rows, key=rank, reverse=True)
    return [
        MemorySuggestion(**dict(row))
        for row in ranked[:limit]
        if rank(row)[0] > 0.1
    ]


async def edit_user_memory(
    db: aiosqlite.Connection, *, memory_id: int, text: str | None = None,
    memory_type: MemoryType | None = None, confidence: float | None = None,
    actor: str = "operator",
) -> None:
    actor = _actor(actor)
    if text is None and memory_type is None and confidence is None:
        raise ValueError("at least one memory field must change")
    if text is not None and not text.strip():
        raise ValueError("memory text cannot be empty")
    if confidence is not None and not 0 <= confidence <= 1:
        raise ValueError("memory confidence must be between 0 and 1")
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute(
            "SELECT * FROM user_memories WHERE id = ?", (memory_id,)
        )).fetchone()
        if row is None:
            raise LookupError(f"memory {memory_id} does not exist")
        if row["state"] != "active":
            raise ValueError(f"memory {memory_id} is {row['state']}")
        new_text = text.strip() if text is not None else row["memory_text"]
        new_type = memory_type if memory_type is not None else row["memory_type"]
        new_confidence = confidence if confidence is not None else row["confidence"]
        if new_type != "temporary_state":
            new_expires_at = None
        elif row["memory_type"] == "temporary_state" and row["expires_at"] is not None:
            new_expires_at = row["expires_at"]
        else:
            new_expires_at = await _temporary_expiry(db)
        await db.execute(
            """UPDATE user_memories SET memory_text = ?, memory_type = ?, confidence = ?,
                   expires_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (new_text, new_type, new_confidence, new_expires_at, memory_id),
        )
        await db.execute(
            """INSERT INTO audit_events(
                   action, entity_type, entity_id, actor, old_text, new_text,
                   old_type, new_type, old_confidence, new_confidence,
                   old_expires_at, new_expires_at
               ) VALUES ('edit', 'user_memory', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (memory_id, actor, row["memory_text"], new_text, row["memory_type"], new_type,
             row["confidence"], new_confidence, row["expires_at"], new_expires_at),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def merge_user_memories(
    db: aiosqlite.Connection, *, target_memory_id: int,
    merged_memory_ids: list[int], text: str | None = None,
    memory_type: MemoryType | None = None, confidence: float | None = None,
    actor: str = "operator",
    _manage_transaction: bool = True,
) -> int:
    actor = _actor(actor)
    source_ids = tuple(dict.fromkeys(merged_memory_ids))
    if not source_ids:
        raise ValueError("at least one memory must be merged")
    if target_memory_id in source_ids:
        raise ValueError("target memory cannot be merged into itself")
    if text is not None and not text.strip():
        raise ValueError("memory text cannot be empty")
    if confidence is not None and not 0 <= confidence <= 1:
        raise ValueError("memory confidence must be between 0 and 1")
    try:
        if _manage_transaction:
            await db.execute("BEGIN IMMEDIATE")
        target = await (await db.execute(
            "SELECT * FROM user_memories WHERE id = ?", (target_memory_id,)
        )).fetchone()
        if target is None:
            raise LookupError(f"memory {target_memory_id} does not exist")
        if target["state"] != "active":
            raise ValueError(f"memory {target_memory_id} is {target['state']}")
        placeholders = ", ".join("?" for _ in source_ids)
        sources = list(await (await db.execute(
            f"SELECT * FROM user_memories WHERE id IN ({placeholders})",
            source_ids,
        )).fetchall())
        if len(sources) != len(source_ids):
            found = {int(row["id"]) for row in sources}
            missing = next(memory_id for memory_id in source_ids if memory_id not in found)
            raise LookupError(f"memory {missing} does not exist")
        for source in sources:
            if source["state"] != "active":
                raise ValueError(f"memory {source['id']} is {source['state']}")
            if (
                source["bot_id"] != target["bot_id"]
                or source["user_id"] != target["user_id"]
            ):
                raise ValueError("merged memories must share a Bottle and user")

        new_text = text.strip() if text is not None else str(target["memory_text"])
        new_type = memory_type if memory_type is not None else target["memory_type"]
        new_confidence = confidence if confidence is not None else target["confidence"]
        if new_type != "temporary_state":
            new_expires_at = None
        elif target["memory_type"] == "temporary_state" and target["expires_at"]:
            new_expires_at = target["expires_at"]
        else:
            new_expires_at = await _temporary_expiry(db)

        await db.execute(
            """UPDATE user_memories
               SET memory_text = ?, memory_type = ?, confidence = ?, expires_at = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                new_text, new_type, new_confidence, new_expires_at,
                target_memory_id,
            ),
        )
        if (
            new_text != target["memory_text"]
            or new_type != target["memory_type"]
            or new_confidence != target["confidence"]
            or new_expires_at != target["expires_at"]
        ):
            await db.execute(
                """INSERT INTO audit_events(
                       action, entity_type, entity_id, actor, old_text, new_text,
                       old_type, new_type, old_confidence, new_confidence,
                       old_expires_at, new_expires_at
                   ) VALUES (
                       'edit', 'user_memory', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                   )""",
                (
                    target_memory_id, actor, target["memory_text"], new_text,
                    target["memory_type"], new_type, target["confidence"],
                    new_confidence, target["expires_at"], new_expires_at,
                ),
            )
        for source in sources:
            await db.execute(
                """UPDATE user_memory_evidence SET memory_id = ?
                   WHERE memory_id = ?""",
                (target_memory_id, source["id"]),
            )
            await db.execute(
                """UPDATE user_memories
                   SET state = 'merged', merged_into_id = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (target_memory_id, source["id"]),
            )
            await db.execute(
                """INSERT INTO audit_events(
                       action, entity_type, entity_id, related_entity_id, actor,
                       old_text, new_text, old_type, new_type, old_confidence,
                       new_confidence, old_status, new_status
                   ) VALUES (
                       'merge', 'user_memory', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       'active', 'merged'
                   )""",
                (
                    source["id"], target_memory_id, actor, source["memory_text"],
                    new_text, source["memory_type"], new_type,
                    source["confidence"], new_confidence,
                ),
            )
        if _manage_transaction:
            await db.commit()
        return target_memory_id
    except Exception:
        if _manage_transaction:
            await db.rollback()
        raise


async def approved_memory_texts(
    db: aiosqlite.Connection, *, bot_id: int, user_id: str,
    query_text: str | None = None, limit: int = 10,
) -> list[str]:
    selected: list[aiosqlite.Row] = []
    selected_ids: set[int] = set()

    async def append_rows(query: str, parameters: tuple[object, ...]) -> None:
        for row in await (await db.execute(query, parameters)).fetchall():
            memory_id = int(row["id"])
            if memory_id not in selected_ids and len(selected) < limit:
                selected.append(row)
                selected_ids.add(memory_id)

    fts_query = _memory_fts_query(query_text or "")
    if fts_query is not None:
        await append_rows(
            """SELECT um.id, um.memory_type, um.memory_text
               FROM user_memories_fts f
               JOIN user_memories um ON um.id = f.rowid
               WHERE user_memories_fts MATCH ?
                 AND um.bot_id = ? AND um.user_id = ? AND um.state = 'active'
                 AND (um.expires_at IS NULL OR um.expires_at > CURRENT_TIMESTAMP)
               ORDER BY bm25(user_memories_fts), um.id DESC
               LIMIT ?""",
            (fts_query, bot_id, user_id, min(6, limit)),
        )
    await append_rows(
        """SELECT id, memory_type, memory_text
           FROM user_memories
           WHERE bot_id = ? AND user_id = ? AND state = 'active'
             AND memory_type IN ('relationship', 'identity')
             AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
           ORDER BY id DESC LIMIT 2""",
        (bot_id, user_id),
    )
    await append_rows(
        """SELECT id, memory_type, memory_text
           FROM user_memories
           WHERE bot_id = ? AND user_id = ? AND state = 'active'
             AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
           ORDER BY id DESC LIMIT ?""",
        (bot_id, user_id, limit),
    )
    return [
        f"{row['memory_type']}: {row['memory_text']}"
        for row in selected
    ]


def _actor(actor: str) -> str:
    actor = actor.strip()
    if not actor:
        raise ValueError("audit actor cannot be empty")
    return actor


async def _temporary_expiry(db: aiosqlite.Connection) -> str:
    row = await (await db.execute(
        "SELECT datetime('now', ?)", (f"+{TEMPORARY_MEMORY_HOURS} hours",)
    )).fetchone()
    if row is None:
        raise RuntimeError("SQLite did not return a temporary memory expiry")
    return str(row[0])


def _candidate_from_row(row: aiosqlite.Row) -> MemoryCandidateView:
    values = dict(row)
    sources = json.loads(values.pop("source_messages_json") or "[]")
    values["source_messages"] = [MemorySource(**source) for source in sources]
    return MemoryCandidateView(**values)


def normalize_memory_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.rstrip(".!?")


def _memory_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w'-]+", normalize_memory_text(text))
        if len(token) >= 3 and token not in MEMORY_SEARCH_STOPWORDS
    }


def _memory_fts_query(text: str) -> str | None:
    tokens = sorted(_memory_tokens(text), key=lambda token: (-len(token), token))
    if not tokens:
        return None
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:12])
