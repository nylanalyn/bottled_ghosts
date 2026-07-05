import argparse
import asyncio
import json
import logging
from getpass import getpass
from pathlib import Path

from cellar.configure import ask, collect_configuration
from cellar.admin_store import set_admin_api_token
from cellar.alias_store import add_alias, delete_alias, list_aliases
from cellar.nick_store import set_alternate_nicks
from cellar.dream_store import list_dreams
from cellar.dreams import run_dream
from cellar.ignore_store import add_ignore_rule, delete_ignore_rule, list_ignore_rules
from cellar.runtime import run_bottle, run_bottles
from cellar.memory_store import (
    approve_memory_candidate,
    edit_user_memory,
    list_memory_candidates,
    list_user_memories,
    reject_memory_candidate,
)
from cellar.module_loader import available_modules
from cellar.module_store import (
    module_settings,
    module_states,
    set_module_enabled,
    set_module_settings,
)
from cellar.storage import (
    create_bottle,
    list_bottles,
    load_bottle,
    load_enabled_bottles,
    open_database,
    search_logs,
    prune_messages,
    set_bottle_enabled,
    set_llm_api_key,
    set_memory_extraction,
    set_sasl_credentials,
    set_server_password,
)

MEMORY_TYPES = ("preference", "project", "relationship", "identity", "temporary_state")


async def async_main(args: argparse.Namespace) -> None:
    db = await open_database(args.database)
    try:
        if args.command == "run":
            await run_bottle(db, await load_bottle(db, args.bottle_id))
        elif args.command == "run-all":
            await run_bottles(args.database, await load_enabled_bottles(db))
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
        elif args.command == "aliases":
            aliases = await list_aliases(db, bottle_id=args.bottle_id)
            print("\n".join(aliases) if aliases else "No aliases configured.")
        elif args.command == "alias-add":
            changed = await add_alias(
                db, bottle_id=args.bottle_id, alias=args.alias, actor=args.actor,
            )
            print("Alias added; reconnect to apply" if changed else "Alias already exists")
        elif args.command == "alias-delete":
            changed = await delete_alias(
                db, bottle_id=args.bottle_id, alias=args.alias, actor=args.actor,
            )
            print("Alias deleted; reconnect to apply" if changed else "Alias not found")
        elif args.command == "alternate-nicks":
            changed = await set_alternate_nicks(
                db, bottle_id=args.bottle_id, nicks=args.nicks, actor=args.actor,
            )
            print("Alternate nicks updated; reconnect to apply" if changed
                  else "Alternate nicks are unchanged")
        elif args.command == "configure":
            (name, soul, irc, llm, max_lines, max_chars, cooldown, listen_window,
             extract_memories) = collect_configuration()
            created_id = await create_bottle(
                db, name=name, soul_prompt_path=soul, irc=irc, llm=llm,
                max_lines=max_lines, max_chars=max_chars, cooldown_seconds=cooldown,
                listen_window_seconds=listen_window, extract_memories=extract_memories,
                actor=args.actor,
            )
            print(f"Created Bottle {created_id}: {name}")
        elif args.command == "set-sasl":
            username = ask("SASL username")
            sasl_password = getpass("SASL password: ")
            if not sasl_password:
                raise ValueError("SASL password is required")
            await set_sasl_credentials(
                db, bottle_id=args.bottle_id, username=username, password=sasl_password,
                actor=args.actor,
            )
            print(f"Updated SASL credentials for Bottle {args.bottle_id}")
        elif args.command == "set-api-key":
            api_key = getpass("LLM API key (empty clears): ").strip() or None
            await set_llm_api_key(
                db, bottle_id=args.bottle_id, api_key=api_key, actor=args.actor,
            )
            print(f"Updated LLM API key for Bottle {args.bottle_id}")
        elif args.command == "set-server-password":
            server_password = getpass("IRC server password (empty clears): ").strip() or None
            await set_server_password(
                db, bottle_id=args.bottle_id, password=server_password, actor=args.actor,
            )
            print(f"Updated IRC server password for Bottle {args.bottle_id}")
        elif args.command == "memory-extraction":
            enabled = args_enabled(args.state)
            await set_memory_extraction(
                db, bottle_id=args.bottle_id, enabled=enabled, actor=args.actor,
            )
            print(f"Memory extraction {'enabled' if enabled else 'disabled'} "
                  f"for Bottle {args.bottle_id}")
        elif args.command == "bottle-toggle":
            enabled = args_enabled(args.state)
            await set_bottle_enabled(
                db, bottle_id=args.bottle_id, enabled=enabled, actor=args.actor,
            )
            print(f"Bottle {args.bottle_id} {'enabled' if enabled else 'disabled'}")
        elif args.command == "sediment-list":
            for candidate in await list_memory_candidates(db, status=args.status):
                sources = "\n".join(
                    f"  source {source.message_id}: {source.body}"
                    for source in candidate.source_messages
                )
                print(f"{candidate.id}\t{candidate.status}\t{candidate.memory_type}\t"
                      f"{candidate.confidence:.2f}\t{candidate.canonical_name}\t"
                      f"{candidate.user_id}\n  candidate: {candidate.candidate_text}\n"
                      f"{sources}")
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
            for user_memory in await list_user_memories(db, user_id=args.user_id):
                expiry = f"\texpires:{user_memory.expires_at}" if user_memory.expires_at else ""
                print(f"{user_memory.id}\t{user_memory.memory_type}\t"
                      f"{user_memory.confidence:.2f}{expiry}\t{user_memory.memory_text}")
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
        elif args.command == "logs-prune":
            deleted = await prune_messages(
                db, older_than_days=args.days, actor=args.actor,
            )
            print(f"Deleted {deleted} unreferenced message(s)")
        elif args.command == "modules":
            states = await module_states(db, bottle_id=args.bottle_id)
            settings = await module_settings(db, bottle_id=args.bottle_id)
            for name in available_modules():
                encoded = json.dumps(settings.get(name, {}), sort_keys=True)
                print(f"{name}\t{'enabled' if states.get(name, False) else 'disabled'}"
                      f"\t{encoded}")
        elif args.command == "module-toggle":
            if args.module_name not in available_modules():
                raise ValueError(f"unknown module: {args.module_name}")
            enabled = args_enabled(args.state)
            await set_module_enabled(
                db, bottle_id=args.bottle_id, module_name=args.module_name, enabled=enabled,
                actor=args.actor,
            )
            print(f"{args.module_name} {'enabled' if enabled else 'disabled'} "
                  f"for Bottle {args.bottle_id}; reconnect to apply")
        elif args.command == "module-settings":
            if args.module_name not in available_modules():
                raise ValueError(f"unknown module: {args.module_name}")
            parsed = json.loads(args.settings_json)
            if not isinstance(parsed, dict):
                raise ValueError("module settings must be a JSON object")
            await set_module_settings(
                db, bottle_id=args.bottle_id, module_name=args.module_name,
                settings=parsed, actor=args.actor,
            )
            print(f"Updated {args.module_name} settings for Bottle {args.bottle_id}; "
                  "reconnect to apply")
        elif args.command == "set-admin-token":
            token = getpass("Admin API token: ").strip()
            if not token:
                raise ValueError("admin API token is required")
            await set_admin_api_token(
                db, bottle_id=args.bottle_id, token=token, actor=args.actor,
            )
            print(f"Updated admin API token for Bottle {args.bottle_id}; reconnect to apply")
        elif args.command == "ignore-list":
            for rule in await list_ignore_rules(db, bottle_id=args.bottle_id):
                print(f"{rule.id}\t{rule.network}\t{rule.match_type}\t"
                      f"{rule.match_value}\t{rule.action}")
        elif args.command == "ignore-add":
            rule_id, created = await add_ignore_rule(
                db, bottle_id=args.bottle_id, network=args.network,
                match_type=args.match_type, match_value=args.match_value,
                action=args.action, actor=args.actor,
            )
            print(f"{'Added' if created else 'Existing'} ignore rule {rule_id}")
        elif args.command == "ignore-delete":
            await delete_ignore_rule(
                db, bottle_id=args.bottle_id, rule_id=args.rule_id, actor=args.actor,
            )
            print(f"Deleted ignore rule {args.rule_id}")
        elif args.command == "dream":
            summary = await run_dream(
                db, bottle=await load_bottle(db, args.bottle_id), hours=args.hours,
            )
            print(f"Stored dream {summary.id}" if summary else "No messages in dream period")
        elif args.command == "dream-all":
            for enabled_bottle in await load_enabled_bottles(db):
                try:
                    summary = await run_dream(db, bottle=enabled_bottle, hours=args.hours)
                    if summary:
                        print(f"Bottle {enabled_bottle.id}: stored dream {summary.id}")
                except Exception:
                    logging.getLogger(__name__).exception(
                        "dream failed for Bottle %d (%s); continuing",
                        enabled_bottle.id, enabled_bottle.name,
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
    configure_parser = commands.add_parser("configure", help="interactively create a Bottle")
    configure_parser.add_argument("--actor", default="operator")
    commands.add_parser("list", help="list configured Bottles")
    aliases_parser = commands.add_parser("aliases", help="list a Bottle's address aliases")
    aliases_parser.add_argument("bottle_id", type=int)
    alias_add = commands.add_parser("alias-add", help="add an address alias")
    alias_add.add_argument("bottle_id", type=int)
    alias_add.add_argument("alias")
    alias_add.add_argument("--actor", default="operator")
    alias_delete = commands.add_parser("alias-delete", help="delete an address alias")
    alias_delete.add_argument("bottle_id", type=int)
    alias_delete.add_argument("alias")
    alias_delete.add_argument("--actor", default="operator")
    alternate_nicks = commands.add_parser(
        "alternate-nicks", help="replace the ordered fallback IRC nick list"
    )
    alternate_nicks.add_argument("bottle_id", type=int)
    alternate_nicks.add_argument("nicks", nargs="*")
    alternate_nicks.add_argument("--actor", default="operator")
    commands.add_parser("run-all", help="run all enabled Bottles")
    sasl_parser = commands.add_parser("set-sasl", help="set SASL credentials for a Bottle")
    sasl_parser.add_argument("bottle_id", type=int)
    sasl_parser.add_argument("--actor", default="operator")
    api_key_parser = commands.add_parser("set-api-key", help="set or clear an LLM API key")
    api_key_parser.add_argument("bottle_id", type=int)
    api_key_parser.add_argument("--actor", default="operator")
    server_password = commands.add_parser(
        "set-server-password", help="set or clear an IRC server password"
    )
    server_password.add_argument("bottle_id", type=int)
    server_password.add_argument("--actor", default="operator")
    memory_parser = commands.add_parser(
        "memory-extraction", help="enable or disable sediment extraction"
    )
    memory_parser.add_argument("bottle_id", type=int)
    memory_parser.add_argument("state", choices=("on", "off"))
    memory_parser.add_argument("--actor", default="operator")
    bottle_toggle = commands.add_parser(
        "bottle-toggle", help="include or exclude a Bottle from run-all"
    )
    bottle_toggle.add_argument("bottle_id", type=int)
    bottle_toggle.add_argument("state", choices=("on", "off"))
    bottle_toggle.add_argument("--actor", default="operator")
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
    logs_prune = commands.add_parser(
        "logs-prune", help="delete old messages not retained as memory provenance"
    )
    logs_prune.add_argument("days", type=int)
    logs_prune.add_argument("--actor", default="operator")
    modules_parser = commands.add_parser("modules", help="list module state for a Bottle")
    modules_parser.add_argument("bottle_id", type=int)
    module_toggle = commands.add_parser("module-toggle", help="enable or disable a module")
    module_toggle.add_argument("bottle_id", type=int)
    module_toggle.add_argument("module_name")
    module_toggle.add_argument("state", choices=("on", "off"))
    module_toggle.add_argument("--actor", default="operator")
    module_settings_parser = commands.add_parser(
        "module-settings", help="replace a module's JSON settings"
    )
    module_settings_parser.add_argument("bottle_id", type=int)
    module_settings_parser.add_argument("module_name")
    module_settings_parser.add_argument("settings_json")
    module_settings_parser.add_argument("--actor", default="operator")
    admin_token_parser = commands.add_parser(
        "set-admin-token", help="set the admin API bearer token through a hidden prompt"
    )
    admin_token_parser.add_argument("bottle_id", type=int)
    admin_token_parser.add_argument("--actor", default="operator")
    ignore_list = commands.add_parser("ignore-list", help="list a Bottle's ignore rules")
    ignore_list.add_argument("bottle_id", type=int)
    ignore_add = commands.add_parser("ignore-add", help="add an audited IRC ignore rule")
    ignore_add.add_argument("bottle_id", type=int)
    ignore_add.add_argument("network")
    ignore_add.add_argument("match_type", choices=("account", "hostmask", "nick"))
    ignore_add.add_argument("match_value")
    ignore_add.add_argument("action", choices=("drop", "no_response"))
    ignore_add.add_argument("--actor", default="operator")
    ignore_delete = commands.add_parser(
        "ignore-delete", help="delete an audited IRC ignore rule"
    )
    ignore_delete.add_argument("bottle_id", type=int)
    ignore_delete.add_argument("rule_id", type=int)
    ignore_delete.add_argument("--actor", default="operator")
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
