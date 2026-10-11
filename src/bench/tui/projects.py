"""The Projects screen: one area at a time, every project with its status and open task count.

Projects are never deleted. s opens a list of the four statuses (active, paused, done, archived) with the
current one highlighted. Archiving a project hides it from the board and the task project picker; its
tasks keep their project.
"""
from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Static

from bench.db import tasks
from bench.tui.forms import Choose, TextPrompt
from bench.tui.tasks import AreaScreen

STATUS_STYLE = {"active": "bold", "paused": "yellow", "done": "green", "archived": "dim"}


class ProjectsScreen(AreaScreen):
    BINDINGS = [
        Binding("c", "create", "New"),
        Binding("s", "status", "Status"),
    ]
    DEFAULT_CSS = """
    ProjectsScreen #heading { padding: 0 1; height: auto; }
    ProjectsScreen DataTable { height: 1fr; margin: 0 1; }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="heading")
        yield DataTable(id="list", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.table()
        for heading in ("Name", "Status", "Open tasks", "Description"):
            table.add_column(heading, key=heading)
        self.reload()
        table.focus()

    def reload(self, focus_id: int | None = None) -> None:
        keep = focus_id or self.selected_id()
        rows = tasks.projects(self.conn)
        table = self.table()
        table.clear()
        for p in rows:
            table.add_row(Text(p.name), Text(p.status, style=STATUS_STYLE[p.status]), str(p.open_tasks),
                          Text(p.description), key=str(p.id))
        self.put_cursor_on(keep)
        self.query_one("#heading", Static).update(
            f"[b]Projects[/b]   {escape(self.label)} ({self.app.area})   {len(rows)} project(s)   "
            "[dim]tasks join a project with p in the Tasks screen[/dim]")

    def action_create(self) -> None:
        def named(name: str | None) -> None:
            if not name:
                return

            def described(description: str | None) -> None:
                description = description or ""

                def save() -> None:
                    project_id = self.attempt(tasks.add_project, self.conn, name, description)
                    if project_id:
                        self.reload(focus_id=project_id)
                        self.notify(f"project {project_id} added")

                self.checked(f"{name}\n{description}", save)

            self.app.push_screen(TextPrompt("Description (optional; enter to skip)", "one line"), described)

        self.app.push_screen(TextPrompt(f"New project in {self.label}", "project name"), named)

    def action_status(self) -> None:
        project_id = self.selected_id()
        if project_id is None:
            self.notify("No project selected.")
            return
        current = next(p.status for p in tasks.projects(self.conn) if p.id == project_id)

        def picked(status) -> None:
            if status is not Choose.CANCELLED and status != current:
                self.attempt(tasks.set_project_status, self.conn, project_id, status)

        self.app.push_screen(Choose(f"Status for project {project_id}", [(s, s) for s in tasks.PROJECT_STATUSES],
                                    start=tasks.PROJECT_STATUSES.index(current)), picked)
