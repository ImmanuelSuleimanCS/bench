from bench import cli
from bench.db import backup, core, migrate


def test_init_creates_four_owner_only_databases(bench_home, capsys):
    assert cli.main(["init"]) == 0
    for area in core.AREAS:
        path = core.db_path(area)
        assert path.stat().st_mode & 0o777 == 0o600
        conn = core.connect(path)
        assert migrate.versions(conn) == {"common": 2}
        conn.close()
    assert "database client: " in capsys.readouterr().out
    assert cli.main(["init"]) == 0
    assert capsys.readouterr().out.count("already there") == 4


def test_backup_all_then_restore_one(bench_home, monkeypatch, capsys):
    cli.main(["init"])
    assert cli.main(["backup"]) == 0
    out = capsys.readouterr().out
    assert out.count("(integrity ok)") == 4
    saved = backup.backups("client", "daily")[0]
    monkeypatch.setattr("builtins.input", lambda prompt: "nope")
    assert cli.main(["restore", "client", str(saved)]) == 1
    assert "Nothing was changed" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda prompt: "restore")
    assert cli.main(["restore", "client", str(saved)]) == 0
    assert "integrity ok" in capsys.readouterr().out


def test_daily_backups_once_a_day(bench_home):
    cli.main(["init"])
    assert len(cli.daily_backups()) == 4
    assert cli.daily_backups() == []
    assert len(backup.backups("work", "daily")) == 1


def test_backup_without_init_says_so(bench_home, capsys):
    assert cli.main(["backup", "work"]) == 1
    assert "no database yet" in capsys.readouterr().out
