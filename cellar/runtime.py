import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from cellar.irc import IRCClient, irc_casefold, mentions_nick
from cellar.identity import resolve_user
from cellar.listening import ListeningWindowManager
from cellar.llm import complete
from cellar.memory import extract_candidates
from cellar.memory_store import approved_memory_texts, store_memory_candidates
from cellar.dream_store import recent_dream_texts
from cellar.models import Bottle, IRCMessage, IncomingIRCMessage
from cellar.module_api import ModuleContext
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


async def run_bottle_once(db: aiosqlite.Connection, bottle: Bottle) -> None:
    soul = read_soul(bottle.soul_prompt_path)
    cooldown = Cooldown(bottle.cooldown_seconds)
    modules = await load_modules(db, bottle_id=bottle.id)
    database_lock = asyncio.Lock()
    client: IRCClient

    async def respond(items: tuple[WindowMessage, ...]) -> None:
        latest = items[-1]
        message = latest.message
        user_id = latest.user_id
        message_ids = [item.message_id for item in items]
        body = "\n".join(item.message.body for item in items)
        speaker, channel = message.nick, latest.conversation
        direct_message = irc_casefold(message.target) == irc_casefold(bottle.irc.nick)
        reply_target = speaker if direct_message else message.target
        module_context = ModuleContext(
            db=db, bottle=bottle, message=message, user_id=user_id,
            source_message_id=latest.message_id,
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
            memories = await approved_memory_texts(db, user_id=user_id)
            dreams = await recent_dream_texts(db, bot_id=bottle.id)
            await modules.before_prompt(module_context)
        prompt = build_prompt(
            soul=soul, module_state=module_context.prompt_sections, memories=memories,
            dreams=dreams, relevant=relevant, history=history, speaker=speaker, body=body,
        )
        response = await complete(bottle.llm, prompt)
        module_context.response = response
        async with database_lock:
            await modules.after_response(module_context)
        lines = sanitize(response, max_lines=bottle.max_lines, max_chars=bottle.max_chars)
        if not lines:
            logger.warning("LLM response was empty after sanitization")
        for line in lines:
            await cooldown.wait()
            await client.send_message(reply_target, line)
            async with database_lock:
                await log_message(
                    db, IRCMessage(network=bottle.irc.network, channel=channel,
                                   speaker=bottle.irc.nick, body=line, bot_id=bottle.id),
                )
        logger.info("sent %d reply line(s) to %s", len(lines), reply_target)
        if bottle.extract_memories:
            try:
                candidates = await extract_candidates(bottle.llm, speaker=speaker, body=body)
                async with database_lock:
                    inserted = await store_memory_candidates(
                        db, user_id=user_id, source_message_id=latest.message_id,
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

    async def on_message(message: IncomingIRCMessage) -> None:
        async with database_lock:
            user_id = await resolve_user(db, network=bottle.irc.network, identity=message)
            direct_message = irc_casefold(message.target) == irc_casefold(bottle.irc.nick)
            conversation = f"@{user_id}" if direct_message else message.target
            incoming = IRCMessage(
                network=bottle.irc.network, channel=conversation, speaker=message.nick,
                body=message.body, bot_id=bottle.id, user_id=user_id,
            )
            message_id = await log_message(db, incoming)
            module_context = ModuleContext(
                db=db, bottle=bottle, message=message, user_id=user_id,
                source_message_id=message_id,
            )
            await modules.on_message(module_context)
        key = (irc_casefold(conversation), user_id)
        addressed = direct_message or mentions_nick(message.body, bottle.irc.nick)
        if windows.contains(key) or addressed:
            windows.add(
                key, WindowMessage(
                    message=message, user_id=user_id, message_id=message_id,
                    conversation=conversation,
                )
            )

    client = IRCClient(bottle.irc, on_message)
    try:
        await client.run()
    finally:
        await windows.close()


async def run_bottle(db: aiosqlite.Connection, bottle: Bottle) -> None:
    delay = 1.0
    while True:
        started_at = time.monotonic()
        try:
            await run_bottle_once(db, bottle)
        except asyncio.CancelledError:
            logger.info("stopping Bottle %d (%s)", bottle.id, bottle.name)
            raise
        except Exception:
            if time.monotonic() - started_at >= 30.0:
                delay = 1.0
            logger.exception("Bottle %d (%s) disconnected; retrying in %.0fs",
                             bottle.id, bottle.name, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)


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
