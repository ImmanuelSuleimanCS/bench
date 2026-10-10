"""The caffeinate guard, proven in the Stage B spike (tests 1 to 9) and moved into Bench.

How it works:
- `on` starts a guard process in its own session, so closing the terminal does not stop it.
- The guard runs `caffeinate <flags> -w <guard pid>`, so caffeinate exits if the guard dies for any reason.
- The guard enforces the time limit itself and checks the battery every poll interval. On battery below
  the floor, or after several unreadable battery checks in a row, it stops caffeinate.
- `off` writes a stop request containing the run's nonce; the guard sees it and stops. No process id is
  signalled on the normal path.
- `kill` is the last resort: it signals the guard only after checking that the recorded process still has
  the same start time and command. That check and the signal are not atomic, which is why it is a fallback.
- A lock file serializes `on`, `off`, and `kill`, so two starts at once cannot create two guards.

Fix from the spike (test 9): the guard checks the battery once before starting caffeinate. If the Mac is
already on battery below the floor, caffeinate never starts, and `on` reports "Did not start: <reason>"
instead of the spike's misleading "did not confirm". `on` accepts any state carrying its own nonce as the
acknowledgement, running or not.
"""
import fcntl
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from bench import paths
from bench.caffeinate import power
from bench.config import CaffeinateConfig

MODULE = "bench.caffeinate.guard"


def _files() -> dict[str, Path]:
    d = paths.caffeinate_dir()
    return {
        "dir": d,
        "state": d / "state.json",
        "lock": d / "caffeinate.lock",
        "stop": d / "caffeinate.stop",
        "log": d / "guard.log",
    }


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    with open(_files()["log"], "a") as f:
        f.write(f"{now()} pid {os.getpid()}: {msg}\n")


def ps_field(pid, field: str) -> str:
    if not pid:
        return ""
    r = subprocess.run(["ps", "-p", str(pid), "-o", f"{field}="], capture_output=True, text=True)
    return r.stdout.strip()


def read_state() -> dict | None:
    try:
        return json.loads(_files()["state"].read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_state(data: dict) -> None:
    state = _files()["state"]
    tmp = state.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(state)


@contextmanager
def state_lock():
    f = _files()
    f["dir"].mkdir(mode=paths.PRIVATE, parents=True, exist_ok=True)
    with open(f["lock"], "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def verified_guard() -> tuple[dict | None, str]:
    """(state, verdict); verdict is running, stopped, stale, or none."""
    s = read_state()
    if s is None:
        return None, "none"
    if not s.get("running"):
        return s, "stopped"
    pid = s.get("guard_pid")
    if ps_field(pid, "lstart") == s.get("guard_lstart") and MODULE in ps_field(pid, "command"):
        return s, "running"
    return s, "stale"


def parse_duration(text: str | None) -> int:
    """Seconds from forms like 90m, 4h, 2h30m, 45s. Empty, None, or 0 means no time limit."""
    if not text or text == "0":
        return 0
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text.strip().lower())
    if not match or not any(match.groups()):
        raise ValueError(f"cannot read duration {text!r}; use forms like 90m, 4h, 2h30m")
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def caffeinate_flags(cfg: CaffeinateConfig) -> str:
    flags = cfg.flags
    if cfg.prevent_system_sleep_on_ac and "s" not in flags:
        flags += "s"
    return flags


def _stop_requested(nonce: str) -> bool:
    try:
        return _files()["stop"].read_text().strip() == nonce
    except FileNotFoundError:
        return False


def _notify(text: str) -> None:
    subprocess.run(
        ["osascript", "-e", f'display notification "{text}" with title "Bench caffeinate stopped"'],
        capture_output=True,
    )


def run_guard(nonce: str, seconds: int, settings: dict) -> None:
    """Body of the detached guard process."""
    me = os.getpid()
    reason = {"value": None}

    def on_term(signum, frame):
        reason["value"] = "stopped by the kill command"

    signal.signal(signal.SIGTERM, on_term)
    if _stop_requested(nonce):
        log("stop requested before start; exiting without starting caffeinate")
        return
    # First battery check happens before caffeinate starts, so a low battery means it never starts.
    fails = 0
    reading = power.read()
    if reading is None:
        fails = 1
        log("battery reading failed (1 in a row)")
    elif reading[0] == "Battery" and reading[1] < settings["floor"]:
        why = f"battery guard: {reading[1]}% on battery, floor is {settings['floor']}%"
        write_state({"running": False, "nonce": nonce, "started": now(), "stopped": now(), "reason": why, "seconds": seconds})
        log(f"did not start: {why}")
        if settings["notify"]:
            _notify(why)
        return
    args = ["caffeinate", settings["flags"], "-w", str(me)]
    caf = subprocess.Popen(args)
    deadline = time.monotonic() + seconds if seconds > 0 else None
    write_state({
        "running": True,
        "nonce": nonce,
        "guard_pid": me,
        "guard_lstart": ps_field(me, "lstart"),
        "caffeinate_pid": caf.pid,
        "caffeinate_args": args,
        "started": now(),
        "seconds": seconds,
    })
    log(f"started caffeinate pid {caf.pid} with {' '.join(args)}")
    next_check = time.monotonic() + settings["poll"]
    while reason["value"] is None:
        if _stop_requested(nonce):
            reason["value"] = "stopped by the off command"
            break
        if caf.poll() is not None:
            reason["value"] = "caffeinate exited unexpectedly"
            break
        if deadline is not None and time.monotonic() >= deadline:
            reason["value"] = "time limit reached"
            break
        if time.monotonic() >= next_check:
            next_check = time.monotonic() + settings["poll"]
            reading = power.read()
            if reading is None:
                fails += 1
                log(f"battery reading failed ({fails} in a row)")
                if fails >= settings["max_fails"]:
                    reason["value"] = f"battery status unavailable {fails} times in a row"
                    break
            else:
                fails = 0
                source, pct = reading
                if source == "Battery" and pct < settings["floor"]:
                    reason["value"] = f"battery guard: {pct}% on battery, floor is {settings['floor']}%"
                    break
        time.sleep(1)
    if caf.poll() is None:
        caf.terminate()
        try:
            caf.wait(timeout=5)
        except subprocess.TimeoutExpired:
            caf.kill()
            caf.wait()
    s = read_state() or {}
    s.update(running=False, reason=reason["value"], stopped=now())
    write_state(s)
    _files()["stop"].unlink(missing_ok=True)
    log(f"stopped: {reason['value']}")
    if settings["notify"] and reason["value"] != "stopped by the off command":
        _notify(reason["value"])


def turn_on(cfg: CaffeinateConfig, seconds: int) -> list[str]:
    files = _files()
    with state_lock():
        s, verdict = verified_guard()
        if verdict == "running":
            return [f"Already on since {s['started']} (guard pid {s['guard_pid']}). Nothing started."]
        if verdict == "stale":
            return ["The previous state is stale. Run `bench caffeinate off` first; nothing was started."]
        nonce = secrets.token_hex(8)
        files["stop"].unlink(missing_ok=True)
        settings = {
            "flags": caffeinate_flags(cfg),
            "floor": cfg.battery_floor_percent,
            "poll": cfg.poll_seconds,
            "max_fails": cfg.max_failed_readings,
            "notify": cfg.notify_on_stop,
        }
        with open(files["log"], "a") as logf:
            subprocess.Popen(
                [sys.executable, "-m", MODULE, nonce, str(seconds), json.dumps(settings)],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=logf,
                stderr=logf,
                close_fds=True,
            )
        for _ in range(50):
            s = read_state()
            if s and s.get("nonce") == nonce:
                break
            time.sleep(0.1)
        else:
            files["stop"].write_text(nonce)
            return ["The guard did not start within 5 seconds. A stop request was left for it; run `bench caffeinate status`."]
        if not s.get("running"):
            return [f"Did not start: {s.get('reason')}"]
    return status_lines()


def turn_off() -> list[str]:
    with state_lock():
        s, verdict = verified_guard()
        if verdict == "none":
            return ["Not running (no state file)."]
        if verdict == "stopped":
            return [f"Not running. Last stop {s.get('stopped')}: {s.get('reason')}"]
        if verdict == "stale":
            guard_alive = MODULE in ps_field(s.get("guard_pid"), "command")
            caf_alive = "caffeinate" in ps_field(s.get("caffeinate_pid"), "command")
            if not guard_alive and not caf_alive:
                s.update(running=False, reason="stale state cleared: guard and caffeinate were already gone", stopped=now())
                write_state(s)
                return ["Stale state cleared. The recorded guard and caffeinate were already gone. Nothing was signalled."]
            return ["The state file does not match the running process. Nothing was signalled or changed.",
                    "Run `bench caffeinate status` and paste it."]
        _files()["stop"].write_text(s["nonce"])
        for _ in range(100):
            if verified_guard()[1] != "running":
                break
            time.sleep(0.1)
        else:
            return ["The guard did not stop within 10 seconds. It is still running; `bench caffeinate kill` is the fallback."]
    return status_lines()


def kill() -> list[str]:
    with state_lock():
        s, verdict = verified_guard()
        if verdict != "running":
            return [f"Refusing: verdict is {verdict}, so the recorded process cannot be confirmed as the guard."]
        os.kill(s["guard_pid"], signal.SIGTERM)
        message = f"Sent SIGTERM to guard pid {s['guard_pid']} (started {s['guard_lstart']})."
    time.sleep(2)
    return [message, *status_lines()]


def status_lines() -> list[str]:
    s, verdict = verified_guard()
    lines = [f"caffeinate: {'ON' if verdict == 'running' else 'off'} ({verdict})"]
    if s:
        if verdict == "running":
            lines.append(f"  since {s.get('started')}, time limit {_human(s.get('seconds', 0))}")
            lines.append(f"  guard pid {s.get('guard_pid')}, caffeinate pid {s.get('caffeinate_pid')} ({' '.join(s.get('caffeinate_args', []))})")
        else:
            lines.append(f"  last run {s.get('started')} to {s.get('stopped')}: {s.get('reason')}")
    reading = power.read()
    lines.append(f"  power: {reading[0]} {reading[1]}%" if reading else "  power: unreadable")
    return lines


def _human(seconds: int) -> str:
    if not seconds:
        return "none"
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m" if m else f"{s}s"


if __name__ == "__main__":
    run_guard(sys.argv[1], int(sys.argv[2]), json.loads(sys.argv[3]))
