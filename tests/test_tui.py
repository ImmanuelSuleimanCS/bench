"""The dashboard, run headless with fake readings, so the test never touches tmux, pmset, or databases."""
import asyncio

from textual.containers import Grid
from textual.widgets import HelpPanel, Static

from bench.config import load
from bench.status import Snapshot
from bench.tui.app import PANELS, BenchApp, columns_for
from tests.conftest import write_config


def make_app(tmp_path, readings):
    cfg = load(write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path))
    return BenchApp(cfg, notes=["client: daily backup x.db"],
                    read_status=lambda c: readings.pop(0),
                    read_databases=lambda c: "DATABASE LINES",
                    read_cockpit=lambda c: "COCKPIT LINES")


def text(app, panel_id):
    return str(app.query_one(f"#{panel_id}", Static).content)


def test_board_shows_every_panel_live_and_later(tmp_path):
    readings = [Snapshot("cockpit 3 of 3", "caffeinate on 12m", "memory normal", "AC 80%"),
                Snapshot(None, "caffeinate on 42m", "memory normal", "AC 80%")]
    app = make_app(tmp_path, readings)

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
            assert text(app, "tasks").startswith("Stage E")
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


def test_narrow_windows_get_fewer_columns(tmp_path):
    assert [columns_for(w) for w in (300, 150, 149, 90, 89, 40)] == [3, 3, 2, 2, 1, 1]
    app = make_app(tmp_path, [Snapshot()])

    async def scenario():
        async with app.run_test(size=(80, 40)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.query_one("#board", Grid).styles.grid_size_columns == 1
            await pilot.press("q")

    asyncio.run(scenario())
