import shlex
import subprocess

from bench.cockpit.plan import make_plan, pane_status, shell_command
from bench.cockpit.tmux import PaneState
from bench.config import load
from tests.conftest import write_config


def cockpit(tmp_path):
    return load(write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path)).cockpit


def state(window, role, command="zsh", dead=False, pane_id="%1"):
    return PaneState(window, pane_id, role, command, "/x", dead, "")


def test_no_session_creates_everything_in_order(tmp_path):
    actions = make_plan(cockpit(tmp_path), False, [])
    assert [(a.kind, a.pane.role, a.split_from, a.flags) for a in actions] == [
        ("new-session", "Terminal One", None, ()),
        ("split", "Builder", "Terminal One", ("-h",)),
        ("split", "Terminal Zero", "Terminal One", ("-v",)),
        ("new-window", "dashboard", None, ()),
    ]


def test_complete_session_has_nothing_to_do(tmp_path):
    panes = [state("agents", r) for r in ("Builder", "Terminal One", "Terminal Zero")] + [state("dashboard", "dashboard")]
    assert make_plan(cockpit(tmp_path), True, panes) == []


def test_one_missing_pane_gives_one_action(tmp_path):
    panes = [state("agents", r) for r in ("Builder", "Terminal One")] + [state("dashboard", "dashboard")]
    actions = make_plan(cockpit(tmp_path), True, panes)
    assert [(a.kind, a.pane.role, a.split_from, a.flags) for a in actions] == [
        ("split", "Terminal Zero", "Terminal One", ("-v",))]


def test_missing_window_is_added(tmp_path):
    panes = [state("agents", r) for r in ("Builder", "Terminal One", "Terminal Zero")]
    actions = make_plan(cockpit(tmp_path), True, panes)
    assert [(a.kind, a.pane.role) for a in actions] == [("new-window", "dashboard")]


def test_pane_status_words(tmp_path):
    spec = cockpit(tmp_path).windows[0].panes[0]
    assert pane_status(spec, None) == "missing"
    assert pane_status(spec, state("agents", spec.role, "zsh")) == "command exited, shell open"
    assert pane_status(spec, state("agents", spec.role, "2.1.296")) == "running (2.1.296)"
    assert pane_status(spec, state("agents", spec.role, "zsh", dead=True)).startswith("dead")


def test_quoting_survives_a_single_quote():
    command = ["printf", "%s\\n", "it's here", "two words"]
    outer = shlex.split(shell_command(command))
    assert outer[:6] == ["arch", "-arm64", "/bin/zsh", "-l", "-i", "-c"]
    inner = outer[6]
    assert inner.endswith("; exec /bin/zsh -l")
    program = inner.removesuffix("; exec /bin/zsh -l")
    assert shlex.split(program) == command
    run = subprocess.run(["/bin/zsh", "-c", program], capture_output=True, text=True)
    assert run.stdout.splitlines() == ["it's here", "two words"]


def test_no_command_means_default_shell():
    assert shell_command(None) is None
