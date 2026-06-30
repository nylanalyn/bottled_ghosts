import pytest

from cellar.models import Bottle, IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleContext, ModuleRunner, NightlyContext
from cellar.module_loader import load_modules
from cellar.module_store import module_states, set_module_enabled
from cellar.storage import create_bottle, open_database


class BrokenModule:
    async def on_message(self, ctx: ModuleContext) -> None:
        raise RuntimeError("broken")

    async def before_prompt(self, ctx: ModuleContext) -> None:
        raise RuntimeError("broken")

    async def after_response(self, ctx: ModuleContext) -> None:
        raise RuntimeError("broken")

    async def nightly(self, ctx: NightlyContext) -> None:
        raise RuntimeError("broken")


class WorkingModule:
    async def on_message(self, ctx: ModuleContext) -> None:
        ctx.prompt_sections.append("on_message continued")

    async def before_prompt(self, ctx: ModuleContext) -> None:
        return None

    async def after_response(self, ctx: ModuleContext) -> None:
        return None

    async def nightly(self, ctx: NightlyContext) -> None:
        return None


def bottle(tmp_path) -> Bottle:
    return Bottle(
        id=1, name="test", soul_prompt_path=tmp_path / "soul.md",
        irc=IRCProfile(network="local", host="irc.example", nick="ghost",
                       username="ghost", realname="Ghost", channels=["#test"]),
        llm=LLMProfile(endpoint="http://localhost", model="test"),
    )


@pytest.mark.asyncio
async def test_module_failure_does_not_stop_later_modules(tmp_path, caplog) -> None:
    ctx = ModuleContext(
        db=object(),  # type: ignore[arg-type]
        bottle=bottle(tmp_path),
        message=IncomingIRCMessage(nick="alice", hostmask=None, account=None,
                                   target="#test", body="hello"),
        user_id="user", source_message_id=1,
    )
    await ModuleRunner([BrokenModule(), WorkingModule()]).on_message(ctx)
    assert ctx.prompt_sections == ["on_message continued"]
    assert "failed during on_message" in caplog.text


@pytest.mark.asyncio
async def test_enabled_module_loads_from_sqlite(tmp_path) -> None:
    db = await open_database(tmp_path / "modules.db")
    try:
        configured = bottle(tmp_path)
        bottle_id = await create_bottle(
            db, name=configured.name, soul_prompt_path=configured.soul_prompt_path,
            irc=configured.irc, llm=configured.llm,
        )
        await set_module_enabled(
            db, bottle_id=bottle_id, module_name="channel_context", enabled=True,
        )
        assert await module_states(db, bottle_id=bottle_id) == {"channel_context": True}
        runner = await load_modules(db, bottle_id=bottle_id)
        assert [type(module).__module__ for module in runner.modules] == [
            "modules.channel_context"
        ]
    finally:
        await db.close()
