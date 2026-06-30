import logging

import aiosqlite

from cellar.irc import IRCClient
from cellar.llm import complete
from cellar.models import Bottle, IRCMessage
from cellar.prompt import build_prompt, read_soul
from cellar.safety import Cooldown, sanitize
from cellar.storage import log_message, recent_messages

logger = logging.getLogger(__name__)


async def run_bottle(db: aiosqlite.Connection, bottle: Bottle) -> None:
    soul = read_soul(bottle.soul_prompt_path)
    cooldown = Cooldown(bottle.cooldown_seconds)
    client: IRCClient

    async def on_message(speaker: str, channel: str, body: str) -> None:
        incoming = IRCMessage(network=bottle.irc.network, channel=channel, speaker=speaker,
                              body=body, bot_id=bottle.id)
        await log_message(db, incoming)
        direct_message = channel.casefold() == bottle.irc.nick.casefold()
        if not direct_message and bottle.irc.nick.casefold() not in body.casefold():
            return
        reply_target = speaker if direct_message else channel
        logger.info("generating reply to %s in %s", speaker, reply_target)
        history = await recent_messages(db, bot_id=bottle.id, network=bottle.irc.network,
                                        channel=channel)
        prompt = build_prompt(soul=soul, history=history[:-1], speaker=speaker, body=body)
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
