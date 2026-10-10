from datetime import datetime, timezone

from bench.stamps import stamp, stamped_path


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_central_daylight_time():
    # 2026-10-10 07:30 UTC is 02:30 CDT (UTC-5).
    assert stamp(utc(2026, 10, 10, 7, 30)) == "20261010.0230"


def test_across_the_daylight_saving_change():
    # Clocks fall back at 02:00 CDT on Sunday 2026-11-01.
    assert stamp(utc(2026, 11, 1, 6, 30)) == "20261101.0130"  # 01:30 CDT
    assert stamp(utc(2026, 11, 1, 7, 30)) == "20261101.0130"  # 01:30 CST, the repeated hour
    assert stamp(utc(2026, 11, 1, 9, 0)) == "20261101.0300"   # 03:00 CST


def test_collision_counter(tmp_path):
    now = utc(2026, 10, 10, 7, 30)
    first = stamped_path(tmp_path, "note", ".md", now)
    first.write_text("x")
    second = stamped_path(tmp_path, "note", ".md", now)
    second.write_text("y")
    third = stamped_path(tmp_path, "note", ".md", now)
    assert first.name == "20261010.0230.note.md"
    assert second.name == "20261010.0230.note.2.md"
    assert third.name == "20261010.0230.note.3.md"
