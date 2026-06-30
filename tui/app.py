from pathlib import Path

import aiosqlite
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from cellar.memory_store import (
    approve_memory_candidate,
    edit_user_memory,
    list_all_user_memories,
    list_memory_candidates,
    reject_memory_candidate,
)
from cellar.config_store import BottleSettings, load_bottle_settings, save_bottle_settings
from cellar.module_loader import available_modules
from cellar.module_store import module_states, set_module_enabled
from cellar.models import LogSearchResult
from cellar.models import IRCProfile, LLMProfile
from cellar.storage import (
    create_bottle,
    open_database,
    search_logs,
    set_bottle_enabled,
    set_memory_extraction,
)
from tui.data import dashboard_bottles, recent_bottle_messages


class BottledGhostsApp(App[None]):
    TITLE = "Bottled Ghosts"
    SUB_TITLE = "Local character engine"
    CSS = """
    TabbedContent { height: 1fr; }
    #bottles { height: 45%; border: solid $accent; }
    #log-title { height: 1; padding: 0 1; background: $panel; }
    #logs { height: 1fr; border: solid $secondary; }
    #sediment { height: 55%; border: solid $accent; }
    #candidate-detail { height: 1fr; border: solid $secondary; padding: 1 2; }
    #memories { height: 45%; border: solid $accent; }
    #memory-detail { height: 5; border: solid $secondary; padding: 1 2; }
    .memory-field { margin: 0 1; }
    #save-memory { margin: 1; width: 20; }
    #module-title { height: 2; padding: 0 1; background: $panel; }
    #module-list { height: 1fr; border: solid $accent; }
    #log-search-query { margin: 1; }
    #log-search-scope { margin: 0 2; }
    #run-log-search { margin: 1; width: 18; }
    #log-results { height: 45%; border: solid $accent; }
    #log-result-detail { height: 1fr; border: solid $secondary; padding: 1 2; }
    #configuration-scroll { height: 1fr; padding: 0 2; }
    #configuration-scroll Label { margin-top: 1; }
    #save-configuration { margin: 1 0 2 0; width: 26; }
    #new-configuration { margin: 1 1 2 0; width: 20; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "approve_candidate", "Approve"),
        Binding("x", "reject_candidate", "Reject"),
        Binding("ctrl+s", "save_memory", "Save memory"),
        Binding("f2", "toggle_bottle", "Toggle Bottle"),
        Binding("f3", "toggle_extraction", "Toggle extraction"),
        Binding("f4", "toggle_module", "Toggle module"),
        Binding("slash", "focus_log_search", "Search logs", key_display="/"),
        Binding("f5", "save_configuration", "Save configuration"),
        Binding("f6", "new_configuration", "New Bottle"),
    ]

    def __init__(self, database: Path, *, actor: str = "operator") -> None:
        super().__init__()
        self.database = database
        self.actor = actor
        self.db: aiosqlite.Connection | None = None
        self.selected_candidate_id: int | None = None
        self.selected_memory_id: int | None = None
        self.selected_bottle_id: int | None = None
        self.selected_module_name: str | None = None
        self.log_results: dict[int, LogSearchResult] = {}
        self.creating_bottle = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="bottles-tab"):
            with TabPane("Bottles", id="bottles-tab"):
                yield DataTable(id="bottles")
                yield Static("Recent messages", id="log-title")
                yield RichLog(id="logs", wrap=True, markup=False)
            with TabPane("Sediment", id="sediment-tab"):
                yield DataTable(id="sediment")
                yield Static("Select a candidate to inspect its source.", id="candidate-detail")
            with TabPane("Memories", id="memories-tab"):
                yield DataTable(id="memories")
                yield Static("Select an approved memory to inspect it.", id="memory-detail")
                yield Input(placeholder="Memory text", id="memory-text", classes="memory-field")
                yield Select(
                    [(name.replace("_", " ").title(), name) for name in (
                        "preference", "project", "relationship", "identity", "temporary_state"
                    )],
                    prompt="Memory type", allow_blank=False, id="memory-type",
                    classes="memory-field",
                )
                yield Input(placeholder="Confidence (0–1)", type="number",
                            id="memory-confidence", classes="memory-field")
                yield Button("Save audited edit", id="save-memory", variant="primary")
            with TabPane("Modules", id="modules-tab"):
                yield Static("Select a Bottle on the Bottles tab.", id="module-title")
                yield DataTable(id="module-list")
            with TabPane("Log Search", id="log-search-tab"):
                yield Input(placeholder="FTS search query", id="log-search-query")
                yield Checkbox("Scope to selected Bottle", value=True, id="log-search-scope")
                yield Button("Search logs", id="run-log-search", variant="primary")
                yield DataTable(id="log-results")
                yield Static("Enter a query to search indexed messages.", id="log-result-detail")
            with TabPane("Configuration", id="configuration-tab"):
                with VerticalScroll(id="configuration-scroll"):
                    yield Static("Select a Bottle on the Bottles tab. Secrets are not displayed.",
                                 id="configuration-title")
                    for label, field_id, input_type in (
                        ("Bottle name", "config-name", "text"),
                        ("Soul prompt path", "config-soul", "text"),
                        ("IRC network name", "config-network", "text"),
                        ("IRC server host", "config-host", "text"),
                        ("IRC server port", "config-port", "integer"),
                        ("IRC nickname", "config-nick", "text"),
                        ("IRC username", "config-username", "text"),
                        ("IRC real name", "config-realname", "text"),
                        ("IRC channels (comma-separated)", "config-channels", "text"),
                        ("LLM chat-completions endpoint", "config-endpoint", "text"),
                        ("LLM model", "config-model", "text"),
                        ("LLM temperature", "config-temperature", "number"),
                        ("LLM maximum tokens", "config-max-tokens", "integer"),
                        ("Maximum IRC reply lines", "config-max-lines", "integer"),
                        ("Maximum characters per line", "config-max-chars", "integer"),
                        ("Cooldown seconds", "config-cooldown", "number"),
                    ):
                        yield Label(label)
                        yield Input(id=field_id, type=input_type)
                    yield Checkbox("Use TLS", id="config-tls")
                    yield Button("New Bottle", id="new-configuration")
                    yield Button("Save audited configuration", id="save-configuration",
                                 variant="primary")
        yield Footer()

    async def on_mount(self) -> None:
        self.db = await open_database(self.database)
        bottle_table = self.query_one("#bottles", DataTable)
        bottle_table.cursor_type = "row"
        bottle_table.zebra_stripes = True
        bottle_table.add_columns(
            "ID", "State", "Bottle", "IRC identity", "Channels",
            "Memory", "Sediment", "Modules", "Last activity",
        )
        sediment_table = self.query_one("#sediment", DataTable)
        sediment_table.cursor_type = "row"
        sediment_table.zebra_stripes = True
        sediment_table.add_columns("ID", "User", "Type", "Confidence", "Candidate")
        memory_table = self.query_one("#memories", DataTable)
        memory_table.cursor_type = "row"
        memory_table.zebra_stripes = True
        memory_table.add_columns("ID", "User", "Type", "Confidence", "Memory")
        module_table = self.query_one("#module-list", DataTable)
        module_table.cursor_type = "row"
        module_table.zebra_stripes = True
        module_table.add_columns("Module", "State")
        log_table = self.query_one("#log-results", DataTable)
        log_table.cursor_type = "row"
        log_table.zebra_stripes = True
        log_table.add_columns("ID", "Timestamp", "Bottle", "Location", "Speaker", "Message")
        await self.refresh_all()
        bottle_table.focus()

    async def on_unmount(self) -> None:
        if self.db is not None:
            await self.db.close()
            self.db = None

    async def action_refresh(self) -> None:
        await self.refresh_all()

    async def refresh_all(self) -> None:
        await self.refresh_dashboard()
        await self.refresh_sediment()
        await self.refresh_memories()
        await self.refresh_modules()
        await self.refresh_configuration()

    async def refresh_dashboard(self) -> None:
        if self.db is None:
            return
        table = self.query_one("#bottles", DataTable)
        table.clear()
        bottles = await dashboard_bottles(self.db)
        for bottle in bottles:
            table.add_row(
                str(bottle.id), "enabled" if bottle.enabled else "disabled", bottle.name,
                f"{bottle.nick}@{bottle.network}", ",".join(bottle.channels),
                "on" if bottle.extract_memories else "off", str(bottle.pending_candidates),
                bottle.enabled_modules or "—", bottle.last_activity or "—",
                key=str(bottle.id),
            )
        bottle_ids = {bottle.id for bottle in bottles}
        if self.selected_bottle_id not in bottle_ids:
            self.selected_bottle_id = bottles[0].id if bottles else None
        if self.selected_bottle_id is not None:
            await self.show_logs(self.selected_bottle_id)
        else:
            self.query_one("#logs", RichLog).write("No Bottles configured.")

    async def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "bottles":
            row_id = int(str(event.row_key.value))
            self.selected_bottle_id = row_id
            self.creating_bottle = False
            await self.show_logs(row_id)
            await self.refresh_modules()
            await self.refresh_configuration()
        elif event.data_table.id == "sediment":
            row_id = int(str(event.row_key.value))
            self.selected_candidate_id = row_id
            await self.show_candidate(row_id)
        elif event.data_table.id == "memories":
            row_id = int(str(event.row_key.value))
            self.selected_memory_id = row_id
            await self.show_memory(row_id)
        elif event.data_table.id == "module-list":
            self.selected_module_name = str(event.row_key.value)
        elif event.data_table.id == "log-results":
            self.show_log_result(int(str(event.row_key.value)))

    async def show_logs(self, bottle_id: int) -> None:
        if self.db is None:
            return
        log = self.query_one("#logs", RichLog)
        log.clear()
        self.query_one("#log-title", Static).update(f"Recent messages — Bottle {bottle_id}")
        messages = await recent_bottle_messages(self.db, bottle_id=bottle_id)
        if not messages:
            log.write("No messages recorded.")
            return
        for message in messages:
            log.write(Text(
                f"{message.timestamp} {message.channel} <{message.speaker}> {message.body}"
            ))

    async def refresh_sediment(self) -> None:
        if self.db is None:
            return
        table = self.query_one("#sediment", DataTable)
        table.clear()
        candidates = await list_memory_candidates(self.db)
        for candidate in candidates:
            table.add_row(
                str(candidate.id), candidate.canonical_name, candidate.memory_type,
                f"{candidate.confidence:.2f}", candidate.candidate_text,
                key=str(candidate.id),
            )
        self.selected_candidate_id = candidates[0].id if candidates else None
        if candidates:
            await self.show_candidate(candidates[0].id)
        else:
            self.query_one("#candidate-detail", Static).update("No pending sediment.")

    async def show_candidate(self, candidate_id: int) -> None:
        if self.db is None:
            return
        candidates = await list_memory_candidates(self.db)
        candidate = next((item for item in candidates if item.id == candidate_id), None)
        if candidate is None:
            return
        self.query_one("#candidate-detail", Static).update(Text(
            f"Candidate {candidate.id} for {candidate.canonical_name} ({candidate.user_id})\n\n"
            f"Proposed {candidate.memory_type} [{candidate.confidence:.2f}]:\n"
            f"{candidate.candidate_text}\n\nSource message {candidate.source_message_id}:\n"
            f"{candidate.source_body}"
        ))

    async def action_approve_candidate(self) -> None:
        if self.db is None or self.selected_candidate_id is None:
            self.notify("No pending candidate selected", severity="warning")
            return
        candidate_id = self.selected_candidate_id
        memory_id = await approve_memory_candidate(
            self.db, candidate_id=candidate_id, actor=self.actor,
        )
        self.notify(f"Approved candidate {candidate_id} as memory {memory_id}")
        await self.refresh_all()

    async def action_reject_candidate(self) -> None:
        if self.db is None or self.selected_candidate_id is None:
            self.notify("No pending candidate selected", severity="warning")
            return
        candidate_id = self.selected_candidate_id
        await reject_memory_candidate(
            self.db, candidate_id=candidate_id, actor=self.actor,
        )
        self.notify(f"Rejected candidate {candidate_id}")
        await self.refresh_all()

    async def refresh_memories(self) -> None:
        if self.db is None:
            return
        table = self.query_one("#memories", DataTable)
        table.clear()
        memories = await list_all_user_memories(self.db)
        for memory in memories:
            table.add_row(
                str(memory.id), memory.canonical_name, memory.memory_type,
                f"{memory.confidence:.2f}", memory.memory_text, key=str(memory.id),
            )
        self.selected_memory_id = memories[0].id if memories else None
        if memories:
            await self.show_memory(memories[0].id)
        else:
            self.query_one("#memory-detail", Static).update("No approved memories.")
            self.query_one("#save-memory", Button).disabled = True

    async def show_memory(self, memory_id: int) -> None:
        if self.db is None:
            return
        memories = await list_all_user_memories(self.db)
        memory = next((item for item in memories if item.id == memory_id), None)
        if memory is None:
            return
        source = memory.source_body or "No source message available."
        self.query_one("#memory-detail", Static).update(Text(
            f"Memory {memory.id} for {memory.canonical_name} ({memory.user_id})\n"
            f"Source candidate: {memory.source_candidate_id or 'none'} — {source}"
        ))
        self.query_one("#memory-text", Input).value = memory.memory_text
        self.query_one("#memory-type", Select).value = memory.memory_type
        self.query_one("#memory-confidence", Input).value = str(memory.confidence)
        self.query_one("#save-memory", Button).disabled = False

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-memory":
            await self.action_save_memory()
        elif event.button.id == "run-log-search":
            await self.action_search_logs()
        elif event.button.id == "save-configuration":
            await self.action_save_configuration()
        elif event.button.id == "new-configuration":
            self.action_new_configuration()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "log-search-query":
            await self.action_search_logs()

    async def action_save_memory(self) -> None:
        if self.db is None or self.selected_memory_id is None:
            self.notify("No approved memory selected", severity="warning")
            return
        text = self.query_one("#memory-text", Input).value
        memory_type = str(self.query_one("#memory-type", Select).value)
        try:
            confidence = float(self.query_one("#memory-confidence", Input).value)
            await edit_user_memory(
                self.db, memory_id=self.selected_memory_id, text=text,
                memory_type=memory_type,  # type: ignore[arg-type]
                confidence=confidence, actor=self.actor,
            )
        except ValueError as error:
            self.notify(str(error), severity="error")
            return
        self.notify(f"Updated memory {self.selected_memory_id}")
        await self.refresh_memories()

    async def refresh_modules(self) -> None:
        table = self.query_one("#module-list", DataTable)
        table.clear()
        if self.db is None or self.selected_bottle_id is None:
            self.query_one("#module-title", Static).update("No Bottle selected.")
            self.selected_module_name = None
            return
        states = await module_states(self.db, bottle_id=self.selected_bottle_id)
        for name in available_modules():
            table.add_row(name, "enabled" if states.get(name, False) else "disabled", key=name)
        self.selected_module_name = available_modules()[0] if available_modules() else None
        self.query_one("#module-title", Static).update(
            f"Bottle {self.selected_bottle_id} configuration — F2 Bottle, "
            "F3 extraction, F4 selected module"
        )

    async def action_toggle_bottle(self) -> None:
        if self.db is None or self.selected_bottle_id is None:
            self.notify("No Bottle selected", severity="warning")
            return
        bottles = await dashboard_bottles(self.db)
        bottle = next(item for item in bottles if item.id == self.selected_bottle_id)
        await set_bottle_enabled(
            self.db, bottle_id=bottle.id, enabled=not bottle.enabled,
        )
        self.notify(f"Bottle {bottle.id} {'disabled' if bottle.enabled else 'enabled'}")
        await self.refresh_dashboard()

    async def action_toggle_extraction(self) -> None:
        if self.db is None or self.selected_bottle_id is None:
            self.notify("No Bottle selected", severity="warning")
            return
        bottles = await dashboard_bottles(self.db)
        bottle = next(item for item in bottles if item.id == self.selected_bottle_id)
        await set_memory_extraction(
            self.db, bottle_id=bottle.id, enabled=not bottle.extract_memories,
        )
        self.notify(
            f"Memory extraction {'disabled' if bottle.extract_memories else 'enabled'} "
            f"for Bottle {bottle.id}"
        )
        await self.refresh_dashboard()

    async def action_toggle_module(self) -> None:
        if self.db is None or self.selected_bottle_id is None or self.selected_module_name is None:
            self.notify("No module selected", severity="warning")
            return
        states = await module_states(self.db, bottle_id=self.selected_bottle_id)
        enabled = not states.get(self.selected_module_name, False)
        await set_module_enabled(
            self.db, bottle_id=self.selected_bottle_id,
            module_name=self.selected_module_name, enabled=enabled,
        )
        self.notify(f"{self.selected_module_name} {'enabled' if enabled else 'disabled'}; "
                    "reconnect to apply")
        await self.refresh_modules()
        await self.refresh_dashboard()

    def action_focus_log_search(self) -> None:
        self.query_one("#log-search-query", Input).focus()

    async def action_search_logs(self) -> None:
        if self.db is None:
            return
        query = self.query_one("#log-search-query", Input).value.strip()
        if not query:
            self.notify("Enter a log search query", severity="warning")
            return
        scoped = self.query_one("#log-search-scope", Checkbox).value
        bottle_id = self.selected_bottle_id if scoped else None
        results = await search_logs(self.db, text=query, bot_id=bottle_id)
        self.log_results = {result.id: result for result in results}
        table = self.query_one("#log-results", DataTable)
        table.clear()
        for result in results:
            snippet = result.body if len(result.body) <= 80 else f"{result.body[:77]}..."
            table.add_row(
                str(result.id), result.timestamp, str(result.bot_id),
                f"{result.network} {result.channel}", result.speaker, snippet,
                key=str(result.id),
            )
        if results:
            self.show_log_result(results[0].id)
        else:
            self.query_one("#log-result-detail", Static).update("No matching messages.")

    def show_log_result(self, message_id: int) -> None:
        result = self.log_results.get(message_id)
        if result is None:
            return
        self.query_one("#log-result-detail", Static).update(Text(
            f"Message {result.id} — Bottle {result.bot_id}\n"
            f"{result.timestamp} {result.network} {result.channel} <{result.speaker}>\n\n"
            f"{result.body}"
        ))

    async def refresh_configuration(self) -> None:
        if self.db is None or self.selected_bottle_id is None or self.creating_bottle:
            return
        settings = await load_bottle_settings(self.db, bottle_id=self.selected_bottle_id)
        values = {
            "config-name": settings.name,
            "config-soul": str(settings.soul_prompt_path),
            "config-network": settings.network,
            "config-host": settings.host,
            "config-port": str(settings.port),
            "config-nick": settings.nick,
            "config-username": settings.username,
            "config-realname": settings.realname,
            "config-channels": ",".join(settings.channels),
            "config-endpoint": settings.endpoint,
            "config-model": settings.model,
            "config-temperature": str(settings.temperature),
            "config-max-tokens": str(settings.max_tokens),
            "config-max-lines": str(settings.max_lines),
            "config-max-chars": str(settings.max_chars),
            "config-cooldown": str(settings.cooldown_seconds),
        }
        for field_id, value in values.items():
            self.query_one(f"#{field_id}", Input).value = value
        self.query_one("#config-tls", Checkbox).value = settings.tls
        self.query_one("#configuration-title", Static).update(
            f"Editing Bottle {settings.id}. Passwords and API keys remain unchanged."
        )

    def form_settings(self, bottle_id: int) -> BottleSettings:
        def value(field_id: str) -> str:
            return self.query_one(f"#{field_id}", Input).value.strip()

        return BottleSettings(
            id=bottle_id,
            name=value("config-name"), soul_prompt_path=value("config-soul"),
            network=value("config-network"), host=value("config-host"),
            port=int(value("config-port")),
            tls=self.query_one("#config-tls", Checkbox).value,
            nick=value("config-nick"), username=value("config-username"),
            realname=value("config-realname"),
            channels=[item.strip() for item in value("config-channels").split(",")
                      if item.strip()],
            endpoint=value("config-endpoint"), model=value("config-model"),
            temperature=float(value("config-temperature")),
            max_tokens=int(value("config-max-tokens")),
            max_lines=int(value("config-max-lines")),
            max_chars=int(value("config-max-chars")),
            cooldown_seconds=float(value("config-cooldown")),
        )

    async def action_save_configuration(self) -> None:
        if self.db is None or (self.selected_bottle_id is None and not self.creating_bottle):
            self.notify("No Bottle selected", severity="warning")
            return
        try:
            settings = self.form_settings(0 if self.creating_bottle else self.selected_bottle_id or 0)
            if not settings.soul_prompt_path.is_file():
                raise ValueError(f"soul prompt does not exist: {settings.soul_prompt_path}")
            if self.creating_bottle:
                created_id = await create_bottle(
                    self.db, name=settings.name, soul_prompt_path=settings.soul_prompt_path,
                    irc=IRCProfile(
                        network=settings.network, host=settings.host, port=settings.port,
                        tls=settings.tls, nick=settings.nick, username=settings.username,
                        realname=settings.realname, channels=settings.channels,
                    ),
                    llm=LLMProfile(
                        endpoint=settings.endpoint, model=settings.model,
                        temperature=settings.temperature, max_tokens=settings.max_tokens,
                    ),
                    max_lines=settings.max_lines, max_chars=settings.max_chars,
                    cooldown_seconds=settings.cooldown_seconds, actor=self.actor,
                )
                self.selected_bottle_id = created_id
                self.creating_bottle = False
                changed = True
            else:
                changed = await save_bottle_settings(
                    self.db, settings=settings, actor=self.actor,
                )
        except ValueError as error:
            self.notify(str(error), severity="error")
            return
        self.notify("Configuration saved; reconnect to apply" if changed
                    else "Configuration is unchanged")
        await self.refresh_dashboard()
        await self.refresh_configuration()

    def action_new_configuration(self) -> None:
        self.creating_bottle = True
        defaults = {
            "config-name": "", "config-soul": "", "config-network": "",
            "config-host": "", "config-port": "6697", "config-nick": "",
            "config-username": "", "config-realname": "", "config-channels": "",
            "config-endpoint": "", "config-model": "", "config-temperature": "0.7",
            "config-max-tokens": "160", "config-max-lines": "2",
            "config-max-chars": "400", "config-cooldown": "1.0",
        }
        for field_id, value in defaults.items():
            self.query_one(f"#{field_id}", Input).value = value
        self.query_one("#config-tls", Checkbox).value = True
        self.query_one("#configuration-title", Static).update(
            "Creating a Bottle without secrets. Add API keys or SASL credentials separately."
        )
        self.query_one("#config-name", Input).focus()


def run_tui(database: Path, *, actor: str = "operator") -> None:
    BottledGhostsApp(database, actor=actor).run()
