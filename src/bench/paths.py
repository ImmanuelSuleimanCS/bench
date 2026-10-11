"""Where Bench keeps its data. Everything lives under one folder, ~/.bench by default.

Tests set BENCH_HOME to a temporary folder so they never touch the real one.
"""
import os
from pathlib import Path


def bench_home() -> Path:
    return Path(os.environ.get("BENCH_HOME", "~/.bench")).expanduser()


def config_file() -> Path:
    return bench_home() / "config.toml"


def denylist_file() -> Path:
    return bench_home() / "denylist.txt"


def state_dir() -> Path:
    return bench_home() / "state"


def tmux_dir() -> Path:
    return state_dir() / "tmux"


def caffeinate_dir() -> Path:
    return state_dir() / "caffeinate"


def data_dir() -> Path:
    return bench_home() / "data"


def backups_dir() -> Path:
    return bench_home() / "backups"


def exports_dir() -> Path:
    return bench_home() / "exports"


# Folder name and permission bits for everything `bench init` creates.
PRIVATE = 0o700
FOLDERS = ("state", "state/tmux", "state/caffeinate", "data", "backups", "exports", "logs")
