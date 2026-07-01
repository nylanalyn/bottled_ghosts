import pytest

from cellar.alias_store import add_alias, delete_alias, list_aliases
from cellar.irc import mentions_any_nick
from cellar.models import IRCProfile, LLMProfile
from cellar.storage import create_bottle, load_bottle, open_database


async def test_aliases_are_persistent_audited_and_irc_casefolded(tmp_path) -> None:
    db = await open_database(tmp_path / "aliases.db")
    soul = tmp_path / "soul.md"
    soul.write_text("Be concise.", encoding="utf-8")
    try:
        bottle_id = await create_bottle(
            db, name="frauderick", soul_prompt_path=soul,
            irc=IRCProfile(
                network="test", host="localhost", nick="Frauderick",
                username="fraud", realname="Frauderick", channels=["#test"],
            ),
            llm=LLMProfile(endpoint="http://localhost/chat", model="test"),
        )
        assert await add_alias(
            db, bottle_id=bottle_id, alias="Fraud", actor="test",
        )
        assert not await add_alias(
            db, bottle_id=bottle_id, alias="fraud", actor="test",
        )
        bottle = await load_bottle(db, bottle_id)
        assert bottle.aliases == ["Fraud"]
        assert mentions_any_nick("fraud, are you there?", bottle.address_names)
        assert not mentions_any_nick("this is fraudulent", bottle.address_names)
        assert await list_aliases(db, bottle_id=bottle_id) == ["Fraud"]
        assert await delete_alias(
            db, bottle_id=bottle_id, alias="FRAUD", actor="test",
        )
        assert await list_aliases(db, bottle_id=bottle_id) == []
        events = await (await db.execute(
            """SELECT changed_fields FROM configuration_events
               WHERE changed_fields LIKE 'alias:%' ORDER BY id"""
        )).fetchall()
        assert [row["changed_fields"] for row in events] == [
            "alias:add", "alias:delete",
        ]
    finally:
        await db.close()


async def test_alias_rejects_nickname_and_invalid_text(tmp_path) -> None:
    db = await open_database(tmp_path / "invalid-alias.db")
    soul = tmp_path / "soul.md"
    soul.write_text("Be concise.", encoding="utf-8")
    try:
        bottle_id = await create_bottle(
            db, name="rumi", soul_prompt_path=soul,
            irc=IRCProfile(
                network="test", host="localhost", nick="rumi-as",
                username="rumi", realname="Rumi", channels=["#test"],
            ),
            llm=LLMProfile(endpoint="http://localhost/chat", model="test"),
        )
        with pytest.raises(ValueError, match="duplicates"):
            await add_alias(db, bottle_id=bottle_id, alias="RUMI-AS")
        with pytest.raises(ValueError, match="nickname characters"):
            await add_alias(db, bottle_id=bottle_id, alias="rumi as")
    finally:
        await db.close()
