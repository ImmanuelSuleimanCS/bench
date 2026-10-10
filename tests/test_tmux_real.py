"""One test against real tmux, on a throwaway socket. Recorded output can drift from what tmux prints;
this catches that. Skipped when tmux is not installed."""
import shutil
import time

import pytest

from bench.cockpit.plan import execute, make_plan, status_lines
from bench.cockpit.tmux import Tmux
from bench.config import load
from tests.conftest import write_config

pytestmark = [
    pytest.mark.needs_tmux,
    pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed"),
]


def test_build_read_back_and_nothing_to_do(short_tmp):
    folder = short_tmp / "work"
    folder.mkdir()
    socket = short_tmp / "s" / "t.sock"
    socket.parent.mkdir(mode=0o700)
    cfg = load(write_config(short_tmp / "config.toml", [short_tmp], folder, folder,
                            command='["sleep", "30"]', socket=socket)).cockpit
    tmux = Tmux("tmux", socket)
    try:
        actions = make_plan(cfg, tmux.has_session("bench"), tmux.panes("bench"))
        assert len(actions) == 4
        execute(tmux, cfg, actions, [])
        time.sleep(2)
        panes = tmux.panes("bench")
        assert {(p.window, p.role) for p in panes} == {
            ("agents", "Builder"), ("agents", "Terminal One"), ("agents", "Terminal Zero"), ("dashboard", "dashboard")}
        assert make_plan(cfg, True, panes) == []
        lines = status_lines(cfg, panes)
        assert sum("running (sleep)" in line for line in lines) == 3
    finally:
        tmux.run("kill-server", check=False)
