import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from cellar.irc import (
    IRCAuthenticationError,
    IRCClient,
    IRCJoinEvent,
    IRCKickEvent,
    IRCKickedError,
    irc_casefold,
    mentions_any_nick,
)
from cellar.local_time import local_datetime_context
from cellar.admin_store import away_status, is_quiet, response_enabled
from cellar.identity import resolve_user_identity
from cellar.ignore_store import matching_ignore_action
from cellar.listening import ListeningWindowManager
from cellar.llm import complete
from cellar.memory import extract_candidates
from cellar.memory_store import approved_memory_texts, store_memory_candidates
from cellar.dream_store import recent_dream_texts
from cellar.models import Bottle, IRCMessage, IncomingIRCMessage
from cellar.module_api import (
    ModuleCommand, RoomBreakRequest,
    ModuleContext,
    ModuleRunner,
    RuntimeContext,
    RuntimeState,
)
from cellar.module_loader import load_modules
from cellar.prompt import build_prompt, read_soul
from cellar.safety import Cooldown, sanitize, typing_pause
from cellar.storage import log_message, open_database, recent_messages, search_messages

logger = logging.getLogger(__name__)
MOOD_BREAK_SECONDS = 30 * 60
MOOD_BREAK_FALLBACK = "I'm too annoyed to be good company. I need thirty minutes to breathe."
ABSENCE_NOTE_MIN_MINUTES = 10


def absence_gap_text(minutes: int) -> str:
    """Human-scale wording for an offline gap."""
    if minutes < 120:
        return f"{minutes} minutes"
    hours = round(minutes / 60)
    if hours < 48:
        return f"{hours} hours"
    return f"{round(hours / 24)} days"


async def _active_room_breaks(
    db: aiosqlite.Connection, *, bottle_id: int, network: str,
) -> list[aiosqlite.Row]:
    cursor = await db.execute(
        """SELECT channel, rejoin_at, baseline_valence, baseline_irritability
           FROM mood_room_breaks
           WHERE bot_id = ? AND network = ? AND active = 1
           ORDER BY rejoin_at""",
        (bottle_id, network),
    )
    return list(await cursor.fetchall())


async def _start_room_break(
    db: aiosqlite.Connection, *, bottle: Bottle, request: RoomBreakRequest,
) -> bool:
    """Persist a break before issuing PART; an active break is never extended."""
    if request.duration_seconds != MOOD_BREAK_SECONDS:
        raise ValueError("room breaks must last exactly 30 minutes")
    now = int(time.time())
    cursor = await db.execute(
        """INSERT INTO mood_room_breaks(
               bot_id, network, channel, started_at, rejoin_at,
               baseline_valence, baseline_irritability, active, reset_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL)
           ON CONFLICT(bot_id, network, channel) DO UPDATE SET
               started_at = excluded.started_at,
               rejoin_at = excluded.rejoin_at,
               baseline_valence = excluded.baseline_valence,
               baseline_irritability = excluded.baseline_irritability,
               active = 1,
               reset_at = NULL
           WHERE mood_room_breaks.active = 0""",
        (bottle.id, bottle.irc.network, request.channel, now,
         now + request.duration_seconds, request.baseline_valence,
         request.baseline_irritability),
    )
    await db.commit()
    return cursor.rowcount == 1


async def _finish_room_break(
    db: aiosqlite.Connection, *, bottle: Bottle, channel: str,
) -> bool:
    """Mark a due break complete and restore the recorded mood defaults."""
    now = int(time.time())
    row = await (await db.execute(
        """SELECT baseline_valence, baseline_irritability
           FROM mood_room_breaks
           WHERE bot_id = ? AND network = ? AND channel = ?
             AND active = 1 AND rejoin_at <= ?""",
        (bottle.id, bottle.irc.network, channel, now),
    )).fetchone()
    if row is None:
        return False
    previous = await (await db.execute(
        "SELECT valence, irritability FROM mood_state WHERE bot_id = ?", (bottle.id,)
    )).fetchone()
    valence, irritability = float(row[0]), float(row[1])
    previous_valence = float(previous[0]) if previous is not None else valence
    previous_irritability = float(previous[1]) if previous is not None else irritability
    await db.execute(
        """INSERT INTO mood_state(
               bot_id, valence, irritability, interaction_heat,
               last_interaction_at, updated_at, last_event,
               last_valence_delta, last_irritability_delta
           ) VALUES (?, ?, ?, 0.0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                     'initial', ?, ?)
           ON CONFLICT(bot_id) DO UPDATE SET
               valence = excluded.valence,
               irritability = excluded.irritability,
               interaction_heat = excluded.interaction_heat,
               last_interaction_at = excluded.last_interaction_at,
               updated_at = excluded.updated_at,
               last_event = excluded.last_event,
               last_valence_delta = excluded.last_valence_delta,
               last_irritability_delta = excluded.last_irritability_delta""",
        (bottle.id, valence, irritability, valence - previous_valence,
         irritability - previous_irritability),
    )
    await db.execute(
        """UPDATE mood_room_breaks SET active = 0, reset_at = ?
           WHERE bot_id = ? AND network = ? AND channel = ? AND active = 1""",
        (now, bottle.id, bottle.irc.network, channel),
    )
    await db.commit()
    return True


async def log_absence_notes(
    db: aiosqlite.Connection, *, bottle: Bottle, network: str, channels: list[str],
    nick: str, database_lock: asyncio.Lock,
) -> None:
    """Record each channel's offline gap so the Bottle knows it was away.

    Mirrors the kick and mood-break system events: the runtime writes an
    inspectable line into the channel history, and the character decides
    whether and how to mention coming back.
    """
    for channel in channels:
        row = await (await db.execute(
            """SELECT CAST((julianday('now') - julianday(MAX(timestamp))) * 1440
                      AS INTEGER)
               FROM messages WHERE bot_id = ? AND network = ? AND channel = ?""",
            (bottle.id, network, channel),
        )).fetchone()
        minutes = int(row[0]) if row is not None and row[0] is not None else 0
        if minutes < ABSENCE_NOTE_MIN_MINUTES:
            continue
        async with database_lock:
            await log_message(
                db, IRCMessage(
                    network=network, channel=channel,
                    speaker="IRC runtime",
                    body=(f"System event: {nick} rejoined after being "
                          f"offline for about {absence_gap_text(minutes)}."),
                    bot_id=bottle.id,
                ),
            )
        logger.info(
            "Bottle %d (%s) noted a %d minute absence in %s",
            bottle.id, bottle.name, minutes, channel,
        )


@dataclass(frozen=True)
class WindowMessage:
    message: IncomingIRCMessage
    user_id: str
    message_id: int
    conversation: str
    addressed: bool
    response_reason: str
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
    room_break_tasks: set[asyncio.Task[None]] = set()
    stepping_away_channels: set[str] = set()

    def active_nick() -> str:
        return getattr(client, "current_nick", bottle.irc.nick)

    async def respond(
        items: tuple[WindowMessage, ...], *, departure_request: RoomBreakRequest | None = None,
    ) -> None:
        latest = items[-1]
        message = latest.message
        user_id = latest.user_id
        message_ids = [item.message_id for item in items]
        body = "\n".join(item.message.body for item in items)
        speaker, channel = message.nick, latest.conversation
        if departure_request is None and irc_casefold(channel) in stepping_away_channels:
            logger.info("not responding in %s while Bottle is on a mood break", channel)
            return
        direct_message = irc_casefold(message.target) == irc_casefold(active_nick())
        reply_target = speaker if direct_message else message.target
        module_context = ModuleContext(
            db=db, bottle=bottle, message=message, user_id=user_id,
            source_message_id=latest.message_id,
            response_reason=(
                "addressed" if any(item.addressed for item in items)
                else "utility_event"
                if any(item.response_reason == "utility_event" for item in items)
                else "ambient"
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
            # Grouped windows can carry lines from several people. Memories are
            # fetched per person and labeled, so nobody else's facts bleed into
            # the wrong name.
            grouped_users = len({item.user_id for item in items}) > 1
            memories: list[str] = []
            seen_user_ids: set[str] = set()
            for item in items:
                if item.user_id in seen_user_ids or item.identity_confidence < 0.8:
                    continue
                seen_user_ids.add(item.user_id)
                texts = await approved_memory_texts(
                    db, bot_id=bottle.id, user_id=item.user_id, query_text=body,
                )
                if grouped_users:
                    memories.extend(
                        f"About {item.message.nick}: {text}" for text in texts
                    )
                else:
                    memories.extend(texts)
            dreams = await recent_dream_texts(db, bot_id=bottle.id)
            availability = await away_status(db, bottle_id=bottle.id)
            if availability is not None:
                module_context.prompt_sections.append(
                    "Operator-set availability status: "
                    f"{availability!r}. If asked where you are or whether you are available, "
                    "answer consistently with this status without claiming more certainty."
                )
            await modules.before_prompt(module_context)
            if departure_request is not None:
                module_context.prompt_sections.append(
                    "You have reached your limit with this room and are now leaving it. "
                    "Give exactly one short, in-character farewell that says you are annoyed "
                    "and need to step away for about thirty minutes. Do not name or insult anyone, "
                    "debate the decision, ask a question, or explain the mood system."
                )
        prompt = build_prompt(
            soul=soul, module_state=module_context.prompt_sections, memories=memories,
            dreams=dreams, relevant=relevant, history=history, speaker=speaker, body=body,
            bot_nicks=(active_nick(),),
            addressed=any(item.addressed for item in items),
            local_time=local_datetime_context(bottle.timezone),
            current_speakers=tuple(dict.fromkeys(item.message.nick for item in items)),
        )
        module_context.generation_prompt = prompt
        await modules.before_generation(module_context)
        response = await complete(bottle.llm, module_context.generation_prompt)
        module_context.response = response
        async with database_lock:
            await modules.after_response(module_context)
            replies_enabled = await response_enabled(db, bottle_id=bottle.id)
        lines = sanitize(
            module_context.response or "",
            max_lines=1 if departure_request is not None else bottle.max_lines,
            max_chars=bottle.max_chars, bot_nick=active_nick(),
        )
        if not replies_enabled:
            lines = []
        elif departure_request is not None and not lines:
            # A break should not silently look like a disconnect merely because
            # an LLM response was empty or malformed.
            lines = sanitize(
                MOOD_BREAK_FALLBACK, max_lines=1, max_chars=bottle.max_chars,
                bot_nick=active_nick(),
            )
        if not lines and module_context.response is not None:
            logger.warning("LLM response was empty after sanitization")
        for line in lines:
            # Read-then-type pacing before the flood-protection cooldown so
            # replies arrive at a human rhythm rather than instantly.
            await typing_pause(line)
            await cooldown.wait()
            action_body = (
                line[4:].strip()
                if line[:4].casefold() == "/me " and line[4:].strip()
                else None
            )
            if action_body is not None:
                await client.send_action(reply_target, action_body)
            else:
                await client.send_message(reply_target, line)
            async with database_lock:
                await log_message(
                    db, IRCMessage(network=bottle.irc.network, channel=channel,
                                   speaker=active_nick(), body=line, bot_id=bottle.id),
                )
        logger.info("sent %d reply line(s) to %s", len(lines), reply_target)
        if bottle.extract_memories and replies_enabled:
            try:
                # A grouped window carries lines from several people; extract
                # per person so facts are attributed to the right identity.
                by_user: dict[str, list[WindowMessage]] = {}
                for item in items:
                    by_user.setdefault(item.user_id, []).append(item)
                for group_user_id, group_items in by_user.items():
                    candidates = await extract_candidates(
                        bottle.llm,
                        speaker=group_items[-1].message.nick,
                        body="\n".join(item.message.body for item in group_items),
                        bot_names=(active_nick(), *bottle.address_names),
                    )
                    async with database_lock:
                        inserted = await store_memory_candidates(
                            db, bot_id=bottle.id, user_id=group_user_id,
                            source_message_ids=[
                                item.message_id for item in group_items
                            ],
                            candidates=candidates,
                        )
                    logger.info(
                        "stored %d pending memory candidate(s) for %s",
                        inserted, group_items[-1].message.nick,
                    )
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
        should_part = False
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
                response_reason=(
                    "addressed"
                    if direct_message
                    or mentions_any_nick(message.body, (active_nick(), *bottle.address_names))
                    else "ambient"
                ),
            )
            await modules.on_message(module_context)
            if module_context.drop_message:
                await db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
                await db.commit()
                logger.info("dropping content-filtered message from %s", message.nick)
                return
            commands = list(module_context.commands)
            replies_enabled = await response_enabled(db, bottle_id=bottle.id)
            quiet_mode = await is_quiet(db, bottle_id=bottle.id)
            request = module_context.room_break
            configured_channels = {irc_casefold(item) for item in bottle.irc.channels}
            if request is not None and irc_casefold(request.channel) in configured_channels:
                should_part = await _start_room_break(db, bottle=bottle, request=request)
                if should_part:
                    stepping_away_channels.add(irc_casefold(request.channel))
                    await log_message(
                        db, IRCMessage(
                            network=bottle.irc.network, channel=request.channel,
                            speaker="IRC runtime",
                            body=("System event: mood irritability reached +1.00; "
                                  "taking a 30-minute room break."),
                            bot_id=bottle.id,
                        ),
                    )
        if should_part:
            assert request is not None
            break_item = WindowMessage(
                message=message, user_id=user_id, message_id=message_id,
                conversation=conversation, addressed=True,
                response_reason=module_context.response_reason,
                identity_confidence=resolved.confidence,
            )
            try:
                await respond((break_item,), departure_request=request)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("failed to announce mood break in %s", request.channel)
            await client.part_channel(message.target)
            task = asyncio.create_task(
                return_from_break(request.channel, int(time.time()) + request.duration_seconds),
                name=f"mood-break-{bottle.id}-{request.channel}",
            )
            room_break_tasks.add(task)
            task.add_done_callback(room_break_tasks.discard)
            return
        if commands and replies_enabled:
            await send_module_commands(
                commands, target=message.target, channel=conversation,
            )
        if ignore_action == "no_response":
            return
        personal_key = (irc_casefold(conversation), user_id)
        # Addressed pings share one conversation-wide window so several people
        # pinging the Bottle inside the listening window produce one reply,
        # not one reply per ping. Ambient lines keep per-speaker windows.
        room_key: tuple[str, str] = ("addressed", irc_casefold(conversation))
        address_names = (active_nick(), *bottle.address_names)
        addressed = direct_message or mentions_any_nick(message.body, address_names)
        if quiet_mode:
            # Quiet bottles respond only to direct pings; suppress ambient
            # triggers and conversation-window continuations alike.
            should_respond = addressed
        elif module_context.request_response:
            should_respond = True
        elif module_context.suppress_automatic_response:
            should_respond = False
        else:
            should_respond = (
                windows.contains(personal_key)
                or windows.contains(room_key)
                or addressed
            )
        should_monitor = not replies_enabled and module_context.monitor_when_silent
        if (replies_enabled and should_respond) or should_monitor:
            # Ambient lines arriving while a reply window is open ride along
            # as context for that single upcoming reply.
            key = (
                room_key
                if addressed or windows.contains(room_key)
                else personal_key
            )
            windows.add(
                key, WindowMessage(
                    message=message, user_id=user_id, message_id=message_id,
                    conversation=conversation,
                    addressed=addressed and not module_context.suppress_automatic_response,
                    response_reason=module_context.response_reason,
                    identity_confidence=resolved.confidence,
                )
            )

    async def on_kick(event: IRCKickEvent) -> None:
        """Persist the event so the Bottle sees it in later channel context."""
        async with database_lock:
            await log_message(
                db,
                IRCMessage(
                    network=bottle.irc.network,
                    channel=event.channel,
                    speaker="IRC server",
                    body=(
                        f"System event: {active_nick()} was kicked by {event.kicker}. "
                        f"Reason: {event.reason}"
                    ),
                    bot_id=bottle.id,
                ),
            )

    async def on_join(event: IRCJoinEvent) -> None:
        """Re-part if a bouncer or server autojoins an active break channel."""
        async with database_lock:
            rows = await _active_room_breaks(
                db, bottle_id=bottle.id, network=bottle.irc.network,
            )
            if not any(irc_casefold(str(row["channel"])) == irc_casefold(event.channel)
                       for row in rows):
                return
            await log_message(
                db,
                IRCMessage(
                    network=bottle.irc.network, channel=event.channel,
                    speaker="IRC runtime",
                    body="System event: active mood break reasserted after an automatic JOIN.",
                    bot_id=bottle.id,
                ),
            )
        logger.warning("re-parting %s while Bottle %d has an active mood break",
                       event.channel, bottle.id)
        await client.part_channel(event.channel)

    client = IRCClient(bottle.irc, on_message)
    async with database_lock:
        active_breaks = await _active_room_breaks(
            db, bottle_id=bottle.id, network=bottle.irc.network,
        )
        # A restart may discover a break whose return time elapsed while the
        # process was down. Complete it before IRC registration so the normal
        # post-registration JOIN includes that channel.
        for row in active_breaks:
            if int(row["rejoin_at"]) <= int(time.time()):
                await _finish_room_break(db, bottle=bottle, channel=str(row["channel"]))
        active_breaks = await _active_room_breaks(
            db, bottle_id=bottle.id, network=bottle.irc.network,
        )
    stepping_away_channels.update(irc_casefold(str(row["channel"])) for row in active_breaks)
    client.join_channels = [
        channel for channel in bottle.irc.channels
        if irc_casefold(channel) not in stepping_away_channels
    ]
    await log_absence_notes(
        db, bottle=bottle, network=bottle.irc.network,
        channels=client.join_channels, nick=active_nick(),
        database_lock=database_lock,
    )

    async def return_from_break(channel: str, rejoin_at: int) -> None:
        await asyncio.sleep(max(0.0, rejoin_at - time.time()))
        async with database_lock:
            finished = await _finish_room_break(db, bottle=bottle, channel=channel)
        if finished:
            stepping_away_channels.discard(irc_casefold(channel))
            await client.join_channel(channel)
            logger.info("Bottle %d (%s) rejoined %s after a mood break",
                        bottle.id, bottle.name, channel)

    for row in active_breaks:
        task = asyncio.create_task(
            return_from_break(str(row["channel"]), int(row["rejoin_at"])),
            name=f"mood-break-{bottle.id}-{row['channel']}",
        )
        room_break_tasks.add(task)
        task.add_done_callback(room_break_tasks.discard)
    client.kick_handler = on_kick
    client.join_handler = on_join
    if runtime_state is not None:
        client.connection_state_handler = lambda connected: setattr(
            runtime_state, "irc_connected", connected
        )
    try:
        await client.run()
    finally:
        await windows.close()
        for task in tuple(room_break_tasks):
            task.cancel()
        if room_break_tasks:
            await asyncio.gather(*room_break_tasks, return_exceptions=True)


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
            except IRCKickedError as error:
                runtime_state.irc_connected = False
                delay = 1.0
                logger.warning(
                    "Bottle %d (%s) was kicked from %s; reconnecting in 60s",
                    bottle.id, bottle.name, error.event.channel,
                )
                await asyncio.sleep(60.0)
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
