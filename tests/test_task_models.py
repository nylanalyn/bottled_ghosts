import argparse
import asyncio

import pytest

from cellar.cli import async_main
from cellar.dreams import run_dream
from cellar.models import Bottle, IRCMessage, IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleRunner
from cellar.runtime import run_bottle_once
from cellar.storage import (
    create_bottle, load_bottle, load_enabled_bottles, log_message, open_database,
    set_task_model,
)


def _bottle(**task_models: str) -> Bottle:
    return Bottle(
        id=1, name="ghost", soul_prompt_path="soul.md",
        irc=IRCProfile(network="local", host="irc", nick="ghost", username="ghost",
                       realname="Ghost", channels=["#one"]),
        llm=LLMProfile(endpoint="http://localhost", model="main-model", api_key="k"),
        task_models=task_models,
    )


def test_task_profiles_fall_back_to_the_main_model() -> None:
    bottle = _bottle(reply="chat-model", dream="big-model")
    assert bottle.llm_for("reply").model == "chat-model"
    assert bottle.llm_for("initiative").model == "chat-model"
    assert bottle.llm_for("dream").model == "big-model"
    assert bottle.llm_for("recollection").model == "main-model"
    assert bottle.llm_for("dream").api_key == "k"
    assert bottle.llm_for("dream").endpoint == "http://localhost"
    assert _bottle(initiative="x", reply="y").llm_for("initiative").model == "x"
    with pytest.raises(ValueError, match="unknown LLM task"):
        bottle.llm_for("vibes")


async def _create(db, tmp_path) -> int:
    soul = tmp_path / "soul.md"
    soul.write_text("Be concise.", encoding="utf-8")
    return await create_bottle(
        db, name="ghost", soul_prompt_path=soul,
        irc=IRCProfile(network="local", host="irc", nick="ghost", username="ghost",
                       realname="Ghost", channels=["#one"]),
        llm=LLMProfile(endpoint="http://localhost", model="main-model"),
        cooldown_seconds=0, listen_window_seconds=0.01,
    )


@pytest.mark.asyncio
async def test_overrides_are_audited_loaded_and_clearable(tmp_path) -> None:
    db = await open_database(tmp_path / "tasks.db")
    try:
        bottle_id = await _create(db, tmp_path)
        assert await set_task_model(
            db, bottle_id=bottle_id, task="dream", model="glm-5.3", actor="tester",
        )
        assert not await set_task_model(
            db, bottle_id=bottle_id, task="dream", model="glm-5.3", actor="tester",
        )
        assert (await load_bottle(db, bottle_id)).task_models == {"dream": "glm-5.3"}
        assert (await load_enabled_bottles(db))[0].task_models == {"dream": "glm-5.3"}
        assert await set_task_model(
            db, bottle_id=bottle_id, task="dream", model=None, actor="tester",
        )
        assert (await load_bottle(db, bottle_id)).task_models == {}
        events = await (await db.execute(
            """SELECT changed_fields, old_value, new_value FROM configuration_events
               WHERE changed_fields LIKE 'task_model:%' ORDER BY id"""
        )).fetchall()
        assert [tuple(row) for row in events] == [
            ("task_model:dream", "null", '"glm-5.3"'),
            ("task_model:dream", '"glm-5.3"', "null"),
        ]
        with pytest.raises(ValueError, match="task must be"):
            await set_task_model(db, bottle_id=bottle_id, task="vibes", model="x")
        with pytest.raises(ValueError, match="without spaces"):
            await set_task_model(db, bottle_id=bottle_id, task="reply", model="two words")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_replies_and_dreams_use_their_task_models(monkeypatch, tmp_path) -> None:
    db = await open_database(tmp_path / "use.db")
    try:
        bottle_id = await _create(db, tmp_path)
        await set_task_model(db, bottle_id=bottle_id, task="reply", model="chat-model")
        await set_task_model(db, bottle_id=bottle_id, task="dream", model="dream-model")
        bottle = await load_bottle(db, bottle_id)
        used: list[str] = []

        class FakeIRCClient:
            def __init__(self, _profile, handler) -> None:
                self.handler = handler

            async def run(self) -> None:
                await self.handler(IncomingIRCMessage(
                    nick="alice", hostmask="a@h", account="alice",
                    target="#one", body="ghost: hello",
                ))
                await asyncio.sleep(0.05)

            async def send_message(self, _target: str, _body: str) -> None:
                return None

        async def fake_complete(profile, _prompt, **_kwargs) -> str:
            used.append(profile.model)
            return "hi"

        monkeypatch.setattr("cellar.runtime.IRCClient", FakeIRCClient)
        monkeypatch.setattr("cellar.runtime.complete", fake_complete)
        monkeypatch.setattr("cellar.dreams.complete", fake_complete)
        await run_bottle_once(db, bottle, ModuleRunner([]))
        await log_message(db, IRCMessage(
            network="local", channel="#one", speaker="alice", body="a day happened",
            bot_id=bottle_id,
        ))
        await run_dream(db, bottle=bottle)
        assert used == ["chat-model", "dream-model"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cli_sets_and_lists_task_models(tmp_path, capsys) -> None:
    database = tmp_path / "cli.db"
    db = await open_database(database)
    try:
        bottle_id = await _create(db, tmp_path)
    finally:
        await db.close()
    base = {"database": database, "bottle_id": bottle_id, "actor": "tester"}
    await async_main(argparse.Namespace(
        command="task-model", task="recollection", model="cheap-model", clear=False, **base,
    ))
    await async_main(argparse.Namespace(
        command="task-model", task="reply", model="chat-model", clear=False, **base,
    ))
    await async_main(argparse.Namespace(command="task-models", **base))
    output = capsys.readouterr().out
    assert "recollection\tcheap-model\toverride" in output
    assert "initiative\tchat-model\tfallback" in output
    assert "dream\tmain-model\tmain" in output
    with pytest.raises(ValueError, match="--clear"):
        await async_main(argparse.Namespace(
            command="task-model", task="reply", model=None, clear=False, **base,
        ))
