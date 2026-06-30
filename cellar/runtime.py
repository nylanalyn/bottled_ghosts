import aiosqlite

from cellar.irc import IRCClient
from cellar.llm import complete
from cellar.models import Bottle, IRCMessage
from cellar.prompt import build_prompt, read_soul
from cellar.safety import Cooldown, sanitize
from cellar.storage import log_message, recent_messages


async def run_bottle(db: aiosqlite.Connection, bottle: Bottle) -> None:
    soul = read_soul(bottle.soul_prompt_path)
    cooldown = Cooldown(bottle.cooldown_seconds)
    client: IRCClient

    async def on_message(speaker: str, channel: str, body: str) -> None:
        incoming = IRCMessage(network=bottle.irc.network, channel=channel, speaker=speaker,
                              body=body, bot_id=bottle.id)
        await log_message(db, incoming)
        if bottle.irc.nick.lower() not in body.lower():
            return
        history = await recent_messages(db, bot_id=bottle.id, network=bottle.irc.network,
                                        channel=channel)
        prompt = build_prompt(soul=soul, history=history[:-1], speaker=speaker, body=body)
        response = await complete(bottle.llm, prompt)
        for line in sanitize(response, max_lines=bottle.max_lines, max_chars=bottle.max_chars):
            await cooldown.wait()
            await client.send_message(channel, line)
            await log_message(db, IRCMessage(network=bottle.irc.network, channel=channel,
                              speaker=bottle.irc.nick, body=line, bot_id=bottle.id))

    client = IRCClient(bottle.irc, on_message)
    await client.run()
