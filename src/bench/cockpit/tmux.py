"""A thin wrapper around Bench's private tmux server. Every call has a timeout."""
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

FIELDS = (
    "#{window_name}",
    "#{pane_id}",
    "#{@role}",
    "#{pane_current_command}",
    "#{pane_current_path}",
    "#{pane_dead}",
    "#{pane_dead_status}",
)
SEP = "\t"


def default_conf() -> Path:
    return Path(str(resources.files("bench") / "data" / "bench.tmux.conf"))


@dataclass
class PaneState:
    window: str
    pane_id: str
    role: str
    current_command: str
    current_path: str
    dead: bool
    dead_status: str


class TmuxError(Exception):
    pass


class Tmux:
    def __init__(self, binary: str, socket: Path, conf: Path | None = None):
        self.binary = binary
        self.socket = socket
        self.conf = conf or default_conf()

    def base(self) -> list[str]:
        return [self.binary, "-S", str(self.socket), "-f", str(self.conf)]

    def run(self, *args: str, check: bool = True, timeout: float = 10) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run([*self.base(), *args], capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as exc:
            raise TmuxError(f"tmux not found: {self.binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TmuxError(f"tmux {' '.join(args)} did not answer within {timeout} seconds") from exc
        if check and result.returncode != 0:
            raise TmuxError(f"tmux {' '.join(args)} failed: {result.stderr.strip()}")
        return result

    def has_session(self, name: str) -> bool:
        if not self.socket.exists():
            return False
        return self.run("has-session", "-t", f"={name}", check=False).returncode == 0

    def panes(self, session: str) -> list[PaneState]:
        if not self.has_session(session):
            return []
        out = self.run("list-panes", "-s", "-t", f"={session}", "-F", SEP.join(FIELDS)).stdout
        states = []
        for line in out.splitlines():
            parts = line.split(SEP)
            if len(parts) != len(FIELDS):
                continue
            window, pane_id, role, command, path, dead, dead_status = parts
            states.append(PaneState(window, pane_id, role, command, path, dead == "1", dead_status))
        return states
