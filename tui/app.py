from pathlib import Path

import aiosqlite
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
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
from cellar.storage import open_database
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
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "approve_candidate", "Approve"),
        Binding("x", "reject_candidate", "Reject"),
        Binding("ctrl+s", "save_memory", "Save memory"),
    ]

    def __init__(self, database: Path, *, actor: str = "operator") -> None:
        super().__init__()
        self.database = database
        self.actor = actor
        self.db: aiosqlite.Connection | None = None
        self.selected_candidate_id: int | None = None
        self.selected_memory_id: int | None = None

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
        if bottles:
            await self.show_logs(bottles[0].id)
        else:
            self.query_one("#logs", RichLog).write("No Bottles configured.")

    async def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row_id = int(str(event.row_key.value))
        if event.data_table.id == "bottles":
            await self.show_logs(row_id)
        elif event.data_table.id == "sediment":
            self.selected_candidate_id = row_id
            await self.show_candidate(row_id)
        elif event.data_table.id == "memories":
            self.selected_memory_id = row_id
            await self.show_memory(row_id)

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


def run_tui(database: Path, *, actor: str = "operator") -> None:
    BottledGhostsApp(database, actor=actor).run()
