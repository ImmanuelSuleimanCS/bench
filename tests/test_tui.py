"""The dashboard, run headless with fake readings, so the test never touches tmux or pmset.

The area databases are real, in a throwaway BENCH_HOME.
"""
import asyncio

from textual.containers import Grid
from textual.widgets import HelpPanel, Static

from bench.config import load
from bench.db import core, migrate, tasks
from bench.status import Snapshot
from bench.tui.app import PANELS, BenchApp, columns_for
from tests.conftest import write_config


def open_areas() -> dict:
    connections = {}
    for area in core.AREAS:
        migrate.prepare(area)
        connections[area] = core.connect(core.db_path(area))
    return connections


def make_app(tmp_path, readings, connections=None):
    cfg = load(write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path))
    return BenchApp(cfg, notes=["client: daily backup x.db"], connections=connections,
                    read_status=lambda c: readings.pop(0),
                    read_databases=lambda c: "DATABASE LINES",
                    read_cockpit=lambda c: "COCKPIT LINES")


def text(app, panel_id):
    return str(app.query_one(f"#{panel_id}", Static).content)


def test_board_shows_every_panel_live_and_later(tmp_path, bench_home):
    connections = open_areas()
    for n in range(7):
        tasks.add(connections["client"], f"client task {n}", urgency=3 if n == 6 else 1)
    garden = tasks.add_project(connections["personal"], "Garden [beds]")
    tasks.add(connections["personal"], "buy soil", project_id=garden)
    readings = [Snapshot("cockpit 3 of 3", "caffeinate on 12m", "memory normal", "AC 80%"),
                Snapshot(None, "caffeinate on 42m", "memory normal", "AC 80%")]
    app = make_app(tmp_path, readings, connections)

    async def scenario():
        async with app.run_test(size=(240, 50)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            titles = [app.query_one(f"#{panel_id}", Static).border_title for panel_id, _, _ in PANELS]
            assert titles == ["System", "Cockpit", "Dev status", "Tasks", "Workout", "Projects"]
            assert app.query_one("#board", Grid).styles.grid_size_columns == 3
            system = text(app, "system")
            assert "caffeinate on 12m" in system and "memory normal" in system and "AC 80%" in system
            assert "DATABASE LINES" in system
            assert "cockpit 3 of 3" in text(app, "cockpit") and "COCKPIT LINES" in text(app, "cockpit")
            board_tasks = text(app, "tasks")
            assert "7 open" in board_tasks and "and 4 more" in board_tasks
            assert board_tasks.index("client task 6") < board_tasks.index("client task 5")
            assert "client task 3" not in board_tasks
            assert "Garden \\[beds]" in text(app, "projects") and "1 open" in text(app, "projects")
            assert text(app, "workout").startswith("Stage F")
            assert text(app, "dev").startswith("Stage G")
            await pilot.press("question_mark")
            await pilot.pause()
            assert app.screen.query(HelpPanel)
            await pilot.press("question_mark")
            await pilot.pause()
            assert not app.screen.query(HelpPanel)
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "caffeinate on 42m" in text(app, "system")
            assert "[dim]cockpit 3 of 3[/dim]" in text(app, "cockpit")
            await pilot.press("q")
        assert app.return_code == 0

    asyncio.run(scenario())
    assert readings == []


def test_each_refresh_takes_due_backups(tmp_path):
    calls = []
    app = make_app(tmp_path, [Snapshot()] * 3)
    app.take_backups = lambda: calls.append(1) or (["etl: daily backup y.db"] if len(calls) == 2 else [])
    notices = []
    real_notify = app.notify
    app.notify = lambda message, **kw: (notices.append(str(message)), real_notify(message, **kw))

    async def scenario():
        async with app.run_test(size=(200, 50)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("q")

    asyncio.run(scenario())
    assert len(calls) == 2
    assert "etl: daily backup y.db" in notices


def test_narrow_windows_get_fewer_columns(tmp_path):
    assert [columns_for(w) for w in (300, 150, 149, 90, 89, 40)] == [3, 3, 2, 2, 1, 1]
    app = make_app(tmp_path, [Snapshot()])

    async def scenario():
        async with app.run_test(size=(80, 40)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.query_one("#board", Grid).styles.grid_size_columns == 1
            assert "database not open" in text(app, "tasks")
            await pilot.press("t")
            await pilot.pause()
            assert len(app.screen_stack) == 1  # no databases, so t does nothing
            await pilot.press("q")

    asyncio.run(scenario())
