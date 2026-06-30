from pathlib import Path

import aiosqlite
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from cellar.storage import open_database
from tui.data import dashboard_bottles, recent_bottle_messages


class BottledGhostsApp(App[None]):
    TITLE = "Bottled Ghosts"
    SUB_TITLE = "Local character engine"
    CSS = """
    #bottles { height: 45%; border: solid $accent; }
    #log-title { height: 1; padding: 0 1; background: $panel; }
    #logs { height: 1fr; border: solid $secondary; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, database: Path) -> None:
        super().__init__()
        self.database = database
        self.db: aiosqlite.Connection | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield DataTable(id="bottles")
        yield Static("Recent messages", id="log-title")
        yield RichLog(id="logs", wrap=True, markup=False)
        yield Footer()

    async def on_mount(self) -> None:
        self.db = await open_database(self.database)
        table = self.query_one("#bottles", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns(
            "ID", "State", "Bottle", "IRC identity", "Channels",
            "Memory", "Sediment", "Modules", "Last activity",
        )
        await self.refresh_dashboard()
        table.focus()

    async def on_unmount(self) -> None:
        if self.db is not None:
            await self.db.close()
            self.db = None

    async def action_refresh(self) -> None:
        await self.refresh_dashboard()

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
        await self.show_logs(int(str(event.row_key.value)))

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


def run_tui(database: Path) -> None:
    BottledGhostsApp(database).run()
