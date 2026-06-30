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
        assert count[0] == 3
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_explicit_uuid_merge_moves_identities(tmp_path) -> None:
    db = await open_database(tmp_path / "merge.db")
    try:
        keep = await resolve_user(db, network="testnet", identity=identity("alice"))
        duplicate = await resolve_user(db, network="othernet", identity=identity("alice"))
        assert keep != duplicate
        await merge_users(db, keep_id=keep, merge_id=duplicate)
        rows = await (await db.execute(
            "SELECT DISTINCT user_id FROM user_identities ORDER BY user_id"
        )).fetchall()
        assert [row[0] for row in rows] == [keep]
    finally:
        await db.close()
