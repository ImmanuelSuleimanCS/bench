from datetime import datetime

from bench import status
from bench.cockpit.tmux import PaneState
from bench.config import load
from tests.conftest import write_config

NOW = datetime(2026, 10, 10, 15, 0, 0)


def running(started: str, seconds: int):
    return lambda: ({"started": started, "seconds": seconds}, "running")


def test_duration():
    assert status.duration(30) == "<1m"
    assert status.duration(12 * 60) == "12m"
    assert status.duration(72 * 60) == "1h12m"
    assert status.duration(-5) == "<1m"


def test_caffeinate_with_and_without_a_limit(monkeypatch):
    monkeypatch.setattr(status.guard, "verified_guard", running("2026-10-10 13:48:00", 7200))
    assert status.caffeinate_text(NOW) == "caffeinate on 1h12m, 48m left"
    monkeypatch.setattr(status.guard, "verified_guard", running("2026-10-10 13:48:00", 0))
    assert status.caffeinate_text(NOW) == "caffeinate on 1h12m"
    monkeypatch.setattr(status.guard, "verified_guard", lambda: (None, "none"))
    assert status.caffeinate_text(NOW) == "caffeinate off"
    monkeypatch.setattr(status.guard, "verified_guard", lambda: ({}, "stale"))
    assert status.caffeinate_text(NOW) == "caffeinate stale"


def test_memory_pressure_and_power():
    assert status.parse_pressure("1\n") == "memory normal"
    assert status.parse_pressure("2") == "memory warning"
    assert status.parse_pressure("4") == "memory critical"
    assert status.parse_pressure("") is None
    assert status.power_text(("AC", 80)) == "AC 80%"
    assert status.power_text(("Battery", 55)) == "battery 55%"
    assert status.power_text(None) is None


def test_a_failed_reading_shows_the_last_good_value_dimmed():
    parts, last = status.merge(status.Snapshot("cockpit 3 of 3", "caffeinate off", "memory normal", "AC 80%"),
                               status.Snapshot())
    assert parts == [("cockpit 3 of 3", False), ("caffeinate off", False), ("memory normal", False), ("AC 80%", False)]
    parts, last = status.merge(status.Snapshot(None, "caffeinate off", None, "AC 79%"), last)
    assert parts == [("cockpit 3 of 3", True), ("caffeinate off", False), ("memory normal", True), ("AC 79%", False)]
    parts, _ = status.merge(status.Snapshot(), status.Snapshot())
    assert parts[0] == ("cockpit unknown", True)


def test_low_battery_pauses_the_other_checks(tmp_path, monkeypatch):
    cfg = load(write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path))
    monkeypatch.setattr(status.power, "read", lambda: ("Battery", 30))
    called = []
    monkeypatch.setattr(status, "memory_text", lambda: called.append("memory"))
    snap = status.gather(cfg)
    assert snap.paused and snap.power == "battery 30%" and snap.cockpit is None
    assert called == []
    parts, _ = status.merge(snap, status.Snapshot())
    assert parts[-1] == ("checks paused: battery below the caffeinate floor", True)


def test_cockpit_counts_agent_panes_only(tmp_path, monkeypatch):
    path = write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path)
    path.write_text(path.read_text() + 'command = ["bench"]\n')
    cfg = load(path)
    states = [
        PaneState("agents", "%1", "Builder", "2.1.296", str(tmp_path), False, ""),
        PaneState("agents", "%2", "Terminal One", "2.1.296", str(tmp_path), False, ""),
        PaneState("agents", "%3", "Terminal Zero", "zsh", str(tmp_path), False, ""),
        PaneState("dashboard", "%4", "dashboard", "python3.12", str(tmp_path), False, ""),
    ]

    class FakeTmux:
        def __init__(self, *args):
            pass

        def has_session(self, name):
            return True

        def panes(self, session):
            return states

    monkeypatch.setattr(status, "Tmux", FakeTmux)
    assert status.cockpit_text(cfg) == "cockpit 2 of 3"


def test_cockpit_off_when_no_server(tmp_path):
    cfg = load(write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path))
    assert status.cockpit_text(cfg) == "cockpit off"
