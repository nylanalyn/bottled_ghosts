import argparse
import asyncio
import logging
from getpass import getpass
from pathlib import Path

from cellar.configure import ask, collect_configuration
from cellar.dream_store import list_dreams
from cellar.dreams import run_dream
from cellar.runtime import run_bottle, run_bottles
from cellar.memory_store import (
    approve_memory_candidate,
    edit_user_memory,
    list_memory_candidates,
    list_user_memories,
    reject_memory_candidate,
)
from cellar.module_loader import available_modules
from cellar.module_store import module_states, set_module_enabled
from cellar.storage import (
    create_bottle,
    list_bottles,
    load_bottle,
    load_enabled_bottles,
    open_database,
    search_logs,
    set_memory_extraction,
    set_sasl_credentials,
)

MEMORY_TYPES = ("preference", "project", "relationship", "identity", "temporary_state")


async def async_main(args: argparse.Namespace) -> None:
    db = await open_database(args.database)
    try:
        if args.command == "run":
            await run_bottle(db, await load_bottle(db, args.bottle_id))
        elif args.command == "run-all":
            await run_bottles(db, await load_enabled_bottles(db))
        elif args.command == "list":
            bottles = await list_bottles(db)
            if not bottles:
                print("No Bottles configured.")
            for bottle in bottles:
                state = "enabled" if bottle.enabled else "disabled"
                channels = ",".join(bottle.channels)
                memory = "memory:on" if bottle.extract_memories else "memory:off"
                print(f"{bottle.id}\t{state}\t{memory}\t{bottle.name}\t"
                      f"{bottle.nick}@{bottle.network}\t{channels}")
        elif args.command == "configure":
            name, soul, irc, llm, max_lines, max_chars, cooldown, extract_memories = (
                collect_configuration()
            )
            created_id = await create_bottle(
                db, name=name, soul_prompt_path=soul, irc=irc, llm=llm,
                max_lines=max_lines, max_chars=max_chars, cooldown_seconds=cooldown,
                extract_memories=extract_memories,
            )
            print(f"Created Bottle {created_id}: {name}")
        elif args.command == "set-sasl":
            username = ask("SASL username")
            password = getpass("SASL password: ")
            if not password:
                raise ValueError("SASL password is required")
            await set_sasl_credentials(
                db, bottle_id=args.bottle_id, username=username, password=password
            )
            print(f"Updated SASL credentials for Bottle {args.bottle_id}")
        elif args.command == "memory-extraction":
            enabled = args_enabled(args.state)
            await set_memory_extraction(db, bottle_id=args.bottle_id, enabled=enabled)
            print(f"Memory extraction {'enabled' if enabled else 'disabled'} "
                  f"for Bottle {args.bottle_id}")
        elif args.command == "sediment-list":
            for candidate in await list_memory_candidates(db, status=args.status):
                print(f"{candidate.id}\t{candidate.status}\t{candidate.memory_type}\t"
                      f"{candidate.confidence:.2f}\t{candidate.canonical_name}\t"
                      f"{candidate.user_id}\n  candidate: {candidate.candidate_text}\n"
                      f"  source {candidate.source_message_id}: {candidate.source_body}")
        elif args.command == "sediment-approve":
            memory_id = await approve_memory_candidate(
                db, candidate_id=args.candidate_id, actor=args.actor
            )
            print(f"Approved candidate {args.candidate_id} as memory {memory_id}")
        elif args.command == "sediment-reject":
            await reject_memory_candidate(
                db, candidate_id=args.candidate_id, actor=args.actor
            )
            print(f"Rejected candidate {args.candidate_id}")
        elif args.command == "memories":
            for memory in await list_user_memories(db, user_id=args.user_id):
                print(f"{memory.id}\t{memory.memory_type}\t{memory.confidence:.2f}\t"
                      f"{memory.memory_text}")
        elif args.command == "memory-edit":
            await edit_user_memory(
                db, memory_id=args.memory_id, text=args.text,
                memory_type=args.memory_type, confidence=args.confidence, actor=args.actor,
            )
            print(f"Updated memory {args.memory_id}")
        elif args.command == "logs-search":
            for result in await search_logs(
                db, text=args.query, bot_id=args.bottle_id, network=args.network,
                channel=args.channel, limit=args.limit,
            ):
                print(f"{result.id}\t{result.timestamp}\t{result.network}\t{result.channel}\t"
                      f"<{result.speaker}> {result.body}")
        elif args.command == "modules":
            states = await module_states(db, bottle_id=args.bottle_id)
            for name in available_modules():
                print(f"{name}\t{'enabled' if states.get(name, False) else 'disabled'}")
        elif args.command == "module-toggle":
            if args.module_name not in available_modules():
                raise ValueError(f"unknown module: {args.module_name}")
            enabled = args_enabled(args.state)
            await set_module_enabled(
                db, bottle_id=args.bottle_id, module_name=args.module_name, enabled=enabled,
            )
            print(f"{args.module_name} {'enabled' if enabled else 'disabled'} "
                  f"for Bottle {args.bottle_id}; reconnect to apply")
        elif args.command == "dream":
            summary = await run_dream(
                db, bottle=await load_bottle(db, args.bottle_id), hours=args.hours,
            )
            print(f"Stored dream {summary.id}" if summary else "No messages in dream period")
        elif args.command == "dream-all":
            for bottle in await load_enabled_bottles(db):
                try:
                    summary = await run_dream(db, bottle=bottle, hours=args.hours)
                    if summary:
                        print(f"Bottle {bottle.id}: stored dream {summary.id}")
                except Exception:
                    logging.getLogger(__name__).exception(
                        "dream failed for Bottle %d (%s); continuing", bottle.id, bottle.name
                    )
        elif args.command == "dreams":
            for summary in await list_dreams(
                db, bot_id=args.bottle_id, limit=args.limit,
            ):
                print(f"{summary.id}\t{summary.period_start}\t{summary.period_end}\n"
                      f"  {summary.summary}")
    finally:
        await db.close()


def args_enabled(value: str | None) -> bool:
    if value not in {"on", "off"}:
        raise ValueError("memory extraction state must be on or off")
    return value == "on"


def main() -> None:
    parser = argparse.ArgumentParser(prog="bottled-ghosts")
    parser.add_argument("--database", type=Path, default=Path("spirits.db"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply pending database migrations")
    commands.add_parser("configure", help="interactively create a Bottle")
    commands.add_parser("list", help="list configured Bottles")
    commands.add_parser("run-all", help="run all enabled Bottles")
    sasl_parser = commands.add_parser("set-sasl", help="set SASL credentials for a Bottle")
    sasl_parser.add_argument("bottle_id", type=int)
    memory_parser = commands.add_parser(
        "memory-extraction", help="enable or disable sediment extraction"
    )
    memory_parser.add_argument("bottle_id", type=int)
    memory_parser.add_argument("state", choices=("on", "off"))
    sediment_list = commands.add_parser("sediment-list", help="list memory candidates")
    sediment_list.add_argument("--status", choices=("pending", "approved", "rejected"),
                               default="pending")
    sediment_approve = commands.add_parser("sediment-approve", help="approve a candidate")
    sediment_approve.add_argument("candidate_id", type=int)
    sediment_approve.add_argument("--actor", default="operator")
    sediment_reject = commands.add_parser("sediment-reject", help="reject a candidate")
    sediment_reject.add_argument("candidate_id", type=int)
    sediment_reject.add_argument("--actor", default="operator")
    memories_parser = commands.add_parser("memories", help="list approved memories for a user")
    memories_parser.add_argument("user_id")
    memory_edit = commands.add_parser("memory-edit", help="edit an approved memory")
    memory_edit.add_argument("memory_id", type=int)
    memory_edit.add_argument("--text")
    memory_edit.add_argument("--type", dest="memory_type", choices=MEMORY_TYPES)
    memory_edit.add_argument("--confidence", type=float)
    memory_edit.add_argument("--actor", default="operator")
    logs_search = commands.add_parser("logs-search", help="search message logs with FTS5")
    logs_search.add_argument("query")
    logs_search.add_argument("--bottle", dest="bottle_id", type=int)
    logs_search.add_argument("--network")
    logs_search.add_argument("--channel")
    logs_search.add_argument("--limit", type=int, default=20)
    modules_parser = commands.add_parser("modules", help="list module state for a Bottle")
    modules_parser.add_argument("bottle_id", type=int)
    module_toggle = commands.add_parser("module-toggle", help="enable or disable a module")
    module_toggle.add_argument("bottle_id", type=int)
    module_toggle.add_argument("module_name")
    module_toggle.add_argument("state", choices=("on", "off"))
    dream_parser = commands.add_parser("dream", help="summarize one Bottle's recent activity")
    dream_parser.add_argument("bottle_id", type=int)
    dream_parser.add_argument("--hours", type=int, default=24)
    dream_all = commands.add_parser("dream-all", help="summarize every enabled Bottle")
    dream_all.add_argument("--hours", type=int, default=24)
    dreams_parser = commands.add_parser("dreams", help="list stored dreams for a Bottle")
    dreams_parser.add_argument("bottle_id", type=int)
    dreams_parser.add_argument("--limit", type=int, default=20)
    tui_parser = commands.add_parser("tui", help="open the operational dashboard")
    tui_parser.add_argument("--actor", default="operator", help="audit identity for TUI edits")
    run_parser = commands.add_parser("run", help="run one configured Bottle")
    run_parser.add_argument("bottle_id", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "tui":
        from tui.app import run_tui

        run_tui(args.database, actor=args.actor)
        return
    asyncio.run(async_main(args))
