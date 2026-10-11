"""The grid layout: four panes, two by two, in one window."""
import shutil
import time

import pytest

from bench.cockpit.plan import execute, make_plan, status_lines
from bench.cockpit.tmux import PaneState, Tmux
from bench.config import ConfigError, load

CORNERS = {"top-left": "dev", "top-right": "db", "bottom-left": "git", "bottom-right": "dashboard"}


def write_grid_config(path, folder, socket=None, command='["sleep", "30"]'):
    socket = socket or path.parent / "state" / "tmux" / "bench.sock"
    panes = "".join(f'''
[[cockpit.pane]]
window = "shells"
role = "{role}"
position = "{position}"
folder = "{folder}"
''' + (f"command = {command}\n" if role == "dashboard" else "") for position, role in CORNERS.items())
    path.write_text(f'''
timezone = "America/Chicago"

[cockpit]
socket = "{socket}"
session = "bench"
dashboard_window = "shells"
allowed_roots = ["{folder}"]

[[cockpit.window]]
name = "shells"
layout = "grid"
{panes}''')
    return path


def state(role, pane_id="%1"):
    return PaneState("shells", pane_id, role, "zsh", "/x", False, "")


def plan_rows(actions):
    return [(a.kind, a.pane.role, a.split_from, a.flags) for a in actions]


def test_grid_config_loads(tmp_path):
    cockpit = load(write_grid_config(tmp_path / "config.toml", tmp_path)).cockpit
    shells = cockpit.windows[0]
    assert shells.layout == "grid"
    assert {p.position: p.role for p in shells.panes} == CORNERS
    assert [p.command for p in shells.panes if p.command] == [["sleep", "30"]]


def test_grid_needs_all_four_corners(tmp_path):
    path = write_grid_config(tmp_path / "config.toml", tmp_path)
    path.write_text(path.read_text().replace('position = "bottom-right"', 'position = "top-left"'))
    with pytest.raises(ConfigError, match="needs exactly one pane at each of top-left, top-right, bottom-left, bottom-right"):
        load(path)


def test_new_grid_splits_into_halves_then_quarters(tmp_path):
    cockpit = load(write_grid_config(tmp_path / "config.toml", tmp_path)).cockpit
    assert plan_rows(make_plan(cockpit, False, [])) == [
        ("new-session", "dev", None, ()),
        ("split", "db", "dev", ("-h",)),
        ("split", "git", "dev", ("-v",)),
        ("split", "dashboard", "db", ("-v",)),
    ]


@pytest.mark.parametrize("missing, expected", [
    ("dev", ("split", "dev", "git", ("-v", "-b"))),
    ("db", ("split", "db", "dashboard", ("-v", "-b"))),
    ("git", ("split", "git", "dev", ("-v",))),
    ("dashboard", ("split", "dashboard", "db", ("-v",))),
])
def test_one_missing_corner_is_rebuilt_alone(tmp_path, missing, expected):
    cockpit = load(write_grid_config(tmp_path / "config.toml", tmp_path)).cockpit
    panes = [state(role) for role in CORNERS.values() if role != missing]
    assert plan_rows(make_plan(cockpit, True, panes)) == [expected]


def test_missing_left_column_is_rebuilt_from_the_right(tmp_path):
    cockpit = load(write_grid_config(tmp_path / "config.toml", tmp_path)).cockpit
    assert plan_rows(make_plan(cockpit, True, [state("db"), state("dashboard")])) == [
        ("split", "dev", "db", ("-h", "-b")),
        ("split", "git", "dev", ("-v",)),
    ]


@pytest.mark.needs_tmux
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_real_tmux_draws_two_by_two(short_tmp):
    folder = short_tmp / "work"
    folder.mkdir()
    socket = short_tmp / "s" / "t.sock"
    socket.parent.mkdir(mode=0o700)
    cockpit = load(write_grid_config(short_tmp / "config.toml", folder, socket=socket)).cockpit
    tmux = Tmux("tmux", socket)
    try:
        execute(tmux, cockpit, make_plan(cockpit, False, []), [])
        time.sleep(1)
        out = tmux.run("list-panes", "-t", "=bench:shells", "-F", "#{@role} #{pane_left} #{pane_top}").stdout
        corners = {role: (int(left), int(top)) for role, left, top in (line.split() for line in out.splitlines())}
        left, right = corners["dev"][0], corners["db"][0]
        top, bottom = corners["dev"][1], corners["git"][1]
        assert left < right and top < bottom
        assert corners == {"dev": (left, top), "db": (right, top), "git": (left, bottom), "dashboard": (right, bottom)}
        assert make_plan(cockpit, True, tmux.panes("bench")) == []
        assert sum("running (sleep)" in line for line in status_lines(cockpit, tmux.panes("bench"))) == 1
    finally:
        tmux.run("kill-server", check=False)
