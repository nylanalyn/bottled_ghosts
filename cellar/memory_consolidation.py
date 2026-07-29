from collections import Counter
import json

import aiosqlite
from pydantic import ValidationError

from cellar.llm import complete
from cellar.memory import FENCE_RE
from cellar.memory_store import (
    _memory_tokens,
    merge_user_memories,
    normalize_memory_text,
)
from cellar.models import (
    ConsolidationGroup,
    ConsolidationGroups,
    ConsolidationProposalView,
    LLMProfile,
)

CONSOLIDATION_BATCH_SIZE = 35
CONSOLIDATION_BATCH_OVERLAP = 10


async def create_consolidation_proposal(
    db: aiosqlite.Connection, *, memory_ids: list[int],
    proposed_text: str, proposed_type: str, proposed_confidence: float,
    rationale: str, actor: str = "operator",
) -> int:
    actor = _actor(actor)
    member_ids = tuple(dict.fromkeys(memory_ids))
    if len(member_ids) < 2:
        raise ValueError("a consolidation proposal needs at least two memories")
    if not proposed_text.strip():
        raise ValueError("proposed memory text cannot be empty")
    if not rationale.strip():
        raise ValueError("consolidation rationale cannot be empty")
    if not 0 <= proposed_confidence <= 1:
        raise ValueError("proposed confidence must be between 0 and 1")
    placeholders = ", ".join("?" for _ in member_ids)
    rows = list(await (await db.execute(
        f"""SELECT id, bot_id, user_id, state
            FROM user_memories WHERE id IN ({placeholders})""",
        member_ids,
    )).fetchall())
    if len(rows) != len(member_ids):
        found = {int(row["id"]) for row in rows}
        missing = next(memory_id for memory_id in member_ids if memory_id not in found)
        raise LookupError(f"memory {missing} does not exist")
    if any(row["state"] != "active" for row in rows):
        raise ValueError("consolidation members must all be active")
    scopes = {(int(row["bot_id"]), str(row["user_id"])) for row in rows}
    if len(scopes) != 1:
        raise ValueError("consolidation members must share a Bottle and user")
    bot_id, user_id = scopes.pop()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """INSERT INTO memory_consolidation_proposals(
                   bot_id, user_id, proposed_text, proposed_type,
                   proposed_confidence, rationale
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                bot_id, user_id, proposed_text.strip(), proposed_type,
                proposed_confidence, rationale.strip(),
            ),
        )
        proposal_id = cursor.lastrowid
        if proposal_id is None:
            raise RuntimeError("SQLite did not return a consolidation proposal id")
        for ordinal, memory_id in enumerate(member_ids):
            await db.execute(
                """INSERT INTO memory_consolidation_members(
                       proposal_id, memory_id, ordinal
                   ) VALUES (?, ?, ?)""",
                (proposal_id, memory_id, ordinal),
            )
        await db.execute(
            """INSERT INTO audit_events(
                   action, entity_type, entity_id, actor, new_text, new_type,
                   new_confidence, new_status
               ) VALUES (
                   'propose', 'consolidation_proposal', ?, ?, ?, ?, ?, 'pending'
               )""",
            (
                proposal_id, actor, proposed_text.strip(), proposed_type,
                proposed_confidence,
            ),
        )
        await db.commit()
        return int(proposal_id)
    except Exception:
        await db.rollback()
        raise


async def list_consolidation_proposals(
    db: aiosqlite.Connection, *, status: str | None = "pending",
) -> list[ConsolidationProposalView]:
    where = "WHERE p.status = ?" if status is not None else ""
    parameters: tuple[object, ...] = (status,) if status is not None else ()
    cursor = await db.execute(
        f"""SELECT p.*, b.name AS bottle_name, u.canonical_name,
                  (SELECT json_group_array(member_id) FROM (
                       SELECT pm.memory_id AS member_id
                       FROM memory_consolidation_members pm
                       WHERE pm.proposal_id = p.id ORDER BY pm.ordinal
                   )) AS memory_ids_json,
                  (SELECT json_group_array(memory_text) FROM (
                       SELECT um.memory_text AS memory_text
                       FROM memory_consolidation_members pm
                       JOIN user_memories um ON um.id = pm.memory_id
                       WHERE pm.proposal_id = p.id ORDER BY pm.ordinal
                   )) AS memory_texts_json
           FROM memory_consolidation_proposals p
           JOIN bots b ON b.id = p.bot_id
           JOIN users u ON u.id = p.user_id
           {where}
           ORDER BY p.created_at, p.id""",
        parameters,
    )
    return [_proposal_from_row(row) for row in await cursor.fetchall()]


async def get_consolidation_proposal(
    db: aiosqlite.Connection, *, proposal_id: int,
) -> ConsolidationProposalView | None:
    proposals = await list_consolidation_proposals_for_ids(
        db, proposal_ids=[proposal_id],
    )
    return proposals[0] if proposals else None


async def list_consolidation_proposals_for_ids(
    db: aiosqlite.Connection, *, proposal_ids: list[int],
) -> list[ConsolidationProposalView]:
    if not proposal_ids:
        return []
    placeholders = ", ".join("?" for _ in proposal_ids)
    cursor = await db.execute(
        f"""SELECT p.*, b.name AS bottle_name, u.canonical_name,
                   (SELECT json_group_array(member_id) FROM (
                        SELECT pm.memory_id AS member_id
                        FROM memory_consolidation_members pm
                        WHERE pm.proposal_id = p.id ORDER BY pm.ordinal
                    )) AS memory_ids_json,
                   (SELECT json_group_array(memory_text) FROM (
                        SELECT um.memory_text AS memory_text
                        FROM memory_consolidation_members pm
                        JOIN user_memories um ON um.id = pm.memory_id
                        WHERE pm.proposal_id = p.id ORDER BY pm.ordinal
                    )) AS memory_texts_json
            FROM memory_consolidation_proposals p
            JOIN bots b ON b.id = p.bot_id
            JOIN users u ON u.id = p.user_id
            WHERE p.id IN ({placeholders})
            ORDER BY p.id""",
        tuple(proposal_ids),
    )
    return [_proposal_from_row(row) for row in await cursor.fetchall()]


async def reject_consolidation_proposal(
    db: aiosqlite.Connection, *, proposal_id: int, actor: str = "operator",
) -> None:
    actor = _actor(actor)
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute(
            "SELECT * FROM memory_consolidation_proposals WHERE id = ?",
            (proposal_id,),
        )).fetchone()
        if row is None:
            raise LookupError(f"consolidation proposal {proposal_id} does not exist")
        if row["status"] != "pending":
            raise ValueError(
                f"consolidation proposal {proposal_id} is already {row['status']}"
            )
        await db.execute(
            """UPDATE memory_consolidation_proposals
               SET status = 'rejected', reviewed_at = CURRENT_TIMESTAMP,
                   reviewed_by = ?
               WHERE id = ?""",
            (actor, proposal_id),
        )
        await db.execute(
            """INSERT INTO audit_events(
                   action, entity_type, entity_id, actor, old_status, new_status
               ) VALUES (
                   'reject', 'consolidation_proposal', ?, ?, 'pending', 'rejected'
               )""",
            (proposal_id, actor),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def accept_consolidation_proposal(
    db: aiosqlite.Connection, *, proposal_id: int,
    target_memory_id: int | None = None, actor: str = "operator",
) -> int:
    actor = _actor(actor)
    try:
        await db.execute("BEGIN IMMEDIATE")
        proposal = await (await db.execute(
            "SELECT * FROM memory_consolidation_proposals WHERE id = ?",
            (proposal_id,),
        )).fetchone()
        if proposal is None:
            raise LookupError(f"consolidation proposal {proposal_id} does not exist")
        if proposal["status"] != "pending":
            raise ValueError(
                f"consolidation proposal {proposal_id} is already {proposal['status']}"
            )
        member_rows = await (await db.execute(
            """SELECT memory_id FROM memory_consolidation_members
               WHERE proposal_id = ? ORDER BY ordinal""",
            (proposal_id,),
        )).fetchall()
        member_ids = [int(row["memory_id"]) for row in member_rows]
        if len(member_ids) < 2:
            raise ValueError("consolidation proposal has fewer than two members")
        target_id = target_memory_id if target_memory_id is not None else member_ids[0]
        if target_id not in member_ids:
            raise ValueError("target memory must be a proposal member")
        source_ids = [memory_id for memory_id in member_ids if memory_id != target_id]
        await merge_user_memories(
            db, target_memory_id=target_id, merged_memory_ids=source_ids,
            text=str(proposal["proposed_text"]),
            memory_type=proposal["proposed_type"],
            confidence=float(proposal["proposed_confidence"]),
            actor=actor, _manage_transaction=False,
        )
        await db.execute(
            """UPDATE memory_consolidation_proposals
               SET status = 'accepted', reviewed_at = CURRENT_TIMESTAMP,
                   reviewed_by = ?
               WHERE id = ?""",
            (actor, proposal_id),
        )
        await db.execute(
            """INSERT INTO audit_events(
                   action, entity_type, entity_id, related_entity_id, actor,
                   new_text, new_type, new_confidence, old_status, new_status
               ) VALUES (
                   'approve', 'consolidation_proposal', ?, ?, ?, ?, ?, ?,
                   'pending', 'accepted'
               )""",
            (
                proposal_id, target_id, actor, proposal["proposed_text"],
                proposal["proposed_type"], proposal["proposed_confidence"],
            ),
        )
        await db.commit()
        return target_id
    except Exception:
        await db.rollback()
        raise


async def scan_consolidation_proposals(
    db: aiosqlite.Connection, *, profile: LLMProfile, bot_id: int,
    user_id: str, actor: str = "operator",
) -> int:
    actor = _actor(actor)
    rows = list(await (await db.execute(
        """SELECT id, memory_text, memory_type, confidence
           FROM user_memories
           WHERE bot_id = ? AND user_id = ? AND state = 'active'
             AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
           ORDER BY id""",
        (bot_id, user_id),
    )).fetchall())
    if len(rows) < 2:
        return 0
    existing = {
        frozenset(proposal.memory_ids)
        for proposal in await list_consolidation_proposals(db, status=None)
        if proposal.bot_id == bot_id and proposal.user_id == user_id
    }
    created = 0
    for batch in _consolidation_batches(rows):
        groups = await _propose_groups(profile, batch)
        valid_ids = {int(row["id"]) for row in batch}
        for group in groups:
            member_ids = list(dict.fromkeys(group.memory_ids))
            member_key = frozenset(member_ids)
            if (
                len(member_ids) < 2
                or not set(member_ids) <= valid_ids
                or member_key in existing
            ):
                continue
            await create_consolidation_proposal(
                db, memory_ids=member_ids,
                proposed_text=group.proposed_text,
                proposed_type=group.proposed_type,
                proposed_confidence=group.proposed_confidence,
                rationale=group.rationale, actor=actor,
            )
            existing.add(member_key)
            created += 1
    return created


async def _propose_groups(
    profile: LLMProfile, rows: list[aiosqlite.Row],
) -> list[ConsolidationGroup]:
    memories = "\n".join(
        f"{row['id']} [{row['memory_type']}, {float(row['confidence']):.2f}]: "
        f"{row['memory_text']}"
        for row in rows
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Review Bottle-owned memories about one person. Identify only groups "
                "that express the same durable belief, relationship, preference, or "
                "project in redundant wording. Same topic is not enough. Do not merge "
                "contradictions, changes over time, or separate events. Return JSON only: "
                '{"groups":[{"memory_ids":[1,2],"proposed_text":"one durable belief",'
                '"proposed_type":"relationship","proposed_confidence":0.8,'
                '"rationale":"why these are redundant"}]}. '
                "Allowed types: preference, project, relationship, identity, "
                "temporary_state. Use an empty groups list when uncertain."
            ),
        },
        {"role": "user", "content": memories},
    ]
    scan_profile = profile.model_copy(update={
        "temperature": 0.0, "max_tokens": 2048,
        "frequency_penalty": 0.0, "presence_penalty": 0.0,
    })
    raw = await complete(scan_profile, messages)
    cleaned = FENCE_RE.sub("", raw.strip())
    try:
        parsed = ConsolidationGroups.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError) as error:
        raise ValueError("invalid consolidation proposal response") from error
    return parsed.groups


def _consolidation_batches(rows: list[aiosqlite.Row]) -> list[list[aiosqlite.Row]]:
    token_counts = Counter(
        token
        for row in rows
        for token in _memory_tokens(str(row["memory_text"]))
    )

    def sort_key(row: aiosqlite.Row) -> tuple[int, str, str]:
        tokens = _memory_tokens(str(row["memory_text"]))
        rarest = min(
            tokens, key=lambda token: (token_counts[token], token),
            default="",
        )
        return (
            token_counts.get(rarest, len(rows) + 1),
            rarest,
            normalize_memory_text(str(row["memory_text"])),
        )

    ordered = sorted(rows, key=sort_key)
    if len(ordered) <= CONSOLIDATION_BATCH_SIZE:
        return [ordered]
    stride = CONSOLIDATION_BATCH_SIZE - CONSOLIDATION_BATCH_OVERLAP
    return [
        ordered[start:start + CONSOLIDATION_BATCH_SIZE]
        for start in range(0, len(ordered), stride)
        if len(ordered[start:start + CONSOLIDATION_BATCH_SIZE]) >= 2
    ]


def _proposal_from_row(row: aiosqlite.Row) -> ConsolidationProposalView:
    values = dict(row)
    values["memory_ids"] = json.loads(values.pop("memory_ids_json") or "[]")
    values["memory_texts"] = json.loads(values.pop("memory_texts_json") or "[]")
    return ConsolidationProposalView(**values)


def _actor(actor: str) -> str:
    actor = actor.strip()
    if not actor:
        raise ValueError("audit actor cannot be empty")
    return actor
