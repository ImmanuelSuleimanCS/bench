import sqlite3
from importlib import resources

import pytest

from bench.config import load
from bench.db import core, migrate


def connect(area):
    return core.connect(core.db_path(area))


def migrations_root(tmp_path, extra: dict[str, str]):
    """A copy of the real migrations plus extra files, for testing later migrations."""
    root = tmp_path / "migrations"
    (root / "common").mkdir(parents=True)
    real = resources.files("bench.db") / "migrations" / "common" / "0001_tasks.sql"
    (root / "common" / "0001_tasks.sql").write_text(real.read_text())
    for name, sql in extra.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(sql)
    return root


def test_area_keys_match_the_config(tmp_path):
    example = tmp_path / "config.toml"
    example.write_text((resources.files("bench") / "data" / "config.example.toml").read_text())
    assert set(load(example).areas) == set(core.AREAS)


def test_new_database_is_owner_only_and_migrated(bench_home):
    lines = migrate.prepare("client")
    path = core.db_path("client")
    assert path.stat().st_mode & 0o777 == 0o600
    assert lines == ["client: applied common/0001_tasks.sql, common/0002_no_delete.sql"]
    conn = connect("client")
    assert migrate.versions(conn) == {"common": 2}
    assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    conn.close()
    assert not (bench_home / "backups").exists() or not any((bench_home / "backups").iterdir())


def test_prepare_twice_does_nothing(bench_home):
    migrate.prepare("work")
    assert migrate.prepare("work") == []


def test_foreign_keys_and_no_cascading_delete(bench_home):
    migrate.prepare("personal")
    conn = connect("personal")
    now = core.utc_now()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO tasks (project_id, title, urgency, created_at, updated_at) VALUES (99, 'x', 2, ?, ?)",
                     (now, now))
    conn.execute("INSERT INTO tasks (title, urgency, created_at, updated_at) VALUES ('a task', 2, ?, ?)", (now, now))
    conn.execute("INSERT INTO task_notes (task_id, created_at, body) VALUES (1, ?, 'a note')", (now,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM tasks WHERE id = 1")
    assert conn.execute("SELECT count(*) FROM task_notes").fetchone() == (1,)
    conn.close()


def test_transaction_is_all_or_nothing(bench_home):
    migrate.prepare("etl")
    conn = connect("etl")
    with pytest.raises(sqlite3.IntegrityError):
        with core.transaction(conn):
            conn.execute("INSERT INTO settings (key, value) VALUES ('a', '1')")
            conn.execute("INSERT INTO settings (key, value) VALUES ('a', '2')")
    assert conn.execute("SELECT count(*) FROM settings").fetchone() == (0,)
    conn.close()


def test_backup_is_taken_before_a_migration(bench_home, tmp_path):
    migrate.prepare("client", migrations_root(tmp_path / "v1", {}))  # start from common 1 only
    conn = connect("client")
    conn.execute("INSERT INTO projects (name, created_at) VALUES ('kept', ?)", (core.utc_now(),))
    conn.close()
    root = migrations_root(tmp_path, {"common/0002_color.sql": "ALTER TABLE projects ADD COLUMN color TEXT NOT NULL DEFAULT '';"})
    lines = migrate.prepare("client", root)
    assert lines[0].startswith("client: backed up to ") and lines[0].endswith(".client.premigration.db before migrating")
    assert lines[1] == "client: applied common/0002_color.sql"
    saved = sqlite3.connect(next((bench_home / "backups").glob("*.client.premigration.db")))
    assert saved.execute("SELECT version FROM schema_version WHERE scope = 'common'").fetchone() == (1,)
    assert saved.execute("SELECT name FROM projects").fetchall() == [("kept",)]
    saved.close()
    conn = connect("client")
    assert migrate.versions(conn) == {"common": 2}
    assert conn.execute("SELECT color FROM projects").fetchall() == [("",)]
    conn.close()


def test_a_failed_migration_changes_nothing(bench_home, tmp_path):
    migrate.prepare("client", migrations_root(tmp_path / "v1", {}))  # start from common 1 only
    root = migrations_root(tmp_path, {"common/0002_bad.sql": "CREATE TABLE half (x INTEGER);\nTHIS IS NOT SQL;"})
    with pytest.raises(migrate.MigrationError, match="0002_bad.sql failed and was rolled back"):
        migrate.prepare("client", root)
    conn = connect("client")
    assert migrate.versions(conn) == {"common": 1}
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'half'").fetchone() is None
    conn.close()


def test_numbering_gaps_are_refused(tmp_path):
    root = migrations_root(tmp_path, {"common/0003_skip.sql": "SELECT 1;"})
    with pytest.raises(migrate.MigrationError, match="no gaps"):
        migrate.available("client", root)


def test_workout_scope_is_personal_only_with_its_own_counter(bench_home, tmp_path):
    root = migrations_root(tmp_path, {"workout/0001_workout.sql": "CREATE TABLE cycle_starts (id INTEGER PRIMARY KEY);"})
    assert [m.label() for m in migrate.available("client", root)] == ["common/0001_tasks.sql"]
    assert [m.label() for m in migrate.available("personal", root)] == ["common/0001_tasks.sql", "workout/0001_workout.sql"]
    migrate.prepare("personal", root)
    conn = connect("personal")
    assert migrate.versions(conn) == {"common": 1, "workout": 1}
    conn.close()


def test_timestamps_are_utc_and_shown_in_central():
    assert core.utc_now().endswith("+00:00")
    assert core.to_central("2026-10-10T19:32:00+00:00") == "2026-10-10 14:32"
    assert core.to_central("2026-12-01T19:32:00+00:00") == "2026-12-01 13:32"


def test_unknown_area_is_refused(bench_home):
    with pytest.raises(core.DatabaseError, match="unknown area"):
        core.db_path("lunch")
