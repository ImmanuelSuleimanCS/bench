import pytest

from bench.cockpit.plan import attach_argv
from bench.config import load
from tests.conftest import write_config


def test_attach_always_opens_on_a_known_window(tmp_path):
    cockpit = load(write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path)).cockpit
    base = ["tmux", "-S", "/s"]
    assert attach_argv(cockpit, base, False, False, None) == [
        *base, "select-window", "-t", "=bench:agents", ";", "attach", "-t", "=bench"]
    assert attach_argv(cockpit, base, True, True, None) == [
        *base, "select-window", "-t", "=bench-left:dashboard", ";", "attach", "-t", "=bench-left"]
    assert attach_argv(cockpit, base, True, False, None) == [
        *base, "new-session", "-t", "=bench", "-s", "bench-left", ";", "select-window", "-t", "=bench-left:dashboard"]
    assert attach_argv(cockpit, base, False, False, "dashboard")[5] == "=bench:dashboard"
    with pytest.raises(ValueError, match="no window named 'gti'"):
        attach_argv(cockpit, base, False, False, "gti")
