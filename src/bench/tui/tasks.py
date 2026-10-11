"""The Tasks screen: one area at a time, three views (open, done, archived), and the task detail.

Keys are listed in the footer and with ?. Every change goes straight to the area's database through the
dashboard's open connection. Notices name a task by its id only, never by its title or notes.
"""
import sqlite3

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Input, Static

from bench import sensitive
from bench.db import core, tasks
from bench.tui.forms import Choose, Confirm, TextPrompt

URGENCY_STYLE = {3: "bold red", 2: "yellow", 1: "dim"}
WHEN_HEADING = {"open": "Updated", "done": "Done", "archived": "Archived"}


class AreaScreen(Screen):
    """A full screen that shows one area at a time. [ and ] step through the areas; escape goes back."""

    BINDINGS = [
        Binding("escape", "back", "Board"),
        Binding("left_square_bracket", "area(-1)", "Area", key_display="[ ]"),
        Binding("right_square_bracket", "area(1)", "Next area", show=False),
    ]

    @property
    def conn(self) -> sqlite3.Connection:
        return self.app.connections[self.app.area]

    @property
    def label(self) -> str:
        return self.app.cfg.areas.get(self.app.area, self.app.area)

    def reload(self) -> None:
        raise NotImplementedError

    def table(self) -> DataTable:
        return self.query_one(DataTable)

    def selected_id(self) -> int | None:
        table = self.table()
        if table.row_count == 0:
            return None
        return int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def put_cursor_on(self, row_id: int | None) -> None:
        table = self.table()
        if row_id is not None and str(row_id) in table.rows:
            table.move_cursor(row=table.get_row_index(str(row_id)))

    def action_area(self, step: int) -> None:
        index = core.AREAS.index(self.app.area)
        self.app.area = core.AREAS[(index + step) % len(core.AREAS)]
        self.reload()

    def action_back(self) -> None:
        if isinstance(self.focused, Input):
            self.table().focus()
        else:
            self.dismiss()

    def attempt(self, change, *args):
        """Make one change and redraw. Returns its result (True if it has none), or None after reporting a
        problem by area and id only."""
        try:
            result = change(*args)
        except (tasks.TaskError, sqlite3.Error) as exc:
            self.notify(f"{self.app.area}: {exc}", severity="error")
            return None
        self.reload()
        return True if result is None else result

    def checked(self, text: str, save) -> None:
        """Call save(), asking first when this area is checked and the text matches the sensitive patterns."""
        settings = self.app.cfg.tasks
        found = sensitive.findings(text, settings.hostname_patterns) if self.app.area in settings.checked_areas else []
        if not found:
            save()
            return
        message = "\n".join([
            f"This text in {self.label} looks like it holds something that stays off this Mac:",
            *[f"  {item}" for item in found],
            "",
            f"Saving stores the whole text, matches included, in the {self.label} database on this Mac.",
            "Save anyway?",
        ])

        def answered(yes: bool) -> None:
            if yes:
                save()
            else:
                self.notify("Not saved.")

        self.app.push_screen(Confirm(message), answered)


class TasksScreen(AreaScreen):
    BINDINGS = [
        Binding("v", "view", "View"),
        Binding("slash", "filter", "Filter", key_display="/"),
        Binding("c", "create", "New"),
        Binding("n", "note", "Note"),
        Binding("1", "urgency(1)", "Urgency", key_display="1 2 3"),
        Binding("2", "urgency(2)", "Urgency 2", show=False),
        Binding("3", "urgency(3)", "Urgency 3", show=False),
        Binding("d", "done", "Done/reopen"),
        Binding("a", "archive", "Archive/restore"),
        Binding("p", "project", "Project"),
        Binding("b", "archive_old", "Archive old"),
        Binding("e", "export", "Export"),
    ]
    DEFAULT_CSS = """
    TasksScreen #heading { padding: 0 1; height: auto; }
    TasksScreen #filter { margin: 0 1; }
    TasksScreen DataTable { height: 1fr; margin: 0 1; }
    """

    def __init__(self):
        super().__init__()
        self.view = "open"

    def compose(self) -> ComposeResult:
        yield Static(id="heading")
        yield Input(placeholder="/ to filter by title, project, or note; enter or escape goes back to the list", id="filter")
        yield DataTable(id="list", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.table()
        for heading in ("#", "U", "Title", "Project", "When"):
            table.add_column(heading, key=heading)
        self.reload()
        table.focus()

    def reload(self, focus_id: int | None = None) -> None:
        keep = focus_id or self.selected_id()
        search = self.query_one("#filter", Input).value
        rows = tasks.tasks(self.conn, self.view, search)
        table = self.table()
        table.clear()
        table.columns["When"].label = Text(WHEN_HEADING[self.view])
        for task in rows:
            when = {"open": task.updated_at, "done": task.done_at, "archived": task.archived_at}[self.view]
            table.add_row(str(task.id), Text(str(task.urgency), style=URGENCY_STYLE[task.urgency]), Text(task.title),
                          Text(task.project or ""), core.to_central(when) if when else "", key=str(task.id))
        self.put_cursor_on(keep)
        views = "   ".join(f"[reverse] {v} [/reverse]" if v == self.view else v for v in tasks.VIEWS)
        heading = f"[b]Tasks[/b]   {escape(self.label)} ({self.app.area})   {views}   {len(rows)} task(s)"
        if search.strip():
            heading += f"   filtered by: {escape(search.strip())}"
        self.query_one("#heading", Static).update(heading)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self.reload()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            self.table().focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        task_id = int(event.row_key.value)
        self.app.push_screen(TaskDetail(tasks.get(self.conn, task_id), tasks.notes(self.conn, task_id), self.label))

    def need_selected(self) -> int | None:
        task_id = self.selected_id()
        if task_id is None:
            self.notify("No task selected.")
        return task_id

    def action_view(self) -> None:
        self.view = tasks.VIEWS[(tasks.VIEWS.index(self.view) + 1) % len(tasks.VIEWS)]
        self.reload()

    def action_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_create(self) -> None:
        def typed(title: str | None) -> None:
            if not title:
                return

            def save() -> None:
                task_id = self.attempt(tasks.add, self.conn, title)
                if task_id:
                    self.reload(focus_id=task_id)
                    self.notify(f"task {task_id} added at urgency 2")

            self.checked(title, save)

        self.app.push_screen(TextPrompt(f"New task in {self.label}", "what needs doing (urgency starts at 2)"), typed)

    def action_note(self) -> None:
        task_id = self.need_selected()
        if task_id is None:
            return

        def typed(body: str | None) -> None:
            if not body:
                return

            def save() -> None:
                if self.attempt(tasks.add_note, self.conn, task_id, body):
                    self.notify(f"note added to task {task_id}")

            self.checked(body, save)

        self.app.push_screen(TextPrompt(f"Note for task {task_id}", "stamped with the time; notes are never edited", 2000), typed)

    def action_urgency(self, urgency: int) -> None:
        if self.view != "open":
            self.notify("Urgency changes in the open view (v).")
            return
        task_id = self.need_selected()
        if task_id is not None:
            self.attempt(tasks.set_urgency, self.conn, task_id, urgency)

    def action_done(self) -> None:
        task_id = self.need_selected()
        if task_id is None:
            return
        if self.view == "open":
            if self.attempt(tasks.mark_done, self.conn, task_id):
                self.notify(f"task {task_id} done; it is in the done view (v)")
        elif self.view == "done":
            if self.attempt(tasks.reopen, self.conn, task_id):
                self.notify(f"task {task_id} reopened")
        else:
            self.notify("Archived tasks come back with a (restore).")

    def action_archive(self) -> None:
        task_id = self.need_selected()
        if task_id is None:
            return
        if self.view == "done":
            if self.attempt(tasks.archive, self.conn, task_id):
                self.notify(f"task {task_id} archived")
        elif self.view == "archived":
            if self.attempt(tasks.restore, self.conn, task_id):
                self.notify(f"task {task_id} restored to open")
        else:
            self.notify("Mark it done first (d); done tasks can be archived.")

    def action_project(self) -> None:
        task_id = self.need_selected()
        if task_id is None:
            return
        choices: list[tuple[str, object]] = [("No project", None)]
        choices += [(p.name, p.id) for p in tasks.projects(self.conn, include_archived=False)]

        def picked(project_id) -> None:
            if project_id is not Choose.CANCELLED:
                self.attempt(tasks.set_project, self.conn, task_id, project_id)

        self.app.push_screen(Choose(f"Project for task {task_id}", choices), picked)

    def action_archive_old(self) -> None:
        days = self.app.cfg.tasks.archive_after_days
        old = tasks.archivable(self.conn, days)
        if not old:
            self.notify(f"No done tasks older than {days} days in {self.label}.")
            return

        def answered(yes: bool) -> None:
            if yes and self.attempt(tasks.archive_many, self.conn, old):
                self.notify(f"{len(old)} task(s) archived")

        self.app.push_screen(Confirm(f"Archive {len(old)} done task(s) in {self.label} finished more than {days} days ago?"), answered)

    def action_export(self) -> None:
        archived = tasks.tasks(self.conn, "archived")
        if not archived:
            self.notify(f"Nothing archived in {self.label} yet.")
            return

        def answered(yes: bool) -> None:
            if not yes:
                self.notify("Not exported.")
                return
            result = self.attempt(tasks.export_archived, self.conn, self.app.area, self.label)
            if result:
                path, count = result
                self.notify(f"{count} archived task(s) exported to {path}", timeout=12)

        self.app.push_screen(Confirm(
            f"Export {len(archived)} archived task(s) in {self.label} to ~/.bench/exports?\n\n"
            "The file holds the full title and every note of each task, readable only by you."), answered)


class TaskDetail(ModalScreen[None]):
    """One task and its note timeline, oldest note first. Read only; escape closes."""

    DEFAULT_CSS = """
    TaskDetail { align: center middle; }
    TaskDetail VerticalScroll { width: 100; height: auto; max-height: 90%; border: round $accent; background: $panel; padding: 1 2; }
    """
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, task: tasks.Task, notes: list[tasks.Note], label: str):
        super().__init__()
        self.task_row = task
        self.task_notes = notes
        self.label = label

    def compose(self) -> ComposeResult:
        t = self.task_row
        stamps = [f"created {core.to_central(t.created_at)}", f"updated {core.to_central(t.updated_at)}"]
        if t.done_at:
            stamps.append(f"done {core.to_central(t.done_at)}")
        if t.archived_at:
            stamps.append(f"archived {core.to_central(t.archived_at)}")
        lines = [
            f"[b]Task {t.id}[/b]   {escape(self.label)}   urgency {t.urgency}   {t.status}",
            "",
            escape(t.title),
            "",
            f"Project: {escape(t.project or 'none')}",
            ", ".join(stamps),
            "",
            "[b]Notes[/b]",
        ]
        if not self.task_notes:
            lines.append("[dim]none yet; n in the list adds one[/dim]")
        for note in self.task_notes:
            lines.append(f"{core.to_central(note.created_at)}  {escape(note.body)}")
        lines += ["", "[dim]escape closes[/dim]"]
        with VerticalScroll():
            yield Static("\n".join(lines), id="detail")

    def action_close(self) -> None:
        self.dismiss(None)
