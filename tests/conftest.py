import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def bench_home(tmp_path, monkeypatch):
    """A throwaway BENCH_HOME so tests never touch ~/.bench."""
    home = tmp_path / "bench_home"
    monkeypatch.setenv("BENCH_HOME", str(home))
    return home


@pytest.fixture
def short_tmp():
    """A short temporary folder under /tmp. tmux socket paths must stay under about 100 characters."""
    folder = Path(tempfile.mkdtemp(prefix="bt", dir="/tmp"))
    yield folder
    for root, dirs, files in os.walk(folder, topdown=False):
        for name in files:
            Path(root, name).unlink(missing_ok=True)
        for name in dirs:
            Path(root, name).rmdir()
    folder.rmdir()


def write_config(path: Path, roots: list[Path], agents_folder: Path, shell_folder: Path,
                 command: str = '["claude", "--add-dir", "/tmp"]', socket: Path | None = None) -> Path:
    socket = socket or path.parent / "state" / "tmux" / "bench.sock"
    text = f'''
timezone = "America/Chicago"

[cockpit]
socket = "{socket}"
session = "bench"
allowed_roots = [{", ".join(f'"{r}"' for r in roots)}]

[[cockpit.window]]
name = "agents"
layout = "builder-right"

[[cockpit.pane]]
window = "agents"
role = "Builder"
position = "right"
folder = "{agents_folder}"
command = {command}

[[cockpit.pane]]
window = "agents"
role = "Terminal One"
position = "left-top"
folder = "{agents_folder}"
command = {command}

[[cockpit.pane]]
window = "agents"
role = "Terminal Zero"
position = "left-bottom"
folder = "{agents_folder}"
command = {command}

[[cockpit.window]]
name = "dashboard"
folder = "{shell_folder}"
'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
