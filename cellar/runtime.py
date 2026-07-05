import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from cellar.irc import IRCAuthenticationError, IRCClient, irc_casefold, mentions_any_nick
from cellar.admin_store import response_enabled
from cellar.identity import resolve_user_identity
from cellar.ignore_store import matching_ignore_action
from cellar.listening import ListeningWindowManager
from cellar.llm import complete
from cellar.memory import extract_candidates
from cellar.memory_store import approved_memory_texts, store_memory_candidates
from cellar.dream_store import recent_dream_texts
from cellar.models import Bottle, IRCMessage, IncomingIRCMessage
from cellar.module_api import (
    ModuleCommand,
    ModuleContext,
    ModuleRunner,
    RuntimeContext,
    RuntimeState,
)
from cellar.module_loader import load_modules
from cellar.prompt import build_prompt, read_soul
from cellar.safety import Cooldown, sanitize
from cellar.storage import log_message, open_database, recent_messages, search_messages

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WindowMessage:
    message: IncomingIRCMessage
    user_id: str
    message_id: int
    conversation: str
    addressed: bool
    identity_confidence: float


async def run_bottle_once(
    db: aiosqlite.Connection, bottle: Bottle,
    modules: ModuleRunner | None = None, runtime_state: RuntimeState | None = None,
) -> None:
    soul = read_soul(bottle.soul_prompt_path)
    cooldown = Cooldown(bottle.cooldown_seconds)
    modules = modules or await load_modules(db, bottle_id=bottle.id)
    database_lock = runtime_state.database_lock if runtime_state is not None else asyncio.Lock()
    client: IRCClient

    def active_nick() -> str:
        return getattr(client, "current_nick", bottle.irc.nick)

    async def respond(items: tuple[WindowMessage, ...]) -> None:
        latest = items[-1]
        message = latest.message
        user_id = latest.user_id
        message_ids = [item.message_id for item in items]
        body = "\n".join(item.message.body for item in items)
        speaker, channel = message.nick, latest.conversation
        direct_message = irc_casefold(message.target) == irc_casefold(active_nick())
        reply_target = speaker if direct_message else message.target
        module_context = ModuleContext(
            db=db, bottle=bottle, message=message, user_id=user_id,
            source_message_id=latest.message_id,
            conversation=channel, bot_nick=active_nick(),
            response_reason=(
                "addressed" if any(item.addressed for item in items) else "ambient"
            ),
        )
        logger.info("generating reply to %s in %s", speaker, reply_target)
        async with database_lock:
            history = await recent_messages(
                db, bot_id=bottle.id, network=bottle.irc.network, channel=channel,
                exclude_message_ids=message_ids,
            )
            relevant = await search_messages(
                db, bot_id=bottle.id, network=bottle.irc.network, channel=channel,
                text=body, exclude_message_ids=message_ids,
            )
            memories = (
                await approved_memory_texts(db, user_id=user_id)
                if latest.identity_confidence >= 0.8 else []
            )
            dreams = await recent_dream_texts(db, bot_id=bottle.id)
            await modules.before_prompt(module_context)
        prompt = build_prompt(
            soul=soul, module_state=module_context.prompt_sections, memories=memories,
            dreams=dreams, relevant=relevant, history=history, speaker=speaker, body=body,
            bot_nicks=(active_nick(),),
        )
        response = await complete(bottle.llm, prompt)
        module_context.response = response
        async with database_lock:
            await modules.after_response(module_context)
            replies_enabled = await response_enabled(db, bottle_id=bottle.id)
        lines = sanitize(
            module_context.response or "", max_lines=bottle.max_lines,
            max_chars=bottle.max_chars,
        )
        if not replies_enabled:
            lines = []
        if not lines:
            logger.warning("LLM response was empty after sanitization")
        for line in lines:
            await cooldown.wait()
            await client.send_message(reply_target, line)
            async with database_lock:
                await log_message(
                    db, IRCMessage(network=bottle.irc.network, channel=channel,
                                   speaker=active_nick(), body=line, bot_id=bottle.id),
                )
        logger.info("sent %d reply line(s) to %s", len(lines), reply_target)
        if bottle.extract_memories and replies_enabled:
            try:
                candidates = await extract_candidates(bottle.llm, speaker=speaker, body=body)
                async with database_lock:
                    inserted = await store_memory_candidates(
                        db, user_id=user_id, source_message_ids=message_ids,
                        candidates=candidates,
                    )
                logger.info("stored %d pending memory candidate(s) for %s", inserted, speaker)
            except Exception:
                logger.exception("memory extraction failed for message %d", latest.message_id)

    async def fire_window(items: tuple[WindowMessage, ...]) -> None:
        try:
            await respond(items)
        except asyncio.CancelledError:
            raise
        except Exception:
            latest = items[-1]
            logger.exception(
                "failed to respond to listening window ending at message %d",
                latest.message_id,
            )

    windows = ListeningWindowManager[WindowMessage](
        bottle.listen_window_seconds, fire_window
    )

    async def send_module_commands(
        commands: list[ModuleCommand], *, target: str, channel: str,
    ) -> None:
        # A single incoming event may cause at most one module command. This keeps
        # module bugs from becoming IRC floods even when several modules are active.
        for command in commands[:1]:
            lines = sanitize(command.body, max_lines=1, max_chars=bottle.max_chars)
            if not lines or not lines[0].startswith("!"):
                logger.warning("discarding invalid module command")
                continue
            await cooldown.wait()
            await client.send_message(target, lines[0])
            async with database_lock:
                await log_message(
                    db, IRCMessage(
                        network=bottle.irc.network, channel=channel,
                        speaker=active_nick(), body=lines[0], bot_id=bottle.id,
                    ),
                )

    async def on_message(message: IncomingIRCMessage) -> None:
        async with database_lock:
            ignore_action = await matching_ignore_action(
                db, bottle_id=bottle.id, network=bottle.irc.network, identity=message,
            )
            if ignore_action == "drop":
                logger.info("dropping ignored message from %s", message.nick)
                return
            resolved = await resolve_user_identity(
                db, network=bottle.irc.network, identity=message,
            )
            user_id = resolved.user_id
            direct_message = irc_casefold(message.target) == irc_casefold(active_nick())
            conversation = f"@{user_id}" if direct_message else message.target
            incoming = IRCMessage(
                network=bottle.irc.network, channel=conversation, speaker=message.nick,
                body=message.body, bot_id=bottle.id, user_id=user_id,
            )
            message_id = await log_message(db, incoming)
            module_context = ModuleContext(
                db=db, bottle=bottle, message=message, user_id=user_id,
                source_message_id=message_id, conversation=conversation,
                bot_nick=active_nick(), response_allowed=ignore_action is None,
            )
            await modules.on_message(module_context)
            commands = list(module_context.commands)
            replies_enabled = await response_enabled(db, bottle_id=bottle.id)
        if commands:
            await send_module_commands(
                commands, target=message.target, channel=conversation,
            )
        if ignore_action == "no_response":
            return
        key = (irc_casefold(conversation), user_id)
        address_names = (active_nick(), *bottle.address_names)
        addressed = direct_message or mentions_any_nick(message.body, address_names)
        should_respond = windows.contains(key) or addressed or module_context.request_response
        should_monitor = not replies_enabled and module_context.monitor_when_silent
        if (replies_enabled and should_respond) or should_monitor:
            windows.add(
                key, WindowMessage(
                    message=message, user_id=user_id, message_id=message_id,
                    conversation=conversation, addressed=addressed,
                    identity_confidence=resolved.confidence,
                )
            )

    client = IRCClient(bottle.irc, on_message)
    if runtime_state is not None:
        client.connection_state_handler = lambda connected: setattr(
            runtime_state, "irc_connected", connected
        )
    try:
        await client.run()
    finally:
        await windows.close()


async def run_bottle(db: aiosqlite.Connection, bottle: Bottle) -> None:
    runtime_state = RuntimeState()
    services = await load_modules(db, bottle_id=bottle.id)
    runtime_context = RuntimeContext(
        db=db, bottle=bottle, database_lock=runtime_state.database_lock, state=runtime_state,
    )
    delay = 1.0
    try:
        await services.start(runtime_context)
        while True:
            started_at = time.monotonic()
            try:
                await run_bottle_once(db, bottle, services, runtime_state)
            except asyncio.CancelledError:
                logger.info("stopping Bottle %d (%s)", bottle.id, bottle.name)
                raise
            except IRCAuthenticationError:
                runtime_state.irc_connected = False
                logger.exception(
                    "Bottle %d (%s) authentication failed; stopping until configuration changes",
                    bottle.id, bottle.name,
                )
                raise
            except Exception:
                runtime_state.irc_connected = False
                if time.monotonic() - started_at >= 30.0:
                    delay = 1.0
                logger.exception("Bottle %d (%s) disconnected; retrying in %.0fs",
                                 bottle.id, bottle.name, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
    finally:
        runtime_state.irc_connected = False
        await services.stop(runtime_context)


async def run_bottle_from_database(database: Path, bottle: Bottle) -> None:
    db = await open_database(database)
    try:
        await run_bottle(db, bottle)
    finally:
        await db.close()


async def run_bottles(database: Path, bottles: list[Bottle]) -> None:
    if not bottles:
        raise ValueError("no enabled Bottles are configured")
    logger.info("starting %d Bottle(s)", len(bottles))
    async with asyncio.TaskGroup() as tasks:
        for bottle in bottles:
            tasks.create_task(
                run_bottle_from_database(database, bottle), name=f"bottle-{bottle.id}"
            )
