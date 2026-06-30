import asyncio
import logging

import aiosqlite

from cellar.irc import IRCClient
from cellar.identity import resolve_user
from cellar.llm import complete
from cellar.models import Bottle, IRCMessage, IncomingIRCMessage
from cellar.prompt import build_prompt, read_soul
from cellar.safety import Cooldown, sanitize
from cellar.storage import log_message, recent_messages, search_messages

logger = logging.getLogger(__name__)


async def run_bottle_once(db: aiosqlite.Connection, bottle: Bottle) -> None:
    soul = read_soul(bottle.soul_prompt_path)
    cooldown = Cooldown(bottle.cooldown_seconds)
    client: IRCClient

    async def on_message(message: IncomingIRCMessage) -> None:
        user_id = await resolve_user(db, network=bottle.irc.network, identity=message)
        incoming = IRCMessage(network=bottle.irc.network, channel=message.target,
                              speaker=message.nick, body=message.body, bot_id=bottle.id,
                              user_id=user_id)
        message_id = await log_message(db, incoming)
        speaker, channel, body = message.nick, message.target, message.body
        direct_message = channel.casefold() == bottle.irc.nick.casefold()
        if not direct_message and bottle.irc.nick.casefold() not in body.casefold():
            return
        reply_target = speaker if direct_message else channel
        logger.info("generating reply to %s in %s", speaker, reply_target)
        history = await recent_messages(db, bot_id=bottle.id, network=bottle.irc.network,
                                        channel=channel)
        relevant = await search_messages(
            db, bot_id=bottle.id, network=bottle.irc.network, channel=channel,
            text=body, exclude_message_id=message_id,
        )
        prompt = build_prompt(soul=soul, relevant=relevant, history=history[:-1],
                              speaker=speaker, body=body)
        response = await complete(bottle.llm, prompt)
        lines = sanitize(response, max_lines=bottle.max_lines, max_chars=bottle.max_chars)
        if not lines:
            logger.warning("LLM response was empty after sanitization")
        for line in lines:
            await cooldown.wait()
            await client.send_message(reply_target, line)
            await log_message(db, IRCMessage(network=bottle.irc.network, channel=reply_target,
                              speaker=bottle.irc.nick, body=line, bot_id=bottle.id))
        logger.info("sent %d reply line(s) to %s", len(lines), reply_target)

    client = IRCClient(bottle.irc, on_message)
    await client.run()


async def run_bottle(db: aiosqlite.Connection, bottle: Bottle) -> None:
    delay = 1.0
    while True:
        try:
            await run_bottle_once(db, bottle)
        except asyncio.CancelledError:
            logger.info("stopping Bottle %d (%s)", bottle.id, bottle.name)
            raise
        except Exception:
            logger.exception("Bottle %d (%s) disconnected; retrying in %.0fs",
                             bottle.id, bottle.name, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)


async def run_bottles(db: aiosqlite.Connection, bottles: list[Bottle]) -> None:
    if not bottles:
        raise ValueError("no enabled Bottles are configured")
    logger.info("starting %d Bottle(s)", len(bottles))
    async with asyncio.TaskGroup() as tasks:
        for bottle in bottles:
            tasks.create_task(run_bottle(db, bottle), name=f"bottle-{bottle.id}")
