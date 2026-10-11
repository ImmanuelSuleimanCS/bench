"""The Bench dashboard: an instrument board for the wide left monitor.

Six panels are visible at once, in a grid: System, Cockpit, and Dev status on top; Tasks, Workout, and
Projects below. Panels for later stages show what will go there. Readings refresh every 30 seconds in a
background thread; a reading that fails keeps its last good value, dimmed. No clock. On a narrow window
the grid drops to two columns, then one. Each refresh also takes any daily database backup that is due,
so a dashboard left running for days still backs up once a day.

t opens the Tasks screen and p the Projects screen; escape comes back to the board. Tasks and projects are
read and written on the main thread through the area connections the dashboard holds open (SQLite answers
these in well under a millisecond). The dashboard never starts, stops, or types into the cockpit.
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
from bench.db import core, report, tasks
from bench.tui.projects import ProjectsScreen
from bench.tui.tasks import TasksScreen

# (id, title, what arrives later; None for a panel that is live now)
PANELS = (
    ("system", "System", None),
    ("cockpit", "Cockpit", None),
    ("dev", "Dev status", "Stage G: git branch and uncommitted changes, Docker containers, Postgres reachable"),
    ("tasks", "Tasks", None),
    ("workout", "Workout", "Stage F: today's workout and the cycle phase"),
    ("projects", "Projects", None),
)
REFRESH_SECONDS = 30
TOP_TASKS = 3     # per area; four areas of 3 fit a panel on a 58 row window
TOP_PROJECTS = 5  # per area
URGENCY_MARK = {3: "[b red]3[/]", 2: "[yellow]2[/]", 1: "[dim]1[/]"}


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


def tasks_text(cfg: Config, connections: dict[str, sqlite3.Connection]) -> str:
    """Each area's open tasks, most urgent first, the top few by title."""
    blocks = []
    for area in core.AREAS:
        head = f"[b]{escape(cfg.areas.get(area, area))}[/b]"
        try:
            open_tasks = tasks.tasks(connections[area], "open")
        except (KeyError, sqlite3.Error):
            blocks.append(f"{head}  [red]database not open[/red]")
            continue
        lines = [f"{head}  {len(open_tasks)} open"]
        lines += [f"  {URGENCY_MARK[t.urgency]}  {escape(t.title)}" for t in open_tasks[:TOP_TASKS]]
        if len(open_tasks) > TOP_TASKS:
            lines.append(f"  [dim]and {len(open_tasks) - TOP_TASKS} more[/dim]")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def projects_text(cfg: Config, connections: dict[str, sqlite3.Connection]) -> str:
    """Projects that are not archived, by area, with their open task counts."""
    blocks = []
    for area in core.AREAS:
        try:
            listed = tasks.projects(connections[area], include_archived=False)
        except (KeyError, sqlite3.Error):
            continue
        if listed:
            lines = [f"[b]{escape(cfg.areas.get(area, area))}[/b]"]
            lines += [f"  {escape(p.name)}  [dim]{p.status}, {p.open_tasks} open[/dim]" for p in listed[:TOP_PROJECTS]]
            if len(listed) > TOP_PROJECTS:
                lines.append(f"  [dim]and {len(listed) - TOP_PROJECTS} more[/dim]")
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "No projects yet."


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
        Binding("t", "tasks", "Tasks"),
        Binding("p", "projects", "Projects"),
        Binding("question_mark", "keys", "Keys"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, cfg: Config, notes: list[str] | None = None,
                 connections: dict[str, sqlite3.Connection] | None = None,
                 read_status: Callable[[Config], status.Snapshot] = status.gather,
                 read_databases: Callable[[Config], str] = databases_text,
                 read_cockpit: Callable[[Config], str] = cockpit_text,
                 take_backups: Callable[[], list[str]] | None = None):
        super().__init__()
        self.cfg = cfg
        self.notes = notes or []
        self.connections = connections or {}
        self.read_status = read_status
        self.read_databases = read_databases
        self.read_cockpit = read_cockpit
        self.take_backups = take_backups  # None (as in tests) takes no backups
        self.last_good = status.Snapshot()
        self.area = core.AREAS[0]  # the area the Tasks and Projects screens show; [ and ] change it

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

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """t and p work on the board only, and only when the area databases are open."""
        if action in ("tasks", "projects"):
            return len(self.screen_stack) == 1 and bool(self.connections)
        return True

    def action_tasks(self) -> None:
        self.push_screen(TasksScreen(), lambda _: self.show_lists())

    def action_projects(self) -> None:
        self.push_screen(ProjectsScreen(), lambda _: self.show_lists())

    def show_lists(self) -> None:
        self.query_one("#tasks", Static).update(tasks_text(self.cfg, self.connections))
        self.query_one("#projects", Static).update(projects_text(self.cfg, self.connections))

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
        made = self.take_backups() if self.take_backups else []
        self.call_from_thread(self.show, snapshot, databases, cockpit, made)

    def show(self, snapshot: status.Snapshot, databases: str, cockpit: str, made: list[str] | None = None) -> None:
        for note in made or []:
            self.notify(note, timeout=8)
        parts, self.last_good = status.merge(snapshot, self.last_good)
        (cockpit_count, cockpit_dim), *system_parts = parts
        system = [shown(text, dimmed) for text, dimmed in system_parts]
        self.query_one("#system", Static).update("\n".join([*system, "", "[b]databases[/b]", databases]))
        self.query_one("#cockpit", Static).update("\n".join([shown(cockpit_count, cockpit_dim), "", cockpit]))
        self.show_lists()
