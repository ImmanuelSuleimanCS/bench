import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from bench.db import core, migrate, tasks


@pytest.fixture
def conn(bench_home):
    migrate.prepare("client")
    connection = core.connect(core.db_path("client"))
    yield connection
    connection.close()


def test_whole_lifecycle_open_done_archived_found_restored(conn):
    task_id = tasks.add(conn, "  renew the domain  ")
    assert tasks.get(conn, task_id).urgency == 2
    assert tasks.get(conn, task_id).title == "renew the domain"
    tasks.set_urgency(conn, task_id, 3)
    tasks.add_note(conn, task_id, "registrar emailed")
    tasks.add_note(conn, task_id, "paid for two years")
    assert [n.body for n in tasks.notes(conn, task_id)] == ["registrar emailed", "paid for two years"]
    tasks.mark_done(conn, task_id)
    assert [t.id for t in tasks.tasks(conn, "done")] == [task_id]
    assert tasks.tasks(conn, "open") == []
    tasks.archive(conn, task_id)
    found = tasks.tasks(conn, "archived", search="TWO YEARS")
    assert [t.id for t in found] == [task_id]
    assert found[0].done_at and found[0].archived_at
    tasks.restore(conn, task_id)
    back = tasks.get(conn, task_id)
    assert (back.status, back.done_at, back.archived_at, back.urgency) == ("open", None, None, 3)


def test_open_tasks_most_urgent_first_then_most_recent(conn):
    low = tasks.add(conn, "low", 1)
    first = tasks.add(conn, "first high", 3)
    second = tasks.add(conn, "second high", 3)
    middle = tasks.add(conn, "middle", 2)
    assert [t.id for t in tasks.tasks(conn, "open")] == [second, first, middle, low]
    tasks.add_note(conn, first, "touched")  # same second as `second`; the id breaks ties, newest first
    conn.execute("UPDATE tasks SET updated_at = '2099-01-01T00:00:00+00:00' WHERE id = ?", (first,))
    assert [t.id for t in tasks.tasks(conn, "open")][:2] == [first, second]


def test_search_matches_title_project_and_notes_and_takes_percent_literally(conn):
    project = tasks.add_project(conn, "Garage")
    a = tasks.add(conn, "paint the door", project_id=project)
    b = tasks.add(conn, "100% done soon")
    c = tasks.add(conn, "call the plumber")
    tasks.add_note(conn, c, "ask about the water heater")
    assert [t.id for t in tasks.tasks(conn, "open", "garage")] == [a]
    assert [t.id for t in tasks.tasks(conn, "open", "heater")] == [c]
    assert [t.id for t in tasks.tasks(conn, "open", "%")] == [b]
    assert tasks.tasks(conn, "open", "nothing like this") == []


def test_wrong_state_errors_name_the_id_never_the_title(conn):
    task_id = tasks.add(conn, "secret sounding title")
    for change in (tasks.archive, tasks.reopen, tasks.restore):
        with pytest.raises(tasks.TaskError) as caught:
            change(conn, task_id)
        assert f"task {task_id}" in str(caught.value)
        assert "secret" not in str(caught.value)
    tasks.mark_done(conn, task_id)
    with pytest.raises(tasks.TaskError):
        tasks.set_urgency(conn, task_id, 1)
    with pytest.raises(tasks.TaskError, match="task 999 not found"):
        tasks.mark_done(conn, 999)
    for bad in ("", "   ", "x" * 201):
        with pytest.raises(tasks.TaskError):
            tasks.add(conn, bad)
    with pytest.raises(tasks.TaskError):
        tasks.add(conn, "fine", urgency=4)
    with pytest.raises(tasks.TaskError):
        tasks.add_note(conn, task_id, "  ")


def test_the_database_refuses_every_delete_and_note_edit(conn):
    project = tasks.add_project(conn, "Kept")
    empty = tasks.add_project(conn, "Empty")
    task_id = tasks.add(conn, "has a note", project_id=project)
    lonely = tasks.add(conn, "no notes, nothing points at it")
    tasks.add_note(conn, task_id, "this note keeps the task")
    for sql, args in (("DELETE FROM tasks WHERE id = ?", (task_id,)),
                      ("DELETE FROM tasks WHERE id = ?", (lonely,)),
                      ("DELETE FROM projects WHERE id = ?", (project,)),
                      ("DELETE FROM projects WHERE id = ?", (empty,)),
                      ("DELETE FROM task_notes", ()),
                      ("UPDATE task_notes SET body = 'rewritten'", ())):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, args)
    assert conn.execute("SELECT count(*) FROM tasks").fetchone() == (2,)
    assert conn.execute("SELECT count(*) FROM projects").fetchone() == (2,)
    assert [n.body for n in tasks.notes(conn, task_id)] == ["this note keeps the task"]


def test_archiving_a_project_keeps_its_tasks(conn):
    project = tasks.add_project(conn, "Old work")
    task_id = tasks.add(conn, "still belongs", project_id=project)
    tasks.set_project_status(conn, project, "archived")
    assert tasks.get(conn, task_id).project == "Old work"
    assert tasks.projects(conn, include_archived=False) == []
    assert tasks.projects(conn)[0].open_tasks == 1
    tasks.set_project(conn, task_id, None)
    assert tasks.get(conn, task_id).project is None
    with pytest.raises(sqlite3.IntegrityError):
        tasks.set_project(conn, task_id, 999)


def test_bulk_archive_only_takes_old_done_tasks_and_is_all_or_nothing(conn):
    old = tasks.add(conn, "old")
    recent = tasks.add(conn, "recent")
    still_open = tasks.add(conn, "open")
    for task_id in (old, recent):
        tasks.mark_done(conn, task_id)
    conn.execute("UPDATE tasks SET done_at = '2026-01-01T12:00:00+00:00' WHERE id = ?", (old,))
    now = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
    assert tasks.archivable(conn, 14, now) == [old]
    assert tasks.archivable(conn, 14, now + timedelta(days=30)) == [old, recent]
    with pytest.raises(tasks.TaskError):
        tasks.archive_many(conn, [old, still_open])
    assert tasks.get(conn, old).status == "done"
    tasks.archive_many(conn, [old])
    assert tasks.get(conn, old).status == "archived"


def test_projects_status_counts_and_order(conn):
    b = tasks.add_project(conn, "beta", "second one")
    a = tasks.add_project(conn, "Alpha")
    tasks.add(conn, "one", project_id=a)
    tasks.add(conn, "two", project_id=a)
    with pytest.raises(tasks.TaskError, match="already exists"):
        tasks.add_project(conn, "beta")
    assert [(p.name, p.open_tasks) for p in tasks.projects(conn)] == [("Alpha", 2), ("beta", 0)]
    tasks.set_project_status(conn, a, "archived")
    assert [p.name for p in tasks.projects(conn)] == ["beta", "Alpha"]
    assert [p.name for p in tasks.projects(conn, include_archived=False)] == ["beta"]
    with pytest.raises(tasks.TaskError):
        tasks.set_project_status(conn, b, "lost")


def test_export_is_stamped_owner_only_prose(conn, bench_home):
    first = tasks.add(conn, "file the receipts")
    tasks.add_note(conn, first, "scanned")
    tasks.add_note(conn, first, "sent to the accountant")
    second = tasks.add(conn, "no notes here")
    for task_id in (first, second):
        tasks.mark_done(conn, task_id)
        tasks.archive(conn, task_id)
    tasks.add(conn, "still open, not exported")
    now = datetime(2026, 10, 10, 23, 5, tzinfo=timezone.utc)
    path, count = tasks.export_archived(conn, "client", "Client", now)
    assert count == 2
    assert path == bench_home / "exports" / "20261010.1805.client.archived_tasks.md"
    assert path.stat().st_mode & 0o777 == 0o600
    text = path.read_text()
    assert text.index("scanned") < text.index("sent to the accountant")
    assert "file the receipts" in text and "No notes." in text
    assert "still open" not in text
    assert "|" not in text and "\u2014" not in text
    assert list(path.parent.glob("*.tmp")) == []
    again, _ = tasks.export_archived(conn, "client", "Client", now)
    assert again.name == "20261010.1805.client.archived_tasks.2.md"
