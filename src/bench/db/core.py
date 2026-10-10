"""SQLite basics shared by every area database.

One file per area in ~/.bench/data, owner only. Every connection turns on foreign keys, WAL, and a busy
timeout, so the dashboard and a command line call can both write without "database is locked" errors.
Timestamps are stored as UTC ISO 8601 text and shown in Central time.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bench import paths

# The area keys. Their display labels live only in ~/.bench/config.toml, outside the repository.
AREAS = ("client", "work", "personal", "etl")
CENTRAL = ZoneInfo("America/Chicago")


class DatabaseError(Exception):
    pass


def db_path(area: str) -> Path:
    if area not in AREAS:
        raise DatabaseError(f"unknown area {area!r}; the areas are {', '.join(AREAS)}")
    return paths.data_dir() / f"{area}.db"


def connect(path: Path) -> sqlite3.Connection:
    """Open a database file with Bench's settings, creating it owner only if it does not exist yet.

    Connections are in autocommit mode; group writes with `transaction()`.
    """
    path.parent.mkdir(mode=paths.PRIVATE, parents=True, exist_ok=True)
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    conn = sqlite3.connect(path, isolation_level=None, timeout=5)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """All of the block's writes, or none of them."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_central(stored: str) -> str:
    """A stored UTC timestamp as Central time, like 2026-10-10 14:32."""
    return datetime.fromisoformat(stored).astimezone(CENTRAL).strftime("%Y-%m-%d %H:%M")
