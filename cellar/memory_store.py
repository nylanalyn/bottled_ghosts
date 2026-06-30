import aiosqlite

from cellar.models import (
    ExtractedMemory,
    MemoryCandidateView,
    MemoryType,
    UserMemory,
    UserMemoryView,
)

TEMPORARY_MEMORY_HOURS = 24


async def store_memory_candidates(
    db: aiosqlite.Connection, *, user_id: str, source_message_id: int,
    candidates: list[ExtractedMemory],
) -> int:
    inserted = 0
    try:
        for candidate in candidates:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO memory_candidates(
                       user_id, source_message_id, candidate_text, memory_type, confidence
                   ) VALUES (?, ?, ?, ?, ?)""",
                (user_id, source_message_id, candidate.text, candidate.type, candidate.confidence),
            )
            inserted += max(cursor.rowcount, 0)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return inserted


async def list_memory_candidates(
    db: aiosqlite.Connection, *, status: str = "pending"
) -> list[MemoryCandidateView]:
    cursor = await db.execute(
        """SELECT c.*, u.canonical_name, m.body AS source_body
           FROM memory_candidates c
           JOIN users u ON u.id = c.user_id
           JOIN messages m ON m.id = c.source_message_id
           WHERE c.status = ? ORDER BY c.created_at, c.id""", (status,),
    )
    return [
        MemoryCandidateView(
            id=row["id"], user_id=row["user_id"], canonical_name=row["canonical_name"],
            source_message_id=row["source_message_id"], source_body=row["source_body"],
            candidate_text=row["candidate_text"], memory_type=row["memory_type"],
            confidence=row["confidence"], status=row["status"],
        )
        for row in await cursor.fetchall()
    ]


async def get_memory_candidate(
    db: aiosqlite.Connection, *, candidate_id: int
) -> MemoryCandidateView | None:
    cursor = await db.execute(
        """SELECT c.*, u.canonical_name, m.body AS source_body
           FROM memory_candidates c
           JOIN users u ON u.id = c.user_id
           JOIN messages m ON m.id = c.source_message_id
           WHERE c.id = ?""", (candidate_id,),
    )
    row = await cursor.fetchone()
    return MemoryCandidateView(**dict(row)) if row is not None else None


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
        cursor = await db.execute(
            """INSERT INTO user_memories(
                   user_id, source_candidate_id, memory_text, memory_type, confidence,
                   expires_at
               ) VALUES (?, ?, ?, ?, ?,
                   CASE WHEN ? = 'temporary_state' THEN datetime('now', '+24 hours') END
               )""",
            (row["user_id"], candidate_id, row["candidate_text"], row["memory_type"],
             row["confidence"], row["memory_type"]),
        )
        memory_id = cursor.lastrowid
        if memory_id is None:
            raise RuntimeError("SQLite did not return a memory id")
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
    db: aiosqlite.Connection, *, user_id: str
) -> list[UserMemory]:
    cursor = await db.execute(
        """SELECT id, user_id, memory_text, memory_type, confidence, expires_at
           FROM user_memories WHERE user_id = ? ORDER BY id""", (user_id,),
    )
    return [UserMemory(**dict(row)) for row in await cursor.fetchall()]


async def list_all_user_memories(db: aiosqlite.Connection) -> list[UserMemoryView]:
    cursor = await db.execute(
        """SELECT um.id, um.user_id, u.canonical_name, um.source_candidate_id,
                  m.body AS source_body, um.memory_text, um.memory_type, um.confidence
                  , um.expires_at
           FROM user_memories um
           JOIN users u ON u.id = um.user_id
           LEFT JOIN memory_candidates c ON c.id = um.source_candidate_id
           LEFT JOIN messages m ON m.id = c.source_message_id
           ORDER BY u.canonical_name COLLATE NOCASE, um.id"""
    )
    return [UserMemoryView(**dict(row)) for row in await cursor.fetchall()]


async def get_user_memory(
    db: aiosqlite.Connection, *, memory_id: int
) -> UserMemoryView | None:
    cursor = await db.execute(
        """SELECT um.id, um.user_id, u.canonical_name, um.source_candidate_id,
                  m.body AS source_body, um.memory_text, um.memory_type, um.confidence
                  , um.expires_at
           FROM user_memories um
           JOIN users u ON u.id = um.user_id
           LEFT JOIN memory_candidates c ON c.id = um.source_candidate_id
           LEFT JOIN messages m ON m.id = c.source_message_id
           WHERE um.id = ?""", (memory_id,),
    )
    row = await cursor.fetchone()
    return UserMemoryView(**dict(row)) if row is not None else None


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


async def approved_memory_texts(
    db: aiosqlite.Connection, *, user_id: str, limit: int = 10
) -> list[str]:
    cursor = await db.execute(
        """SELECT memory_type, memory_text FROM user_memories
           WHERE user_id = ? AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
           ORDER BY id DESC LIMIT ?""", (user_id, limit),
    )
    return [f"{row['memory_type']}: {row['memory_text']}" for row in await cursor.fetchall()]


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
