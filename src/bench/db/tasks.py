"""Tasks, notes, and projects in one area database.

Archive and restore are the whole lifecycle: there is no delete, and the triggers from migration
common/0002 make the database refuse one, even by hand. Every function takes an open connection. Error messages name a task
or project by its id only, never by its title or note text.
"""
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bench import paths, stamps
from bench.db import core

VIEWS = ("open", "done", "archived")
PROJECT_STATUSES = ("active", "paused", "done", "archived")
URGENCIES = (1, 2, 3)
TITLE_LIMIT = 200


class TaskError(Exception):
    pass


@dataclass
class Task:
    id: int
    title: str
    urgency: int
    status: str
    project: str | None
    project_id: int | None
    created_at: str
    updated_at: str
    done_at: str | None
    archived_at: str | None


@dataclass
class Note:
    id: int
    created_at: str
    body: str


@dataclass
class Project:
    id: int
    name: str
    description: str
    status: str
    open_tasks: int


TASK_SELECT = """
SELECT t.id, t.title, t.urgency, t.status, p.name, t.project_id,
       t.created_at, t.updated_at, t.done_at, t.archived_at
FROM tasks t LEFT JOIN projects p ON p.id = t.project_id
"""


def _like(text: str) -> str:
    """A LIKE pattern that matches `text` anywhere, with % and _ taken literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def tasks(conn: sqlite3.Connection, view: str = "open", search: str = "") -> list[Task]:
    """Tasks in one view. Open: most urgent first, then most recently changed. Done and archived: newest first.

    `search` matches the title, the project name, or any note, ignoring case.
    """
    if view not in VIEWS:
        raise TaskError(f"unknown view {view!r}")
    sql = TASK_SELECT + " WHERE t.status = ?"
    params: list = [view]
    if search.strip():
        like = _like(search.strip())
        sql += (" AND (t.title LIKE ? ESCAPE '\\' OR coalesce(p.name, '') LIKE ? ESCAPE '\\'"
                " OR EXISTS (SELECT 1 FROM task_notes n WHERE n.task_id = t.id AND n.body LIKE ? ESCAPE '\\'))")
        params += [like, like, like]
    if view == "open":
        sql += " ORDER BY t.urgency DESC, t.updated_at DESC, t.id DESC"
    else:
        sql += " ORDER BY coalesce(t.archived_at, t.done_at) DESC, t.id DESC"
    return [Task(*row) for row in conn.execute(sql, params)]


def get(conn: sqlite3.Connection, task_id: int) -> Task:
    row = conn.execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    if row is None:
        raise TaskError(f"task {task_id} not found")
    return Task(*row)


def _check_title(title: str) -> str:
    title = title.strip()
    if not 1 <= len(title) <= TITLE_LIMIT:
        raise TaskError(f"a title must be 1 to {TITLE_LIMIT} characters")
    return title


def _check_urgency(urgency: int) -> int:
    if urgency not in URGENCIES:
        raise TaskError("urgency must be 1, 2, or 3")
    return urgency


def add(conn: sqlite3.Connection, title: str, urgency: int = 2, project_id: int | None = None) -> int:
    now = core.utc_now()
    cur = conn.execute(
        "INSERT INTO tasks (project_id, title, urgency, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, _check_title(title), _check_urgency(urgency), now, now),
    )
    return cur.lastrowid


def _change(conn: sqlite3.Connection, task_id: int, sql: str, params: tuple, wrong_state: str) -> None:
    """Run one UPDATE that names the task's required status. Zero rows changed means missing or wrong state."""
    if conn.execute(sql, params).rowcount == 0:
        get(conn, task_id)
        raise TaskError(f"task {task_id}: {wrong_state}")


def set_urgency(conn: sqlite3.Connection, task_id: int, urgency: int) -> None:
    _change(conn, task_id, "UPDATE tasks SET urgency = ?, updated_at = ? WHERE id = ? AND status = 'open'",
            (_check_urgency(urgency), core.utc_now(), task_id), "only open tasks change urgency")


def set_project(conn: sqlite3.Connection, task_id: int, project_id: int | None) -> None:
    _change(conn, task_id, "UPDATE tasks SET project_id = ?, updated_at = ? WHERE id = ?",
            (project_id, core.utc_now(), task_id), "not changed")


def mark_done(conn: sqlite3.Connection, task_id: int) -> None:
    now = core.utc_now()
    _change(conn, task_id, "UPDATE tasks SET status = 'done', done_at = ?, updated_at = ? WHERE id = ? AND status = 'open'",
            (now, now, task_id), "only open tasks can be marked done")


def reopen(conn: sqlite3.Connection, task_id: int) -> None:
    _change(conn, task_id, "UPDATE tasks SET status = 'open', done_at = NULL, updated_at = ? WHERE id = ? AND status = 'done'",
            (core.utc_now(), task_id), "only done tasks can be reopened")


def archive(conn: sqlite3.Connection, task_id: int) -> None:
    now = core.utc_now()
    _change(conn, task_id, "UPDATE tasks SET status = 'archived', archived_at = ?, updated_at = ? WHERE id = ? AND status = 'done'",
            (now, now, task_id), "only done tasks can be archived")


def restore(conn: sqlite3.Connection, task_id: int) -> None:
    """Back to open, from the archive."""
    _change(conn, task_id,
            "UPDATE tasks SET status = 'open', done_at = NULL, archived_at = NULL, updated_at = ? WHERE id = ? AND status = 'archived'",
            (core.utc_now(), task_id), "only archived tasks can be restored")


def add_note(conn: sqlite3.Connection, task_id: int, body: str) -> int:
    """Append a stamped note. Notes are never edited or removed."""
    body = body.strip()
    if not body:
        raise TaskError("a note cannot be empty")
    get(conn, task_id)
    now = core.utc_now()
    with core.transaction(conn):
        cur = conn.execute("INSERT INTO task_notes (task_id, created_at, body) VALUES (?, ?, ?)", (task_id, now, body))
        conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
    return cur.lastrowid


def notes(conn: sqlite3.Connection, task_id: int) -> list[Note]:
    rows = conn.execute("SELECT id, created_at, body FROM task_notes WHERE task_id = ? ORDER BY created_at, id", (task_id,))
    return [Note(*row) for row in rows]


def archivable(conn: sqlite3.Connection, days: int, now: datetime | None = None) -> list[int]:
    """Ids of done tasks finished more than `days` days ago."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute("SELECT id FROM tasks WHERE status = 'done' AND done_at < ? ORDER BY id", (cutoff,))
    return [row[0] for row in rows]


def archive_many(conn: sqlite3.Connection, task_ids: list[int]) -> None:
    """Archive all of them, or none."""
    now = core.utc_now()
    with core.transaction(conn):
        for task_id in task_ids:
            _change(conn, task_id, "UPDATE tasks SET status = 'archived', archived_at = ?, updated_at = ? WHERE id = ? AND status = 'done'",
                    (now, now, task_id), "only done tasks can be archived")


def projects(conn: sqlite3.Connection, include_archived: bool = True) -> list[Project]:
    """Projects with their open task counts: active first, then paused, done, archived; by name within each."""
    sql = """
    SELECT p.id, p.name, p.description, p.status,
           (SELECT count(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'open')
    FROM projects p
    """
    if not include_archived:
        sql += " WHERE p.status != 'archived'"
    sql += """ ORDER BY CASE p.status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 WHEN 'done' THEN 2 ELSE 3 END,
                        p.name COLLATE NOCASE"""
    return [Project(*row) for row in conn.execute(sql)]


def add_project(conn: sqlite3.Connection, name: str, description: str = "") -> int:
    name = name.strip()
    if not 1 <= len(name) <= TITLE_LIMIT:
        raise TaskError(f"a project name must be 1 to {TITLE_LIMIT} characters")
    try:
        cur = conn.execute("INSERT INTO projects (name, description, created_at) VALUES (?, ?, ?)",
                           (name, description.strip(), core.utc_now()))
    except sqlite3.IntegrityError:
        raise TaskError("a project with that name already exists in this area") from None
    return cur.lastrowid


def set_project_status(conn: sqlite3.Connection, project_id: int, status: str) -> None:
    if status not in PROJECT_STATUSES:
        raise TaskError(f"unknown project status {status!r}")
    if conn.execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id)).rowcount == 0:
        raise TaskError(f"project {project_id} not found")


def export_archived(conn: sqlite3.Connection, area: str, label: str, now: datetime | None = None) -> tuple[Path, int]:
    """Write the archived tasks and their notes to ~/.bench/exports/<stamp>.<area>.archived_tasks.md.

    Plain prose, newest first. Written to a temporary file (owner only) and renamed, so a half written
    export never appears. Returns the path and how many tasks it holds.
    """
    archived = tasks(conn, "archived")
    folder = paths.exports_dir()
    folder.mkdir(mode=paths.PRIVATE, parents=True, exist_ok=True)
    path = stamps.stamped_path(folder, f"{area}.archived_tasks", ".md", now)
    when = (now or datetime.now(timezone.utc)).astimezone(core.CENTRAL).strftime("%Y-%m-%d %H:%M")
    lines = [f"# Archived tasks: {label}", "",
             f"Exported {when} Central from the {area} area. {len(archived)} archived task(s), newest first.", ""]
    for task in archived:
        lines += [f"## Task {task.id}: {task.title}", ""]
        done = core.to_central(task.done_at) if task.done_at else "never"
        lines += [f"Urgency {task.urgency}. Project: {task.project or 'none'}. Created {core.to_central(task.created_at)}, "
                  f"done {done}, archived {core.to_central(task.archived_at)}.", ""]
        task_notes = notes(conn, task.id)
        if not task_notes:
            lines += ["No notes.", ""]
        for note in task_notes:
            lines += [f"{core.to_central(note.created_at)}: {note.body}", ""]
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines))
    os.replace(temporary, path)
    return path, len(archived)
