import argparse
import asyncio
import logging
from getpass import getpass
from pathlib import Path

from cellar.configure import ask, collect_configuration
from cellar.runtime import run_bottle, run_bottles
from cellar.storage import (
    create_bottle,
    list_bottles,
    load_bottle,
    load_enabled_bottles,
    open_database,
    set_memory_extraction,
    set_sasl_credentials,
)


async def async_main(
    database: Path, command: str, bottle_id: int | None = None, command_value: str | None = None
) -> None:
    db = await open_database(database)
    try:
        if command == "run":
            if bottle_id is None:
                raise SystemExit("a Bottle id is required")
            await run_bottle(db, await load_bottle(db, bottle_id))
        elif command == "run-all":
            await run_bottles(db, await load_enabled_bottles(db))
        elif command == "list":
            bottles = await list_bottles(db)
            if not bottles:
                print("No Bottles configured.")
            for bottle in bottles:
                state = "enabled" if bottle.enabled else "disabled"
                channels = ",".join(bottle.channels)
                memory = "memory:on" if bottle.extract_memories else "memory:off"
                print(f"{bottle.id}\t{state}\t{memory}\t{bottle.name}\t"
                      f"{bottle.nick}@{bottle.network}\t{channels}")
        elif command == "configure":
            name, soul, irc, llm, max_lines, max_chars, cooldown, extract_memories = (
                collect_configuration()
            )
            created_id = await create_bottle(
                db, name=name, soul_prompt_path=soul, irc=irc, llm=llm,
                max_lines=max_lines, max_chars=max_chars, cooldown_seconds=cooldown,
                extract_memories=extract_memories,
            )
            print(f"Created Bottle {created_id}: {name}")
        elif command == "set-sasl":
            if bottle_id is None:
                raise SystemExit("a Bottle id is required")
            username = ask("SASL username")
            password = getpass("SASL password: ")
            if not password:
                raise ValueError("SASL password is required")
            await set_sasl_credentials(db, bottle_id=bottle_id, username=username, password=password)
            print(f"Updated SASL credentials for Bottle {bottle_id}")
        elif command == "memory-extraction":
            if bottle_id is None:
                raise SystemExit("a Bottle id is required")
            enabled = args_enabled(command_value)
            await set_memory_extraction(db, bottle_id=bottle_id, enabled=enabled)
            print(f"Memory extraction {'enabled' if enabled else 'disabled'} for Bottle {bottle_id}")
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
    run_parser = commands.add_parser("run", help="run one configured Bottle")
    run_parser.add_argument("bottle_id", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(async_main(args.database, args.command, getattr(args, "bottle_id", None),
                           getattr(args, "state", None)))
