import pytest

from cellar.models import Bottle, IRCProfile, IncomingIRCMessage, LLMProfile
from cellar.module_api import ModuleContext
from modules.reflection import Module


def _context(tmp_path) -> ModuleContext:
    soul = tmp_path / "soul.md"
    soul.write_text("Be reflective.", encoding="utf-8")
    bottle = Bottle(
        id=1, name="aria", soul_prompt_path=soul,
        irc=IRCProfile(network="local", host="irc.example", nick="aria",
                       username="aria", realname="Aria", channels=["#test"]),
        llm=LLMProfile(endpoint="http://localhost", model="test"),
    )
    return ModuleContext(
        db=None,  # type: ignore[arg-type]
        bottle=bottle,
        message=IncomingIRCMessage(
            nick="alice", hostmask="u@h", account="alice",
            target="#test", body="I fixed the telescope",
        ),
        user_id="alice-id", source_message_id=1,
        generation_prompt=[
            {"role": "system", "content": "Character rules."},
            {"role": "user", "content": "Current message."},
        ],
    )


@pytest.mark.asyncio
async def test_reflection_appends_private_notes_to_final_prompt(tmp_path, monkeypatch) -> None:
    ctx = _context(tmp_path)
    calls = []

    async def fake_complete(profile, messages):
        calls.append((profile, messages))
        return "The telescope callback matters; answer warmly and briefly."

    monkeypatch.setattr("modules.reflection.complete", fake_complete)
    await Module().before_generation(ctx)

    assert len(calls) == 1
    assert "private reflection pass" in calls[0][1][0]["content"]
    assert "The telescope callback matters" not in calls[0][1][0]["content"]
    assert "The telescope callback matters" in ctx.generation_prompt[0]["content"]
    assert calls[0][0].max_tokens == 160
    assert calls[0][0].temperature == 0.2


@pytest.mark.asyncio
async def test_reflection_failure_keeps_original_prompt(tmp_path, monkeypatch) -> None:
    ctx = _context(tmp_path)
    original = [dict(message) for message in ctx.generation_prompt]

    async def failing_complete(_profile, _messages):
        raise RuntimeError("reflection endpoint unavailable")

    monkeypatch.setattr("modules.reflection.complete", failing_complete)
    await Module().before_generation(ctx)

    assert ctx.generation_prompt == original
