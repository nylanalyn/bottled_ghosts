import pytest

from cellar.ignore_store import (
    add_ignore_rule,
    delete_ignore_rule,
    list_ignore_rules,
    matching_ignore_action,
)
from cellar.models import IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.storage import create_bottle, open_database


@pytest.mark.asyncio
async def test_ignore_rules_match_and_are_audited(tmp_path) -> None:
    db = await open_database(tmp_path / "ignore.db")
    try:
        bottle_id = await create_bottle(
            db, name="test", soul_prompt_path=tmp_path / "SOUL.md",
            irc=IRCProfile(network="testnet", host="localhost", nick="ghost",
                           username="ghost", realname="Ghost", channels=["#test"]),
            llm=LLMProfile(endpoint="http://localhost", model="test"),
        )
        rule_id, created = await add_ignore_rule(
            db, bottle_id=bottle_id, network="testnet", match_type="nick",
            match_value="[OtherBot]", action="no_response", actor="tester",
        )
        assert created is True
        assert await add_ignore_rule(
            db, bottle_id=bottle_id, network="TESTNET", match_type="nick",
            match_value="{otherbot}", action="no_response", actor="tester",
        ) == (rule_id, False)
        identity = IncomingIRCMessage(
            nick="{OTHERBOT}", hostmask="bot@host", account=None,
            target="#test", body="ghost: hello",
        )
        assert await matching_ignore_action(
            db, bottle_id=bottle_id, network="testnet", identity=identity,
        ) == "no_response"
        drop_id, _ = await add_ignore_rule(
            db, bottle_id=bottle_id, network="testnet", match_type="hostmask",
            match_value="bot@host", action="drop", actor="tester",
        )
        assert await matching_ignore_action(
            db, bottle_id=bottle_id, network="testnet", identity=identity,
        ) == "drop"
        assert [rule.id for rule in await list_ignore_rules(db, bottle_id=bottle_id)] == [
            rule_id, drop_id,
        ]
        await delete_ignore_rule(
            db, bottle_id=bottle_id, rule_id=drop_id, actor="tester",
        )
        events = await (await db.execute(
            "SELECT changed_fields FROM configuration_events ORDER BY id"
        )).fetchall()
        assert [row[0] for row in events] == [
            "created", "ignore_rule:added", "ignore_rule:added", "ignore_rule:deleted",
        ]
    finally:
        await db.close()
