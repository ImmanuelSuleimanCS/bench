"""The Bench dashboard: an instrument board for the wide left monitor.

Six panels are visible at once, in a grid: System, Cockpit, and Dev status on top; Tasks, Workout, and
Projects below. Panels for later stages show what will go there, so the layout can be judged now. Live
panels refresh every 30 seconds in a background thread; a reading that fails keeps its last good value,
dimmed. No clock. On a narrow window the grid drops to two columns, then one.

The dashboard reads; it does not start, stop, or type into anything. The Claude Code panes stay in tmux.
"""
import sqlite3
from collections.abc import Callable

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.widgets import Footer, HelpPanel, Static

from bench import status
from bench.cockpit import plan
from bench.cockpit.tmux import Tmux, TmuxError
from bench.config import Config
from bench.db import report

# (id, title, what arrives later; None for a panel that is live now)
PANELS = (
    ("system", "System", None),
    ("cockpit", "Cockpit", None),
    ("dev", "Dev status", "Stage G: git branch and uncommitted changes, Docker containers, Postgres reachable"),
    ("tasks", "Tasks", "Stage E: open tasks for each area, most urgent first"),
    ("workout", "Workout", "Stage F: today's workout and the cycle phase"),
    ("projects", "Projects", "Stage E: personal projects and their status"),
)
REFRESH_SECONDS = 30


def databases_text(cfg: Config) -> str:
    lines = []
    for text, ok in report.area_lines(cfg):
        lines.append(escape(text) if ok else f"[red]{escape(text)}[/red]")
    return "\n".join(lines)


def cockpit_text(cfg: Config) -> str:
    tmux = Tmux(cfg.cockpit.tmux, cfg.cockpit.socket)
    try:
        if not tmux.has_session(cfg.cockpit.session):
            return "Not running. In a plain Terminal window: bench cockpit build"
        return "\n".join(escape(line) for line in plan.status_lines(cfg.cockpit, tmux.panes(cfg.cockpit.session)))
    except (TmuxError, OSError) as exc:
        return f"[red]{escape(str(exc))}[/red]"


def columns_for(width: int) -> int:
    if width >= 150:
        return 3
    return 2 if width >= 90 else 1


def shown(text: str, dimmed: bool) -> str:
    return f"[dim]{escape(text)}[/dim]" if dimmed else escape(text)


class BenchApp(App):
    TITLE = "Bench"
    CSS = """
    #board { grid-size: 3; grid-gutter: 0 1; padding: 0 1; }
    .panel { border: round $primary; border-title-color: $accent; padding: 0 1; height: 100%; }
    .later { color: $text-muted; }
    """
    BINDINGS = [
        Binding("r", "refresh_now", "Refresh"),
        Binding("question_mark", "keys", "Keys"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, cfg: Config, notes: list[str] | None = None,
                 connections: dict[str, sqlite3.Connection] | None = None,
                 read_status: Callable[[Config], status.Snapshot] = status.gather,
                 read_databases: Callable[[Config], str] = databases_text,
                 read_cockpit: Callable[[Config], str] = cockpit_text):
        super().__init__()
        self.cfg = cfg
        self.notes = notes or []
        self.connections = connections or {}
        self.read_status = read_status
        self.read_databases = read_databases
        self.read_cockpit = read_cockpit
        self.last_good = status.Snapshot()

    def compose(self) -> ComposeResult:
        with Grid(id="board"):
            for panel_id, title, later in PANELS:
                body = Static("reading..." if later is None else later, id=panel_id, classes="panel")
                if later is not None:
                    body.add_class("later")
                body.border_title = title
                yield body
        yield Footer()

    def on_mount(self) -> None:
        for note in self.notes:
            self.notify(note, timeout=8)
        self.fit_columns(self.size.width)
        self.action_refresh_now()
        self.set_interval(REFRESH_SECONDS, self.action_refresh_now)

    def on_resize(self, event) -> None:
        self.fit_columns(event.size.width)

    def fit_columns(self, width: int) -> None:
        self.query_one("#board", Grid).styles.grid_size_columns = columns_for(width)

    def action_keys(self) -> None:
        if self.screen.query(HelpPanel):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()

    @work(thread=True, exclusive=True, group="refresh")
    def action_refresh_now(self) -> None:
        snapshot = self.read_status(self.cfg)
        databases = self.read_databases(self.cfg)
        cockpit = self.read_cockpit(self.cfg)
        self.call_from_thread(self.show, snapshot, databases, cockpit)

    def show(self, snapshot: status.Snapshot, databases: str, cockpit: str) -> None:
        parts, self.last_good = status.merge(snapshot, self.last_good)
        (cockpit_count, cockpit_dim), *system_parts = parts
        system = [shown(text, dimmed) for text, dimmed in system_parts]
        self.query_one("#system", Static).update("\n".join([*system, "", "[b]databases[/b]", databases]))
        self.query_one("#cockpit", Static).update("\n".join([shown(cockpit_count, cockpit_dim), "", cockpit]))
