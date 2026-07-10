import asyncio
import socket

from aiohttp import ClientSession

from cellar.admin_store import consume_admin_events, enqueue_admin_event, response_enabled, set_admin_api_token, set_response_enabled
from cellar.models import IncomingIRCMessage, IRCProfile, LLMProfile
from cellar.module_api import ModuleContext, RuntimeContext, RuntimeState
from cellar.module_store import set_module_enabled
from cellar.storage import create_bottle, load_bottle, open_database
from cellar.storage import log_message
from cellar.models import IRCMessage
from modules.admin_api import Module as AdminAPIModule, _active_module_names
from modules.emergency_alert import Module as EmergencyAlertModule


def test_active_module_names_excludes_failed_modules() -> None:
    assert _active_module_names(
        {
            "moods": {},
            "admin_api": {},
            "emergency_alert": {},
        },
        {"emergency_alert": "on_message"},
    ) == ("admin_api", "moods")


async def _bottle(db, tmp_path):
    soul = tmp_path / "soul.md"
    soul.write_text("Be concise.", encoding="utf-8")
    bottle_id = await create_bottle(
        db, name="rumi", soul_prompt_path=soul,
        irc=IRCProfile(network="test", host="localhost", nick="rumi-as", username="rumi", realname="Rumi", channels=["#test"]),
        llm=LLMProfile(endpoint="http://localhost/chat", model="test-model"),
    )
    return await load_bottle(db, bottle_id)


async def test_response_control_and_event_delivery_are_persistent(tmp_path) -> None:
    db = await open_database(tmp_path / "admin.db")
    try:
        bottle = await _bottle(db, tmp_path)
        assert await asyncio.wait_for(response_enabled(db, bottle_id=bottle.id), 1)
        assert await asyncio.wait_for(
            set_response_enabled(db, bottle_id=bottle.id, enabled=False), 1,
        )
        assert not await asyncio.wait_for(response_enabled(db, bottle_id=bottle.id), 1)
        assert await asyncio.wait_for(
            enqueue_admin_event(
                db, bottle_id=bottle.id, event_type="emergency", message="alert"
            ),
            1,
        )
        delivered = await asyncio.wait_for(
            consume_admin_events(db, bottle_id=bottle.id), 1,
        )
        assert [event["message"] for event in delivered] == ["alert"]
        assert await consume_admin_events(db, bottle_id=bottle.id) == []
        await asyncio.wait_for(
            set_admin_api_token(
                db, bottle_id=bottle.id, token="never-audited", actor="test",
            ),
            1,
        )
        audit = await (await db.execute(
            "SELECT new_value FROM configuration_events ORDER BY id DESC LIMIT 1"
        )).fetchone()
        assert audit is not None
        assert "never-audited" not in str(audit["new_value"])
        assert audit["new_value"] is None
    finally:
        await db.close()


async def test_emergency_module_requires_address_and_deduplicates_source(tmp_path) -> None:
    db = await open_database(tmp_path / "emergency.db")
    try:
        bottle = await _bottle(db, tmp_path)
        module = EmergencyAlertModule()
        settings = {"emergency_alert": {"discord_user_id": "12345"}}
        ordinary = ModuleContext(db=db, bottle=bottle, message=IncomingIRCMessage(nick="alice", hostmask=None, account=None, target="#test", body="everything is normal"), user_id="user", source_message_id=1, module_settings=settings)
        await module.on_message(ordinary)
        assert not ordinary.monitor_when_silent
        source_message_id = await log_message(
            db, IRCMessage(network="test", channel="#test", speaker="alice",
                           body="rumi-as! bots are going insane", bot_id=bottle.id),
        )
        addressed = ModuleContext(db=db, bottle=bottle, message=IncomingIRCMessage(nick="alice", hostmask=None, account=None, target="#test", body="rumi-as! bots are going insane"), user_id="user", source_message_id=2, module_settings=settings, response="[URGENT: bots are flooding the channel]\ni am looking into it")
        addressed.source_message_id = source_message_id
        await module.on_message(addressed)
        assert addressed.monitor_when_silent
        await module.after_response(addressed)
        assert addressed.response == "i am looking into it"
        await module.after_response(addressed)
        events = await consume_admin_events(db, bottle_id=bottle.id)
        assert len(events) == 1
        assert "<@12345>" in str(events[0]["message"])
        assert "#test <alice>" in str(events[0]["message"])
    finally:
        await db.close()


async def test_admin_api_matches_legacy_contract(tmp_path) -> None:
    db = await open_database(tmp_path / "api.db")
    try:
        bottle = await _bottle(db, tmp_path)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        state = RuntimeState(irc_connected=True)
        context = RuntimeContext(db=db, bottle=bottle, database_lock=state.database_lock, state=state, module_settings={"admin_api": {"host": "127.0.0.1", "port": port, "token": "secret"}})
        module = AdminAPIModule()
        await set_admin_api_token(db, bottle_id=bottle.id, token="secret", actor="test")
        await module.start(context)
        try:
            async with ClientSession() as session:
                assert await (await session.get(f"http://127.0.0.1:{port}/health")).json() == {"ok": True}
                denied = await session.post(f"http://127.0.0.1:{port}/v1/command", json={"command": "status"})
                assert denied.status == 401
                headers = {"Authorization": "Bearer secret"}
                status = await session.post(f"http://127.0.0.1:{port}/v1/command", json={"command": "status", "args": ""}, headers=headers)
                status_json = (await status.json())["messages"]
                assert "irc: connected" in status_json[0]
                assert "modules: admin_api" in status_json[0]
                # With no mood_state row yet, status omits the mood block.
                assert all("valence" not in m for m in status_json)

                # Persist a mood reading, then status surfaces the mood block.
                await db.execute(
                    """INSERT INTO mood_state(
                           bot_id, valence, irritability, interaction_heat,
                           last_interaction_at, updated_at, last_event,
                           last_valence_delta, last_irritability_delta
                       ) VALUES (?, 0.4, -0.2, 3.0,
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'interaction', 0.4, -0.2)""",
                    (bottle.id,),
                )
                await db.commit()
                await set_module_enabled(
                    db, bottle_id=bottle.id, module_name="moods", enabled=True,
                    actor="test",
                )
                status2 = await session.post(f"http://127.0.0.1:{port}/v1/command", json={"command": "status", "args": ""}, headers=headers)
                status2_json = (await status2.json())["messages"]
                mood_messages = [m for m in status2_json if "valence" in m]
                assert len(mood_messages) == 1
                assert mood_messages[0].startswith("```")
                assert "irritability" in mood_messages[0]
                assert "heat" in mood_messages[0]

                await set_module_enabled(
                    db, bottle_id=bottle.id, module_name="moods", enabled=False,
                    actor="test",
                )
                status3 = await session.post(f"http://127.0.0.1:{port}/v1/command", json={"command": "status", "args": ""}, headers=headers)
                status3_json = (await status3.json())["messages"]
                assert all("valence" not in m for m in status3_json)

                off = await session.post(f"http://127.0.0.1:{port}/v1/command", json={"command": "off", "args": ""}, headers=headers)
                assert off.status == 200
                assert not await response_enabled(db, bottle_id=bottle.id)
        finally:
            await module.stop(context)
            await asyncio.sleep(0)
    finally:
        await db.close()
