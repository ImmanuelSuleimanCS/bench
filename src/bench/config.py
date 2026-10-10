"""Load and check ~/.bench/config.toml.

Every problem is reported with the file name and the key, so a typo points straight at the line to fix.
Folder checks (does it exist, is it allowed) are done later by bench.safety, because a missing project
folder should not stop `bench caffeinate` from working.
"""
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

POSITIONS = {
    "builder-right": ("left-top", "left-bottom", "right"),
    "single": ("full",),
}


class ConfigError(Exception):
    pass


@dataclass
class PaneSpec:
    window: str
    role: str
    position: str
    folder: Path
    command: list[str] | None


@dataclass
class WindowSpec:
    name: str
    layout: str
    panes: list[PaneSpec]


@dataclass
class CockpitConfig:
    socket: Path
    session: str
    left_session: str
    dashboard_window: str
    tmux: str
    allowed_roots: list[Path]
    windows: list[WindowSpec]


@dataclass
class CaffeinateConfig:
    flags: str = "-di"
    prevent_system_sleep_on_ac: bool = False
    battery_floor_percent: int = 40
    poll_seconds: int = 60
    max_failed_readings: int = 3
    notify_on_stop: bool = True


@dataclass
class Config:
    path: Path
    timezone: str
    areas: dict[str, str]
    cockpit: CockpitConfig
    caffeinate: CaffeinateConfig
    required_env: list[str] = field(default_factory=list)


def _expand(value: str) -> Path:
    return Path(value).expanduser()


def _check_keys(where: str, table: dict, allowed: set[str], required: set[str], src: Path) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"{src}: {where}: unknown key {unknown[0]!r}")
    missing = sorted(required - set(table))
    if missing:
        raise ConfigError(f"{src}: {where}: missing key {missing[0]!r}")


def _want(where: str, key: str, value, kind, src: Path):
    if kind is int and isinstance(value, bool):
        raise ConfigError(f"{src}: {where}: key {key!r} must be a whole number")
    if not isinstance(value, kind):
        names = {str: "text", int: "a whole number", bool: "true or false", list: "a list"}
        raise ConfigError(f"{src}: {where}: key {key!r} must be {names.get(kind, kind.__name__)}")
    return value


def _command(where: str, value, src: Path) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(
            f"{src}: {where}: key 'command' must be a list of strings, "
            'for example ["claude", "--add-dir", "~/Projects/client-notes"]'
        )
    return [str(Path(v).expanduser()) if v.startswith("~") else v for v in value]


def load(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"{path}: not found. Run `bench init` first.")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML: {exc}") from exc

    _check_keys("top level", data, {"timezone", "areas", "cockpit", "caffeinate", "project"}, {"cockpit"}, path)
    timezone = _want("top level", "timezone", data.get("timezone", "America/Chicago"), str, path)

    areas = data.get("areas", {})
    _check_keys("[areas]", areas, {"client", "work", "personal", "etl"}, set(), path)
    for key, value in areas.items():
        _want("[areas]", key, value, str, path)

    cockpit = _load_cockpit(data["cockpit"], path)

    caf_table = data.get("caffeinate", {})
    defaults = CaffeinateConfig()
    _check_keys("[caffeinate]", caf_table, set(vars(defaults)), set(), path)
    caf_values = {}
    for key, default in vars(defaults).items():
        caf_values[key] = _want("[caffeinate]", key, caf_table.get(key, default), type(default), path)
    caffeinate = CaffeinateConfig(**caf_values)
    if not caffeinate.flags.startswith("-") or not set(caffeinate.flags[1:]) <= set("dimsu"):
        raise ConfigError(f"{path}: [caffeinate]: key 'flags' may only combine d, i, m, s, u, like \"-di\"")
    if not 1 <= caffeinate.battery_floor_percent <= 100:
        raise ConfigError(f"{path}: [caffeinate]: key 'battery_floor_percent' must be between 1 and 100")
    if caffeinate.poll_seconds < 1 or caffeinate.max_failed_readings < 1:
        raise ConfigError(f"{path}: [caffeinate]: poll_seconds and max_failed_readings must be at least 1")

    project = data.get("project", {})
    _check_keys("[project]", project, {"required_env"}, set(), path)
    required_env = _want("[project]", "required_env", project.get("required_env", []), list, path)
    if not all(isinstance(name, str) and name.isidentifier() for name in required_env):
        raise ConfigError(f"{path}: [project]: key 'required_env' must list variable names only")

    return Config(path, timezone, dict(areas), cockpit, caffeinate, list(required_env))


def _load_cockpit(table: dict, path: Path) -> CockpitConfig:
    where = "[cockpit]"
    _check_keys(
        where,
        table,
        {"socket", "session", "left_session", "dashboard_window", "tmux", "allowed_roots", "window", "pane"},
        {"socket", "session", "allowed_roots", "window"},
        path,
    )
    socket = _expand(_want(where, "socket", table["socket"], str, path))
    session = _want(where, "session", table["session"], str, path)
    left_session = _want(where, "left_session", table.get("left_session", f"{session}-left"), str, path)
    dashboard_window = _want(where, "dashboard_window", table.get("dashboard_window", "dashboard"), str, path)
    tmux = _want(where, "tmux", table.get("tmux", "tmux"), str, path)
    roots = _want(where, "allowed_roots", table["allowed_roots"], list, path)
    if not roots or not all(isinstance(r, str) for r in roots):
        raise ConfigError(f"{path}: {where}: key 'allowed_roots' must be a list of folders")
    allowed_roots = [_expand(r) for r in roots]

    panes_by_window: dict[str, list[dict]] = {}
    for i, pane in enumerate(table.get("pane", []), start=1):
        pwhere = f"[[cockpit.pane]] number {i}"
        _check_keys(pwhere, pane, {"window", "role", "position", "folder", "command"}, {"window", "role", "position", "folder"}, path)
        for key in ("window", "role", "position", "folder"):
            _want(pwhere, key, pane[key], str, path)
        panes_by_window.setdefault(pane["window"], []).append(pane)

    windows: list[WindowSpec] = []
    seen_names: set[str] = set()
    seen_roles: set[str] = set()
    for i, win in enumerate(table["window"], start=1):
        wwhere = f"[[cockpit.window]] number {i}"
        _check_keys(wwhere, win, {"name", "layout", "folder", "command"}, {"name"}, path)
        name = _want(wwhere, "name", win["name"], str, path)
        if name in seen_names:
            raise ConfigError(f"{path}: {wwhere}: window name {name!r} is used twice")
        seen_names.add(name)
        layout = _want(wwhere, "layout", win.get("layout", "single"), str, path)
        if layout not in POSITIONS:
            raise ConfigError(f"{path}: {wwhere}: layout must be one of {sorted(POSITIONS)}")

        if layout == "single":
            if name in panes_by_window:
                raise ConfigError(f"{path}: {wwhere}: a single pane window takes 'folder' and 'command' itself, not [[cockpit.pane]] entries")
            if "folder" not in win:
                raise ConfigError(f"{path}: {wwhere}: missing key 'folder'")
            folder = _expand(_want(wwhere, "folder", win["folder"], str, path))
            command = _command(wwhere, win.get("command"), path)
            panes = [PaneSpec(name, name, "full", folder, command)]
        else:
            if "folder" in win or "command" in win:
                raise ConfigError(f"{path}: {wwhere}: layout {layout!r} takes its folders and commands from [[cockpit.pane]] entries")
            entries = panes_by_window.pop(name, [])
            got = sorted(p["position"] for p in entries)
            if got != sorted(POSITIONS[layout]):
                raise ConfigError(
                    f"{path}: {wwhere}: layout {layout!r} needs exactly one pane at each of "
                    f"{', '.join(POSITIONS[layout])}; found {', '.join(got) or 'none'}"
                )
            panes = []
            for p in entries:
                pwhere = f"[[cockpit.pane]] role {p['role']!r}"
                panes.append(PaneSpec(name, p["role"], p["position"], _expand(p["folder"]), _command(pwhere, p.get("command"), path)))
        for pane in panes:
            if pane.role in seen_roles:
                raise ConfigError(f"{path}: role {pane.role!r} is used twice")
            seen_roles.add(pane.role)
        windows.append(WindowSpec(name, layout, panes))

    if panes_by_window:
        stray = sorted(panes_by_window)[0]
        raise ConfigError(f"{path}: [[cockpit.pane]] entries name window {stray!r}, which has no [[cockpit.window]] with layout set")
    if dashboard_window not in seen_names:
        raise ConfigError(f"{path}: {where}: dashboard_window {dashboard_window!r} is not one of the windows")

    return CockpitConfig(socket, session, left_session, dashboard_window, tmux, allowed_roots, windows)
