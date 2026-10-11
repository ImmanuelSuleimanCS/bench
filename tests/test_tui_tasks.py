"""The Tasks and Projects screens, driven by key presses, headless, on real databases in a throwaway BENCH_HOME."""
import asyncio
import logging

from textual.widgets import DataTable, Input, Static

from bench.db import core, tasks
from bench.status import Snapshot
from bench.tui.forms import Confirm
from bench.tui.projects import ProjectsScreen
from bench.tui.tasks import TaskDetail, TasksScreen
from tests.test_tui import make_app, open_areas

TITLE = "renew the domain"
NOTES = ["registrar emailed back", "paid for two years"]


async def type_into(app, pilot, text):
    """Fill the pop up's text field and press enter."""
    await pilot.pause()
    app.screen.query_one(Input).value = text
    await pilot.press("enter")
    await pilot.pause()


def rows(app):
    table = app.screen.query_one(DataTable)
    return [table.get_row_at(i) for i in range(table.row_count)]


def test_task_lifecycle_by_keys_and_nothing_sensitive_in_notices(tmp_path, bench_home, caplog):
    caplog.set_level(logging.DEBUG)
    connections = open_areas()
    app = make_app(tmp_path, [Snapshot()] * 5, connections)
    notices = []
    real_notify = app.notify
    app.notify = lambda message, **kw: (notices.append(str(message)), real_notify(message, **kw))
    client = connections["client"]

    async def scenario():
        async with app.run_test(size=(200, 50)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("t")
            await pilot.pause()
            assert isinstance(app.screen, TasksScreen)

            await pilot.press("c")
            await type_into(app, pilot, TITLE)
            [task] = tasks.tasks(client, "open")
            assert (task.title, task.urgency) == (TITLE, 2)
            await pilot.press("3")
            await pilot.pause()
            assert tasks.get(client, task.id).urgency == 3
            for note in NOTES:
                await pilot.press("n")
                await type_into(app, pilot, note)
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, TaskDetail)
            detail = str(app.screen.query_one("#detail", Static).content)
            assert detail.index(NOTES[0]) < detail.index(NOTES[1])
            await pilot.press("escape")
            await pilot.pause()

            await pilot.press("a")  # open tasks are not archived directly
            await pilot.press("d")
            await pilot.pause()
            assert tasks.get(client, task.id).status == "done"
            await pilot.press("v")
            await pilot.press("a")
            await pilot.pause()
            assert tasks.get(client, task.id).status == "archived"
            await pilot.press("v")
            await pilot.press("slash")
            app.screen.query_one("#filter", Input).value = "two years"
            await pilot.press("escape")  # escape in the filter goes back to the list, not to the board
            await pilot.pause()
            assert isinstance(app.screen, TasksScreen) and isinstance(app.screen.focused, DataTable)
            assert [str(r[0]) for r in rows(app)] == [str(task.id)]
            await pilot.press("e")
            await pilot.pause()
            assert isinstance(app.screen, Confirm) and "full title and every note" in str(app.screen.query(Static).first().content)
            await pilot.press("n")
            await pilot.pause()
            assert not (bench_home / "exports").exists() or not list((bench_home / "exports").iterdir())
            await pilot.press("e")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            exported = list((bench_home / "exports").glob("*.client.archived_tasks.md"))
            assert len(exported) == 1 and NOTES[1] in exported[0].read_text()
            # The filter is kept when the area changes, and cleared by emptying it.
            await pilot.press("right_square_bracket")
            await pilot.pause()
            assert app.screen.query_one("#filter", Input).value == "two years" and rows(app) == []
            await pilot.press("left_square_bracket")
            await pilot.pause()
            app.screen.query_one("#filter", Input).value = ""
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            back = tasks.get(client, task.id)
            assert (back.status, back.urgency) == ("open", 3)

            # The work area is checked: a ten digit number and an email ask first, and no is the default.
            await pilot.press("right_square_bracket")
            await pilot.pause()
            assert app.area == "work"
            risky = "call 5551234567 or mail a.person@example.com"
            await pilot.press("c")
            await type_into(app, pilot, risky)
            assert isinstance(app.screen, Confirm)
            shown = str(app.screen.query(Static).first().content)
            assert "5551234567" in shown and "a.person@example.com" in shown
            assert "Saving stores the whole text" in shown
            await pilot.press("enter")
            await pilot.pause()
            assert tasks.tasks(connections["work"], "open") == []
            await pilot.press("c")
            await type_into(app, pilot, risky)
            await pilot.press("y")
            await pilot.pause()
            assert [t.title for t in tasks.tasks(connections["work"], "open")] == [risky]
            # The same text in an unchecked area saves without asking.
            await pilot.press("right_square_bracket")
            await pilot.press("c")
            await type_into(app, pilot, risky)
            assert [t.title for t in tasks.tasks(connections["personal"], "open")] == [risky]

            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert "1 open" in str(app.query_one("#tasks", Static).content)
            await pilot.press("q")

    asyncio.run(scenario())
    said = "\n".join(notices) + caplog.text
    for secret in (TITLE, *NOTES, "5551234567", "a.person@example.com"):
        assert secret not in said
    assert "task 1 archived" in said and "task 1 restored to open" in said
    # What the screens saved is in the files, seen through a fresh connection.
    fresh = core.connect(core.db_path("client"))
    assert fresh.execute("SELECT count(*) FROM task_notes").fetchone() == (2,)
    fresh.close()


def test_projects_screen_adds_and_steps_status(tmp_path, bench_home):
    connections = open_areas()
    app = make_app(tmp_path, [Snapshot()] * 5, connections)
    personal = connections["personal"]

    async def scenario():
        async with app.run_test(size=(200, 50)) as pilot:
            await app.workers.wait_for_complete()
            app.area = "personal"
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, ProjectsScreen)
            await pilot.press("c")
            await type_into(app, pilot, "Garden")
            await type_into(app, pilot, "raised beds")
            [project] = tasks.projects(personal)
            assert (project.name, project.description, project.status) == ("Garden", "raised beds", "active")
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("escape")  # cancelling the status list changes nothing
            await pilot.pause()
            assert tasks.projects(personal)[0].status == "active"
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("down", "enter")  # the list opens on the current status; down is paused
            await pilot.pause()
            assert tasks.projects(personal)[0].status == "paused"
            await pilot.press("c")
            await type_into(app, pilot, "Garden")
            await type_into(app, pilot, "")
            assert len(tasks.projects(personal)) == 1  # the duplicate name was refused

            await pilot.press("escape")
            await pilot.pause()
            assert "Garden" in str(app.query_one("#projects", Static).content)
            task_id = tasks.add(personal, "buy soil")
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()
            assert tasks.get(personal, task_id).project == "Garden"
            await pilot.press("escape", "q")

    asyncio.run(scenario())
    for conn in connections.values():
        conn.close()
    assert core.db_path("personal").stat().st_mode & 0o777 == 0o600
