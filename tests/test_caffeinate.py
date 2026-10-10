"""The Stage B caffeinate tests, rebuilt against Bench's guard.

A fake `caffeinate` (a small shell script that waits for its -w process) and a fake `pmset` are put on
PATH, so these tests never change how the real Mac sleeps.
"""
import json
import os
import subprocess
import sys
import time

import pytest

from bench.caffeinate import guard
from bench.config import CaffeinateConfig

# Waits while its -w process exists and is not a zombie, like the real caffeinate.
# (In these tests pytest is the guard's parent, so a killed guard lingers as a zombie until reaped.)
FAKE_CAFFEINATE = """#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -w) W=$2; shift;; esac; shift; done
while state=$(ps -o stat= -p "$W" 2>/dev/null) && [[ "$state" != Z* ]]; do sleep 0.2; done
"""


def fake_pmset(source: str, percent: int) -> str:
    return f"""#!/bin/sh
printf "Now drawing from '{source}'\\n -InternalBattery-0 (id=1)\\t{percent}%%; discharging; present: true\\n"
"""


@pytest.fixture
def fakes(bench_home, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "caffeinate").write_text(FAKE_CAFFEINATE)
    (bin_dir / "caffeinate").chmod(0o755)
    pmset = bin_dir / "pmset"
    pmset.write_text(fake_pmset("AC Power", 80))
    pmset.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("BENCH_PMSET", str(pmset))
    yield pmset
    s, verdict = guard.verified_guard()
    if verdict == "running":
        guard.turn_off()


def cfg(**kw):
    return CaffeinateConfig(**{"poll_seconds": 1, "notify_on_stop": False, **kw})


def alive(pid) -> bool:
    """True when the process exists and is not a zombie."""
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def test_parse_duration():
    assert guard.parse_duration(None) == 0
    assert guard.parse_duration("90m") == 5400
    assert guard.parse_duration("4h") == 14400
    assert guard.parse_duration("2h30m") == 9000
    assert guard.parse_duration("45s") == 45
    with pytest.raises(ValueError):
        guard.parse_duration("soon")


def test_on_then_off(fakes):
    lines = guard.turn_on(cfg(), 0)
    assert lines[0].startswith("caffeinate: ON")
    s = guard.read_state()
    assert alive(s["guard_pid"]) and alive(s["caffeinate_pid"])
    assert guard.turn_on(cfg(), 0)[0].startswith("Already on")
    guard.turn_off()
    s2, verdict = guard.verified_guard()
    assert verdict == "stopped" and s2["reason"] == "stopped by the off command"
    time.sleep(0.5)
    assert not alive(s["caffeinate_pid"])


def test_hard_kill_leaves_nothing_and_off_clears_stale(fakes):
    guard.turn_on(cfg(), 0)
    s = guard.read_state()
    os.kill(s["guard_pid"], 9)
    time.sleep(1.5)
    assert not alive(s["caffeinate_pid"])
    assert guard.turn_off()[0].startswith("Stale state cleared")


def test_mismatched_identity_is_never_signalled(fakes):
    guard.turn_on(cfg(), 0)
    real = guard.read_state()
    forged = dict(real, guard_lstart="Mon Jan  1 00:00:00 2001")
    guard.write_state(forged)
    assert "Nothing was signalled" in guard.turn_off()[0]
    assert guard.kill()[0].startswith("Refusing")
    assert alive(real["guard_pid"])
    guard.write_state(real)
    guard.turn_off()
    time.sleep(0.5)
    assert not alive(real["guard_pid"]) and not alive(real["caffeinate_pid"])


def test_concurrent_starts_make_one_guard(fakes):
    code = "from bench.caffeinate import guard; from bench.config import CaffeinateConfig as C; print(guard.turn_on(C(poll_seconds=1, notify_on_stop=False), 0)[0])"
    procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True) for _ in range(2)]
    outs = [p.communicate(timeout=30)[0] for p in procs]
    assert sum("Already on" in o for o in outs) == 1
    assert guard.verified_guard()[1] == "running"


def test_time_limit(fakes):
    guard.turn_on(cfg(), 2)
    time.sleep(3.5)
    s, verdict = guard.verified_guard()
    assert verdict == "stopped" and s["reason"] == "time limit reached"


def test_unreadable_battery_stops_after_three(fakes):
    fakes.write_text("#!/bin/sh\necho something unexpected\n")
    guard.turn_on(cfg(), 0)
    time.sleep(4)
    s, verdict = guard.verified_guard()
    assert verdict == "stopped" and s["reason"] == "battery status unavailable 3 times in a row"


def test_low_battery_reports_immediate_stop(fakes):
    fakes.write_text(fake_pmset("Battery Power", 30))
    lines = guard.turn_on(cfg(battery_floor_percent=40), 0)
    assert lines == ["Did not start: battery guard: 30% on battery, floor is 40%"]
    assert guard.verified_guard()[1] == "stopped"


def test_battery_dropping_later_stops_it(fakes):
    guard.turn_on(cfg(battery_floor_percent=40), 0)
    assert guard.verified_guard()[1] == "running"
    fakes.write_text(fake_pmset("Battery Power", 30))
    time.sleep(2.5)
    s, verdict = guard.verified_guard()
    assert verdict == "stopped" and s["reason"] == "battery guard: 30% on battery, floor is 40%"


def test_settings_reach_the_guard(fakes):
    guard.turn_on(cfg(prevent_system_sleep_on_ac=True), 0)
    s = guard.read_state()
    assert s["caffeinate_args"][:2] == ["caffeinate", "-dis"]
    assert json.loads(json.dumps(s))["running"] is True
