from importlib import resources
from pathlib import Path

import pytest

from bench.config import ConfigError, load
from tests.conftest import write_config


def test_example_config_loads(tmp_path):
    example = tmp_path / "config.toml"
    example.write_text((resources.files("bench") / "data" / "config.example.toml").read_text())
    cfg = load(example)
    assert [w.name for w in cfg.cockpit.windows] == ["agents", "server", "database", "git", "dashboard"]
    agents = cfg.cockpit.windows[0]
    assert {p.position: p.role for p in agents.panes} == {
        "right": "Builder", "left-top": "Terminal One", "left-bottom": "Terminal Zero"}
    builder = next(p for p in agents.panes if p.role == "Builder")
    assert builder.command[0] == "claude"
    assert builder.command[2] == str(Path("~/Projects/client-notes").expanduser())
    assert cfg.caffeinate.flags == "-di"
    assert cfg.areas["work"] == "Work"


def test_unknown_key_names_key_and_file(tmp_path):
    path = write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path)
    path.write_text(path.read_text().replace('timezone = "America/Chicago"', 'timezone = "America/Chicago"\ncolour = "blue"'))
    with pytest.raises(ConfigError) as err:
        load(path)
    assert "colour" in str(err.value) and str(path) in str(err.value)


def test_command_must_be_a_list(tmp_path):
    path = write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path, command='"claude --add-dir x"')
    with pytest.raises(ConfigError, match="must be a list of strings"):
        load(path)


def test_layout_needs_all_three_positions(tmp_path):
    path = write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path)
    path.write_text(path.read_text().replace('position = "left-bottom"', 'position = "left-top"'))
    with pytest.raises(ConfigError, match="needs exactly one pane"):
        load(path)


def test_bad_caffeinate_flags(tmp_path):
    path = write_config(tmp_path / "config.toml", [tmp_path], tmp_path, tmp_path)
    path.write_text(path.read_text() + '\n[caffeinate]\nflags = "-dz"\n')
    with pytest.raises(ConfigError, match="flags"):
        load(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="bench init"):
        load(tmp_path / "nope.toml")
