import pytest

from bench.config import ConfigError, load
from bench.sensitive import findings
from tests.conftest import write_config


def test_long_numbers_emails_and_hostname_patterns():
    text = "call 5551234567 or mail someone.else+x@example.co.uk about box12.corp.local"
    assert findings(text, [r"\.corp\.local\b"]) == [
        "9 or 10 digit number: 5551234567",
        "email address: someone.else+x@example.co.uk",
        "hostname: .corp.local",
    ]
    assert findings("ID1234567890 and tin 123456789 and ssn 123-45-6789", []) == [
        "9 or 10 digit number: 1234567890",
        "9 or 10 digit number: 123456789",
        "dashed number: 123-45-6789",
    ]
    assert findings("ticket 55512345 and 55512345678 and 2026-10-10", []) == []
    assert findings("same 5551234567 twice 5551234567", []) == ["9 or 10 digit number: 5551234567"]
    assert findings("plain short description", [r"\.corp\.local\b"]) == []


def config_with(tmp_path, tasks_table: str):
    path = write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path)
    path.write_text(path.read_text() + "\n" + tasks_table)
    return path


def test_tasks_settings_default_and_checked(tmp_path):
    cfg = load(write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path))
    assert (cfg.tasks.checked_areas, cfg.tasks.hostname_patterns, cfg.tasks.archive_after_days) == (["work"], [], 14)
    cfg = load(config_with(tmp_path, '[tasks]\nchecked_areas = ["work", "client"]\nhostname_patterns = ["\\\\.lan\\\\b"]\n'))
    assert cfg.tasks.checked_areas == ["work", "client"]
    assert cfg.tasks.hostname_patterns == [r"\.lan\b"]
    for bad, message in (('checked_areas = ["job"]', "may only list"),
                         ('hostname_patterns = ["(unclosed"]', "not a valid pattern"),
                         ("archive_after_days = 0", "at least 1"),
                         ("colour = 1", "unknown key")):
        with pytest.raises(ConfigError, match=message):
            load(config_with(tmp_path, f"[tasks]\n{bad}\n"))
