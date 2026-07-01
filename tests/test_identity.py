import asyncio

import pytest

from cellar.identity import merge_users, resolve_user
from cellar.models import IncomingIRCMessage
from cellar.storage import open_database


def identity(nick: str, *, account: str | None = None, hostmask: str | None = None) -> IncomingIRCMessage:
    return IncomingIRCMessage(nick=nick, account=account, hostmask=hostmask,
                              target="#test", body="hello")


@pytest.mark.asyncio
async def test_account_merges_nick_changes_into_one_uuid(tmp_path) -> None:
    db = await open_database(tmp_path / "identity.db")
    try:
        first = await resolve_user(
            db, network="testnet", identity=identity("alice", hostmask="u@host")
        )
        linked = await resolve_user(
            db, network="testnet",
            identity=identity("alice", account="alice_account", hostmask="u@host"),
        )
        renamed = await resolve_user(
            db, network="testnet",
            identity=identity("newnick", account="alice_account", hostmask="other@host"),
        )
        assert first == linked == renamed
        count = await (await db.execute(
            "SELECT COUNT(*) FROM user_identities WHERE user_id = ?", (first,)
        )).fetchone()
        assert count is not None
        assert count[0] == 3
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_unverified_nick_does_not_inherit_account_identity(tmp_path) -> None:
    db = await open_database(tmp_path / "nick-impersonation.db")
    try:
        owner = await resolve_user(
            db, network="testnet",
            identity=identity("aureate", account="aureate", hostmask="owner@host"),
        )
        impostor = await resolve_user(
            db, network="testnet",
            identity=identity("AUREATE", hostmask="stranger@elsewhere"),
        )
        assert impostor != owner
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_explicit_uuid_merge_moves_identities(tmp_path) -> None:
    db = await open_database(tmp_path / "merge.db")
    try:
        keep = await resolve_user(db, network="testnet", identity=identity("alice"))
        duplicate = await resolve_user(db, network="othernet", identity=identity("alice"))
        assert keep != duplicate
        await db.execute(
            """INSERT INTO irc_profiles(
                   network, host, nick, username, realname, channels
               ) VALUES ('othernet', 'localhost', 'ghost', 'ghost', 'Ghost', '[]')"""
        )
        await db.execute(
            "INSERT INTO llm_profiles(endpoint, model) VALUES ('http://localhost', 'test')"
        )
        await db.execute(
            """INSERT INTO bots(name, soul_prompt_path, llm_profile_id, irc_profile_id)
               VALUES ('test', 'soul.md', 1, 1)"""
        )
        message = await db.execute(
            """INSERT INTO messages(network, channel, speaker, body, bot_id, user_id)
               VALUES ('othernet', '#test', 'alice', 'likes tea', 1, ?)""", (duplicate,),
        )
        candidate = await db.execute(
            """INSERT INTO memory_candidates(
                   user_id, source_message_id, candidate_text, memory_type, confidence
               ) VALUES (?, ?, 'likes tea', 'preference', 0.9)""",
            (duplicate, message.lastrowid),
        )
        await db.execute(
            """INSERT INTO user_memories(
                   user_id, source_candidate_id, memory_text, memory_type, confidence
               ) VALUES (?, ?, 'likes tea', 'preference', 0.9)""",
            (duplicate, candidate.lastrowid),
        )
        await db.commit()
        await merge_users(db, keep_id=keep, merge_id=duplicate)
        rows = await (await db.execute(
            "SELECT DISTINCT user_id FROM user_identities ORDER BY user_id"
        )).fetchall()
        assert [row[0] for row in rows] == [keep]
        candidate_owner = await (await db.execute(
            "SELECT user_id FROM memory_candidates"
        )).fetchone()
        memory_owner = await (await db.execute(
            "SELECT user_id FROM user_memories"
        )).fetchone()
        assert candidate_owner is not None and candidate_owner[0] == keep
        assert memory_owner is not None and memory_owner[0] == keep
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_concurrent_connections_resolve_one_uuid(tmp_path) -> None:
    database = tmp_path / "concurrent-identity.db"
    first_db = await open_database(database)
    second_db = await open_database(database)
    try:
        first, second = await asyncio.gather(
            resolve_user(
                first_db, network="testnet",
                identity=identity("alice", account="alice_account", hostmask="u@host"),
            ),
            resolve_user(
                second_db, network="testnet",
                identity=identity("alice", account="alice_account", hostmask="u@host"),
            ),
        )
        assert first == second
        row = await (await first_db.execute("SELECT COUNT(*) FROM users")).fetchone()
        assert row is not None and row[0] == 1
    finally:
        await first_db.close()
        await second_db.close()
