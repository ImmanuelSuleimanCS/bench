"""Backups and restores through SQLite's online backup API.

A plain file copy can miss committed transactions that are still in the -wal file. The backup API copies
the database as SQLite itself sees it, including those. Every backup is checked with
`PRAGMA integrity_check` before it counts, and is saved as one self contained file (journal mode DELETE).

File names: ~/.bench/backups/<stamp>.<area>.<kind>.db, where kind is daily, premigration, or prerestore.
Retention per area: the newest 14 daily, 5 premigration, and 5 prerestore backups.
"""
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

from bench import paths, stamps
from bench.db import core

KEEP = {"daily": 14, "premigration": 5, "prerestore": 5}
FILE_NAME = re.compile(r"(\d{8}\.\d{4})\.([a-z]+)\.([a-z]+)(?:\.(\d+))?\.db")
DAY_SECONDS = 24 * 3600


def backup(area: str, kind: str = "daily", now: datetime | None = None) -> Path:
    """Back up one area's database; returns the new file. Raises DatabaseError if the copy is not sound."""
    if kind not in KEEP:
        raise core.DatabaseError(f"unknown backup kind {kind!r}")
    source = core.db_path(area)
    if not source.exists():
        raise core.DatabaseError(f"{area}: no database at {source}; run `bench init`")
    folder = paths.backups_dir()
    folder.mkdir(mode=paths.PRIVATE, parents=True, exist_ok=True)
    dest = stamps.stamped_path(folder, f"{area}.{kind}", ".db", now)
    os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    src = core.connect(source)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode = DELETE")
    finally:
        dst.close()
        src.close()
    problem = integrity_problem(dest)
    if problem:
        dest.unlink()
        raise core.DatabaseError(f"{area}: the backup failed its integrity check ({problem}) and was removed")
    prune(area)
    return dest


def integrity_problem(path: Path) -> str | None:
    """None when PRAGMA integrity_check says ok; otherwise its first complaint. Opens the file read only."""
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        return str(exc)
    finally:
        conn.close()
    return None if rows == [("ok",)] else str(rows[0][0])


def backups(area: str, kind: str) -> list[Path]:
    """This area's backups of one kind, newest first."""
    found = []
    folder = paths.backups_dir()
    if not folder.exists():
        return []
    for entry in folder.iterdir():
        match = FILE_NAME.fullmatch(entry.name)
        if match and match.group(2) == area and match.group(3) == kind:
            found.append((match.group(1), int(match.group(4) or 1), entry))
    return [entry for _, _, entry in sorted(found, reverse=True)]


def prune(area: str) -> list[Path]:
    removed = []
    for kind, keep in KEEP.items():
        for old in backups(area, kind)[keep:]:
            old.unlink()
            removed.append(old)
    return removed


def daily_if_due(area: str) -> Path | None:
    """A daily backup when the area has a database and its newest daily backup is over 24 hours old."""
    if not core.db_path(area).exists():
        return None
    newest = backups(area, "daily")
    if newest and time.time() - newest[0].stat().st_mtime < DAY_SECONDS:
        return None
    return backup(area, "daily")


def holders(path: Path) -> list[int]:
    """Process ids, other than this one, that have the database or its -wal or -shm file open."""
    files = [str(p) for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")) if p.exists()]
    if not files:
        return []
    result = subprocess.run(["/usr/sbin/lsof", "-t", "--", *files], capture_output=True, text=True, timeout=30)
    return sorted({int(pid) for pid in result.stdout.split()} - {os.getpid()})


def restore(area: str, source: Path) -> list[str]:
    """Replace an area's database with a backup. Refuses while any other process has it open."""
    target = core.db_path(area)
    if not source.is_file():
        raise core.DatabaseError(f"no backup file at {source}")
    problem = integrity_problem(source)
    if problem:
        raise core.DatabaseError(f"{source.name} failed its integrity check ({problem}); nothing was changed")
    busy = holders(target)
    if busy:
        raise core.DatabaseError(
            f"{area}: the database is open in process {', '.join(map(str, busy))}. "
            "Quit the dashboard (q) and try again; nothing was changed")
    lines = []
    if target.exists():
        saved = backup(area, "prerestore")
        lines.append(f"{area}: current database backed up to {saved.name}")
    src = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    dst = core.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    problem = integrity_problem(target)
    if problem:
        raise core.DatabaseError(f"{area}: integrity check after restore failed ({problem})")
    lines.append(f"{area}: restored from {source.name}; integrity ok")
    return lines
