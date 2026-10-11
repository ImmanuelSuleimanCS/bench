"""Compare the cockpit described in config.toml with what tmux is running, and fix the difference.

Existing panes are never recreated. Only missing windows and missing panes are created.
"""
import shlex
from dataclasses import dataclass

from bench.cockpit.tmux import PaneState, Tmux
from bench.config import CockpitConfig, PaneSpec, WindowSpec

# Commands that mean "only a shell is running in this pane".
SHELLS = {"zsh", "-zsh", "bash", "-bash", "sh", "login", "arch"}

# Order in which panes of a layout are created, and how to split for each one:
# a list of (existing position to split from, tmux split flags), tried in order.
CREATE_ORDER = {
    "builder-right": ("left-top", "right", "left-bottom"),
    "grid": ("top-left", "top-right", "bottom-left", "bottom-right"),
    "single": ("full",),
}
SPLIT_FROM = {
    # builder-right
    "right": [("left-top", ["-h"]), ("left-bottom", ["-h"])],
    "left-top": [("left-bottom", ["-v", "-b"]), ("right", ["-h", "-b"])],
    "left-bottom": [("left-top", ["-v"]), ("right", ["-h", "-b"])],
    # grid: a new grid splits into left and right halves, then each half into top and bottom.
    # A lost corner is split from the pane below or above it first, so it stays in its own column.
    "top-left": [("bottom-left", ["-v", "-b"]), ("top-right", ["-h", "-b"])],
    "top-right": [("bottom-right", ["-v", "-b"]), ("top-left", ["-h"])],
    "bottom-left": [("top-left", ["-v"]), ("bottom-right", ["-h", "-b"])],
    "bottom-right": [("top-right", ["-v"]), ("bottom-left", ["-h"])],
}


def shell_command(command: list[str] | None) -> str | None:
    """The tmux shell command for a pane: run `command` in a native login shell, then stay in a shell.

    Interactive (-i) so ~/.zshrc is read and the pane has the same PATH as a normal Terminal window.
    """
    if not command:
        return None
    inner = f"{shlex.join(command)}; exec /bin/zsh -l"
    return f"arch -arm64 /bin/zsh -l -i -c {shlex.quote(inner)}"


@dataclass
class Action:
    kind: str  # "new-session", "new-window", or "split"
    pane: PaneSpec
    split_from: str | None = None  # role of the pane to split
    flags: tuple[str, ...] = ()

    def describe(self) -> str:
        what = " ".join(self.pane.command) if self.pane.command else "a shell"
        where = f"window {self.pane.window!r}, {self.pane.role} ({self.pane.position})"
        if self.kind == "new-session":
            how = "new session, first window"
        elif self.kind == "new-window":
            how = "new window"
        else:
            how = f"split from {self.split_from}"
        return f"{where}: {how}\n      folder:  {self.pane.folder}\n      runs:    {what}"


def make_plan(cockpit: CockpitConfig, session_exists: bool, panes: list[PaneState]) -> list[Action]:
    present = {(p.window, p.role) for p in panes}
    windows_present = {p.window for p in panes}
    actions: list[Action] = []
    first = not session_exists
    for window in cockpit.windows:
        by_position = {p.position: p for p in window.panes}
        if window.name not in windows_present:
            # Build the whole window: first pane, then splits in creation order.
            order = CREATE_ORDER[window.layout]
            head = by_position[order[0]]
            actions.append(Action("new-session" if first else "new-window", head))
            first = False
            available = {order[0]}
            for position in order[1:]:
                actions.append(_split_action(window, by_position[position], available))
                available.add(position)
            continue
        available = {p.position for p in window.panes if (window.name, p.role) in present}
        for position in CREATE_ORDER[window.layout]:
            spec = by_position[position]
            if (window.name, spec.role) in present:
                continue
            if not available:
                raise ValueError(f"window {window.name!r} has none of its configured panes; close it and build again")
            actions.append(_split_action(window, spec, available))
            available.add(position)
    return actions


def _split_action(window: WindowSpec, spec: PaneSpec, available: set[str]) -> Action:
    roles = {p.position: p.role for p in window.panes}
    for source, flags in SPLIT_FROM[spec.position]:
        if source in available:
            return Action("split", spec, roles[source], tuple(flags))
    raise ValueError(f"no pane to split from for {spec.role!r}")


def execute(tmux: Tmux, cockpit: CockpitConfig, actions: list[Action], panes: list[PaneState]) -> None:
    ids = {p.role: p.pane_id for p in panes if p.role}
    for action in actions:
        spec = action.pane
        command = shell_command(spec.command)
        tail = [command] if command else []
        if action.kind == "new-session":
            args = ["new-session", "-d", "-s", cockpit.session, "-n", spec.window, "-c", str(spec.folder),
                    "-x", "240", "-y", "60", "-P", "-F", "#{pane_id}", *tail]
        elif action.kind == "new-window":
            args = ["new-window", "-d", "-t", f"={cockpit.session}:", "-n", spec.window, "-c", str(spec.folder),
                    "-P", "-F", "#{pane_id}", *tail]
        else:
            args = ["split-window", *action.flags, "-d", "-t", ids[action.split_from], "-c", str(spec.folder),
                    "-P", "-F", "#{pane_id}", *tail]
        pane_id = tmux.run(*args).stdout.strip()
        tmux.run("set-option", "-p", "-t", pane_id, "@role", spec.role)
        ids[spec.role] = pane_id
    if actions:
        tmux.run("select-window", "-t", f"={cockpit.session}:{cockpit.windows[0].name}", check=False)


def pane_status(spec: PaneSpec, state: PaneState | None) -> str:
    if state is None:
        return "missing"
    if state.dead:
        return f"dead (exit status {state.dead_status or '?'}); bench cockpit restart {shlex.quote(spec.role)}"
    if state.current_command in SHELLS:
        return "command exited, shell open" if spec.command else "shell"
    return f"running ({state.current_command})"


def status_lines(cockpit: CockpitConfig, panes: list[PaneState]) -> list[str]:
    by_key = {(p.window, p.role): p for p in panes}
    lines = []
    for window in cockpit.windows:
        for spec in window.panes:
            state = by_key.get((window.name, spec.role))
            lines.append(f"{window.name:<10} {spec.role:<14} {pane_status(spec, state)}")
    return lines


def is_busy(spec: PaneSpec, state: PaneState) -> bool:
    """True when a program other than a shell is running in the pane."""
    return not state.dead and state.current_command not in SHELLS

def attach_argv(cockpit: CockpitConfig, base: list[str], left: bool, left_exists: bool, window: str | None) -> list[str]:
    """The tmux command that attaches a Terminal window to the cockpit, always opening on a known window.

    The right monitor uses the main session and opens on the first window (agents). The left monitor uses
    the grouped left session and opens on the dashboard. `window` picks another window by name. A session
    remembers the window it was last on, so the window is selected every time, before attaching.
    """
    names = [w.name for w in cockpit.windows]
    if window is None:
        window = cockpit.dashboard_window if left else names[0]
    if window not in names:
        raise ValueError(f"no window named {window!r}; the windows are {', '.join(names)}")
    if left and not left_exists:
        return [*base, "new-session", "-t", f"={cockpit.session}", "-s", cockpit.left_session,
                ";", "select-window", "-t", f"={cockpit.left_session}:{window}"]
    target = cockpit.left_session if left else cockpit.session
    return [*base, "select-window", "-t", f"={target}:{window}", ";", "attach", "-t", f"={target}"]
