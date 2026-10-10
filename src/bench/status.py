"""What the dashboard's status bar shows: cockpit, caffeinate, memory pressure, and battery. No clock.

Every reading has a timeout. A reading that fails comes back as None, and the bar keeps showing the last
good value, dimmed. When the Mac is on battery below the caffeinate floor, only the battery is read and the
other checks pause, to save power. Nothing here writes anything.
"""
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime

from bench.caffeinate import guard, power
from bench.cockpit import plan
from bench.cockpit.tmux import Tmux, TmuxError
from bench.config import Config

PRESSURE = {"1": "normal", "2": "warning", "4": "critical"}
FIELDS = ("cockpit", "caffeinate", "memory", "power")


@dataclass
class Snapshot:
    cockpit: str | None = None
    caffeinate: str | None = None
    memory: str | None = None
    power: str | None = None
    paused: bool = False


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m" if minutes else "<1m"


def cockpit_text(cfg: Config) -> str | None:
    """'cockpit 3 of 3': agent panes (panes with a command, outside the dashboard window) running a program."""
    agents = [(w.name, p) for w in cfg.cockpit.windows if w.name != cfg.cockpit.dashboard_window
              for p in w.panes if p.command]
    try:
        tmux = Tmux(cfg.cockpit.tmux, cfg.cockpit.socket)
        if not tmux.has_session(cfg.cockpit.session):
            return "cockpit off"
        states = {(s.window, s.role): s for s in tmux.panes(cfg.cockpit.session)}
    except (TmuxError, OSError):
        return None
    running = sum(1 for window, spec in agents
                  if (state := states.get((window, spec.role))) and plan.is_busy(spec, state))
    return f"cockpit {running} of {len(agents)}"


def caffeinate_text(now: datetime | None = None) -> str | None:
    """'caffeinate on 1h12m' with ', 48m left' when there is a time limit."""
    try:
        state, verdict = guard.verified_guard()
    except (OSError, subprocess.SubprocessError):
        return None
    if verdict == "stale":
        return "caffeinate stale"
    if verdict != "running":
        return "caffeinate off"
    now = now or datetime.now()
    try:
        elapsed = (now - datetime.strptime(state["started"], "%Y-%m-%d %H:%M:%S")).total_seconds()
    except (KeyError, ValueError):
        return "caffeinate on"
    text = f"caffeinate on {duration(elapsed)}"
    limit = state.get("seconds") or 0
    if limit:
        text += f", {duration(limit - elapsed)} left"
    return text


def parse_pressure(text: str) -> str | None:
    level = PRESSURE.get(text.strip())
    return f"memory {level}" if level else None


def memory_text() -> str | None:
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_pressure(out)


def power_text(reading: tuple[str, int] | None) -> str | None:
    if reading is None:
        return None
    source, percent = reading
    return f"AC {percent}%" if source == "AC" else f"battery {percent}%"


def gather(cfg: Config) -> Snapshot:
    reading = power.read()
    if reading and reading[0] == "Battery" and reading[1] < cfg.caffeinate.battery_floor_percent:
        return Snapshot(power=power_text(reading), paused=True)
    return Snapshot(cockpit_text(cfg), caffeinate_text(), memory_text(), power_text(reading))


def merge(new: Snapshot, last: Snapshot) -> tuple[list[tuple[str, bool]], Snapshot]:
    """(parts to show as (text, dimmed), the updated last good values)."""
    good = replace(last, **{f: getattr(new, f) for f in FIELDS if getattr(new, f) is not None})
    parts = []
    for f in FIELDS:
        value = getattr(new, f)
        if value is not None:
            parts.append((value, False))
        else:
            parts.append((getattr(good, f) or f"{f} unknown", True))
    if new.paused:
        parts.append(("checks paused: battery below the caffeinate floor", True))
    return parts, good
