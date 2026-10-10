"""Scoped migrations.

Files live in bench/db/migrations/<scope>/NNNN_name.sql and are numbered 0001, 0002, ... within their
scope. Each scope has its own version counter in the schema_version table, so a later common/0002 can never
be confused with workout/0001. `common` applies to every area; `workout` applies to the personal area only
(it arrives in Stage F).

Each file runs in one transaction together with its version update, so it applies completely or not at all.
Before any migration runs on a database that already has content, `prepare` takes a backup.
"""
import re
import sqlite3
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable

from bench.db import backup, core

SCOPES = {"common": core.AREAS, "workout": ("personal",)}
FILE_NAME = re.compile(r"(\d{4})_[a-z0-9_]+\.sql")
VERSION_TABLE = "CREATE TABLE IF NOT EXISTS schema_version (scope TEXT PRIMARY KEY, version INTEGER NOT NULL);"


class MigrationError(core.DatabaseError):
    pass


@dataclass(frozen=True)
class Migration:
    scope: str
    number: int
    name: str
    sql: str

    def label(self) -> str:
        return f"{self.scope}/{self.name}"


def default_root() -> Traversable:
    return resources.files("bench.db") / "migrations"


def available(area: str, root: Traversable | None = None) -> list[Migration]:
    """Every migration that applies to this area, in order. Numbers must run 1, 2, 3 within a scope."""
    root = root or default_root()
    found = []
    for scope, areas in SCOPES.items():
        folder = root / scope
        if area not in areas or not folder.is_dir():
            continue
        scoped = []
        for entry in folder.iterdir():
            match = FILE_NAME.fullmatch(entry.name)
            if match:
                scoped.append(Migration(scope, int(match.group(1)), entry.name, entry.read_text()))
        scoped.sort(key=lambda m: m.number)
        if [m.number for m in scoped] != list(range(1, len(scoped) + 1)):
            raise MigrationError(f"migrations/{scope}: files must be numbered 0001, 0002, ... with no gaps")
        found.extend(scoped)
    return found


def versions(conn: sqlite3.Connection) -> dict[str, int]:
    """{scope: version}; empty for a database that has never been migrated."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'").fetchone()
    if not exists:
        return {}
    return dict(conn.execute("SELECT scope, version FROM schema_version").fetchall())


def pending(conn: sqlite3.Connection, area: str, root: Traversable | None = None) -> list[Migration]:
    current = versions(conn)
    return [m for m in available(area, root) if m.number > current.get(m.scope, 0)]


def apply(conn: sqlite3.Connection, migrations: list[Migration]) -> None:
    for m in migrations:
        script = (
            f"BEGIN IMMEDIATE;\n{VERSION_TABLE}\n{m.sql}\n"
            f"INSERT INTO schema_version (scope, version) VALUES ('{m.scope}', {m.number})\n"
            f"  ON CONFLICT (scope) DO UPDATE SET version = excluded.version;\nCOMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"{m.label()} failed and was rolled back: {exc}") from exc


def prepare(area: str, root: Traversable | None = None) -> list[str]:
    """Create the area's database if it is missing and apply pending migrations, backing up first."""
    path = core.db_path(area)
    had_content = path.exists() and path.stat().st_size > 0
    conn = core.connect(path)
    try:
        todo = pending(conn, area, root)
        if not todo:
            return []
        lines = []
        if had_content:
            saved = backup.backup(area, "premigration")
            lines.append(f"{area}: backed up to {saved.name} before migrating")
        apply(conn, todo)
        lines.append(f"{area}: applied {', '.join(m.label() for m in todo)}")
        return lines
    finally:
        conn.close()
