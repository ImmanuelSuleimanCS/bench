import os
import sqlite3
import subprocess
import sys
import time

import pytest

from bench.db import backup, core, migrate


def fill(area: str, rows: int) -> sqlite3.Connection:
    """Add `rows` rows in committed transactions, with automatic checkpoints off, and keep the connection open."""
    migrate.prepare(area)
    conn = core.connect(core.db_path(area))
    conn.execute("PRAGMA wal_autocheckpoint = 0")
    conn.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
    for start in range(0, rows, 100):
        with core.transaction(conn):
            conn.executemany("INSERT INTO t (n) VALUES (?)", [(n,) for n in range(start, start + 100)])
    return conn


def test_wal_backup_includes_commits_not_yet_checkpointed(bench_home):
    conn = fill("client", 1000)
    wal = core.db_path("client").with_name("client.db-wal")
    assert wal.stat().st_size > 0
    path = backup.backup("client")
    conn.close()
    assert path.stat().st_mode & 0o777 == 0o600
    assert not path.with_name(path.name + "-wal").exists()
    check = ("import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); "
             "print(c.execute('SELECT count(*) FROM t').fetchone()[0], c.execute('PRAGMA integrity_check').fetchone()[0])")
    out = subprocess.run([sys.executable, "-c", check, str(path)], capture_output=True, text=True, timeout=30).stdout
    assert out.split() == ["1000", "ok"]


def test_restore_brings_the_rows_back(bench_home):
    conn = fill("work", 1000)
    saved = backup.backup("work")
    conn.execute("DELETE FROM t WHERE n >= 500")
    conn.close()
    lines = backup.restore("work", saved)
    assert lines[0].startswith("work: current database backed up to ") and lines[0].endswith(".work.prerestore.db")
    assert lines[1] == f"work: restored from {saved.name}; integrity ok"
    conn = core.connect(core.db_path("work"))
    assert conn.execute("SELECT count(*), sum(n) FROM t").fetchone() == (1000, sum(range(1000)))
    conn.close()
    prerestore = backup.backups("work", "prerestore")[0]
    other = sqlite3.connect(prerestore)
    assert other.execute("SELECT count(*) FROM t").fetchone() == (500,)
    other.close()


def test_restore_refuses_while_another_process_has_it_open(bench_home):
    conn = fill("personal", 100)
    saved = backup.backup("personal")
    conn.close()
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sqlite3, sys, time; c = sqlite3.connect(sys.argv[1]); "
         "c.execute('SELECT count(*) FROM t').fetchone(); print('ready', flush=True); time.sleep(30)",
         str(core.db_path("personal"))],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ready"
        with pytest.raises(core.DatabaseError, match=f"open in process {holder.pid}"):
            backup.restore("personal", saved)
    finally:
        holder.kill()
        holder.wait()
    assert backup.backups("personal", "prerestore") == []


def test_this_process_does_not_count_as_a_holder(bench_home):
    conn = fill("etl", 100)
    assert backup.holders(core.db_path("etl")) == []
    conn.close()


def test_a_damaged_backup_is_never_restored(bench_home, tmp_path):
    migrate.prepare("client")
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"SQLite format 3\x00" + b"\x01" * 4000)
    with pytest.raises(core.DatabaseError, match="failed its integrity check"):
        backup.restore("client", junk)


def test_retention_keeps_14_daily_and_5_premigration(bench_home):
    folder = bench_home / "backups"
    folder.mkdir(parents=True)
    for day in range(1, 21):
        (folder / f"202610{day:02d}.0900.client.daily.db").write_text("")
    for day in range(1, 9):
        (folder / f"202610{day:02d}.0900.client.premigration.db").write_text("")
    (folder / "20261001.0900.work.daily.db").write_text("")
    removed = backup.prune("client")
    assert len(removed) == 6 + 3
    daily = backup.backups("client", "daily")
    assert [p.name[:8] for p in daily] == [f"202610{d:02d}" for d in range(20, 6, -1)]
    assert len(backup.backups("client", "premigration")) == 5
    assert (folder / "20261001.0900.work.daily.db").exists()


def test_same_minute_backups_sort_by_counter(bench_home):
    folder = bench_home / "backups"
    folder.mkdir(parents=True)
    for name in ("20261010.0900.client.daily.db", "20261010.0900.client.daily.2.db", "20261010.0900.client.daily.3.db"):
        (folder / name).write_text("")
    assert [p.name for p in backup.backups("client", "daily")][0] == "20261010.0900.client.daily.3.db"


def test_daily_backup_only_when_due(bench_home):
    assert backup.daily_if_due("client") is None
    migrate.prepare("client")
    first = backup.daily_if_due("client")
    assert first is not None
    assert backup.daily_if_due("client") is None
    old = time.time() - 25 * 3600
    os.utime(first, (old, old))
    assert backup.daily_if_due("client") is not None
