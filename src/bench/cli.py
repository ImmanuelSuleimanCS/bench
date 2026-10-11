"""The `bench` command line: the dashboard (no arguments), init, check, backup, restore, cockpit, caffeinate."""
import argparse
import os
import shutil
import subprocess
import sqlite3
import sys
from importlib import resources
from pathlib import Path

from bench import __version__, context_check, paths, safety
from bench.caffeinate import guard
from bench.cockpit import plan as cockpit_plan
from bench.cockpit.tmux import Tmux, TmuxError
from bench.config import Config, ConfigError, load
from bench.db import backup, core, migrate, report

RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"


def red(text: str) -> str:
    return f"{RED}{text}{RESET}" if sys.stdout.isatty() else text


def green(text: str) -> str:
    return f"{GREEN}{text}{RESET}" if sys.stdout.isatty() else text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench", description="Bench, a personal command center for the Mac. With no command, opens the dashboard.")
    parser.add_argument("--version", action="version", version=f"bench {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("init", help="create ~/.bench, a starting config.toml (never overwrites), and the area databases")
    sub.add_parser("check", help="check config, folders, tools, databases, and auto loaded Claude Code context")
    back = sub.add_parser("backup", help="back up the area databases now (all, or one area)")
    back.add_argument("area", nargs="?", choices=core.AREAS)
    rest = sub.add_parser("restore", help="replace one area's database with a backup (asks you to type restore)")
    rest.add_argument("area", choices=core.AREAS)
    rest.add_argument("file", type=Path)

    cockpit = sub.add_parser("cockpit", help="the tmux cockpit")
    csub = cockpit.add_subparsers(dest="action", required=True)
    csub.add_parser("build", help="create whatever is missing, after showing it and waiting for Enter")
    csub.add_parser("status", help="one line per pane")
    restart = csub.add_parser("restart", help="restart one pane by its role")
    restart.add_argument("role")
    attach = csub.add_parser("attach", help="attach this Terminal window to the cockpit")
    attach.add_argument("--left", action="store_true", help="the left monitor's view, opening on the dashboard")
    attach.add_argument("--window", help="open on this window instead, for example git")
    csub.add_parser("kill", help="stop the cockpit and everything running in it (asks you to type kill)")

    caf = sub.add_parser("caffeinate", help="keep the Mac awake")
    fsub = caf.add_subparsers(dest="action", required=True)
    on = fsub.add_parser("on", help="start, optionally with a time limit like 90m, 4h, 2h30m")
    on.add_argument("duration", nargs="?", default=None)
    fsub.add_parser("off", help="stop")
    fsub.add_parser("status", help="show state")
    fsub.add_parser("kill", help="last resort if off does not work")

    args = parser.parse_args(argv)
    try:
        if args.command is None:
            return cmd_dashboard()
        if args.command == "init":
            return cmd_init()
        if args.command == "check":
            return cmd_check()
        if args.command == "backup":
            return cmd_backup(args)
        if args.command == "restore":
            return cmd_restore(args)
        if args.command == "cockpit":
            return cmd_cockpit(args)
        if args.command == "caffeinate":
            return cmd_caffeinate(args)
    except ConfigError as exc:
        print(red(f"config problem: {exc}"))
        return 2
    except TmuxError as exc:
        print(red(f"tmux problem: {exc}"))
        return 2
    except (core.DatabaseError, sqlite3.Error) as exc:
        print(red(f"database problem: {exc}"))
        return 2
    except KeyboardInterrupt:
        print("\nCancelled. Nothing more was done.")
        return 130
    return 1


# ---------------------------------------------------------------- init

def cmd_init() -> int:
    home = paths.bench_home()
    home.mkdir(mode=paths.PRIVATE, parents=True, exist_ok=True)
    home.chmod(paths.PRIVATE)
    for name in paths.FOLDERS:
        folder = home / name
        folder.mkdir(mode=paths.PRIVATE, parents=True, exist_ok=True)
        folder.chmod(paths.PRIVATE)
    print(f"folders ready under {home} (owner only)")

    config = paths.config_file()
    if config.exists():
        print(f"{config} already exists; left unchanged")
    else:
        example = resources.files("bench") / "data" / "config.example.toml"
        config.write_text(example.read_text())
        config.chmod(0o600)
        print(f"wrote {config}")

    denylist = paths.denylist_file()
    if denylist.exists():
        print(f"{denylist} already exists; left unchanged")
    else:
        denylist.write_text(
            "# One term per line. The pre commit hook in the Bench repository rejects any staged change\n"
            "# containing one of these (case insensitive). This file never goes into the repository.\n"
        )
        denylist.chmod(0o600)
        print(f"wrote {denylist} (add your terms to it)")

    for area in core.AREAS:
        existed = core.db_path(area).exists()
        for line in migrate.prepare(area):
            print(line)
        print(f"database {area}: {core.db_path(area)} {'already there' if existed else 'created'}")
    return 0


# ---------------------------------------------------------------- check

def cmd_check() -> int:
    problems = 0
    cfg = load(paths.config_file())
    print(green(f"config: {cfg.path} loads"))

    tmux_path = shutil.which(cfg.cockpit.tmux)
    if tmux_path:
        version = subprocess.run([tmux_path, "-V"], capture_output=True, text=True).stdout.strip()
        print(f"tmux: {tmux_path} ({version})")
    else:
        print(red(f"tmux: {cfg.cockpit.tmux} not found on PATH"))
        problems += 1

    sock_dir = cfg.cockpit.socket.parent
    if sock_dir.exists():
        mode = sock_dir.stat().st_mode & 0o777
        line = f"socket folder: {sock_dir} mode {mode:o}"
        print(line if mode == 0o700 else red(line + " (should be 700)"))
        problems += mode != 0o700

    seen_commands: set[str] = set()
    for window in cfg.cockpit.windows:
        for pane in window.panes:
            found = safety.launch_folder_problems(pane.folder, cfg.cockpit.allowed_roots)
            if found:
                problems += 1
                for p in found:
                    print(red(f"pane {pane.role}: {p}"))
            if pane.command and pane.command[0] not in seen_commands:
                seen_commands.add(pane.command[0])
                where = shutil.which(pane.command[0])
                print(f"command {pane.command[0]}: {where}" if where else red(f"command {pane.command[0]}: not found on PATH"))
                problems += where is None
    if not problems:
        print(green("cockpit folders and commands: ok"))

    if cfg.required_env:
        for line in _env_presence(cfg.required_env):
            print(line if line.endswith("present") else red(line))
            problems += line.endswith("missing")

    print()
    print("databases:")
    for text, ok in report.area_lines(cfg):
        print(f"  {text}" if ok else red(f"  {text}"))
        problems += not ok

    print()
    print("\n".join(guard.status_lines()))

    print()
    print("Claude Code context that loads automatically (names only):")
    folders = sorted({str(p.folder) for w in cfg.cockpit.windows for p in w.panes if p.command})
    for folder in folders:
        found = context_check.ancestor_claude_md(Path(folder))
        listed = ", ".join(str(p) for p in found) or "none"
        print(f"  launch folder {folder}: CLAUDE.md files: {listed}")
    for line in context_check.user_level():
        print(f"  {line}")
    print("  Note: the folder checks stop a pane from starting in a home folder repository. They do not stop")
    print("  user level settings, skills, and plugins in ~/.claude from loading in every session, and they do")
    print("  not cover `claude` started by hand outside Bench (the zsh home guard covers the home folder case).")

    print()
    print(green("no problems found") if not problems else red(f"{problems} problem(s) found"))
    return 0 if not problems else 1


def _env_presence(names: list[str]) -> list[str]:
    """Whether each name is set in a fresh interactive login shell. Values are never read back."""
    script = 'for n in "$@"; do if [[ -n ${(P)n} ]]; then print -r -- "$n present"; else print -r -- "$n missing"; fi; done'
    result = subprocess.run(
        ["/bin/zsh", "-l", "-i", "-c", script, "zsh", *names],
        capture_output=True, text=True, timeout=30, cwd=paths.bench_home(),
    )
    lines = [l for l in result.stdout.splitlines() if l.endswith((" present", " missing"))]
    return [f"project env {l}" for l in lines]


# ---------------------------------------------------------------- dashboard and databases

def daily_backups() -> list[str]:
    """Take each area's daily backup if the newest is more than a day old. One short line per backup made."""
    notes = []
    for area in core.AREAS:
        try:
            made = backup.daily_if_due(area)
            if made:
                notes.append(f"{area}: daily backup {made.name}")
        except (core.DatabaseError, sqlite3.Error, OSError) as exc:
            notes.append(f"{area}: backup problem: {exc}")
    return notes


def cmd_dashboard() -> int:
    """Bring each area database up to date (backing up first), take any daily backups due, then open.

    While it runs, the dashboard checks for a due daily backup on every refresh.
    """
    cfg = load(paths.config_file())
    notes = []
    for area in core.AREAS:
        try:
            notes += migrate.prepare(area)
        except (core.DatabaseError, sqlite3.Error) as exc:
            notes.append(f"{area}: database problem: {exc}")
    notes += daily_backups()
    from bench.tui.app import BenchApp

    # The dashboard keeps every area database open while it runs (Stage E reads and writes through these
    # connections), so `bench restore` refuses until the dashboard quits.
    connections = {area: core.connect(core.db_path(area)) for area in core.AREAS}
    try:
        BenchApp(cfg, notes, connections=connections, take_backups=daily_backups).run()
    finally:
        for conn in connections.values():
            conn.close()
    return 0


def cmd_backup(args) -> int:
    areas = [args.area] if args.area else list(core.AREAS)
    made = 0
    for area in areas:
        if not core.db_path(area).exists():
            print(red(f"{area}: no database yet; run `bench init`"))
            continue
        path = backup.backup(area, "daily")
        print(green(f"{area}: {path} (integrity ok)"))
        made += 1
    return 0 if made == len(areas) else 1


def cmd_restore(args) -> int:
    source = args.file.expanduser().resolve()
    print(f"This replaces the {args.area} database with {source}.")
    print("The current database is backed up first, and nothing happens while Bench has it open.")
    typed = input("Type restore to confirm: ")
    if typed.strip() != "restore":
        print("Not confirmed. Nothing was changed.")
        return 1
    for line in backup.restore(args.area, source):
        print(green(line))
    return 0


# ---------------------------------------------------------------- cockpit

def _tmux(cfg: Config) -> Tmux:
    sock_dir = cfg.cockpit.socket.parent
    sock_dir.mkdir(mode=paths.PRIVATE, parents=True, exist_ok=True)
    sock_dir.chmod(paths.PRIVATE)
    return Tmux(cfg.cockpit.tmux, cfg.cockpit.socket)


def cmd_cockpit(args) -> int:
    cfg = load(paths.config_file())
    tmux = _tmux(cfg)
    session = cfg.cockpit.session

    if args.action == "status":
        if not tmux.has_session(session):
            print(f"cockpit: not running (session {session!r} on {cfg.cockpit.socket})")
            return 0
        print(f"cockpit: session {session!r} on {cfg.cockpit.socket}")
        print("\n".join(cockpit_plan.status_lines(cfg.cockpit, tmux.panes(session))))
        return 0

    if args.action == "build":
        exists = tmux.has_session(session)
        panes = tmux.panes(session)
        actions = cockpit_plan.make_plan(cfg.cockpit, exists, panes)
        if not actions:
            print("nothing to do; the cockpit already matches config.toml")
            print("\n".join(cockpit_plan.status_lines(cfg.cockpit, panes)))
            return 0
        refused = []
        for action in actions:
            for p in safety.launch_folder_problems(action.pane.folder, cfg.cockpit.allowed_roots):
                refused.append(f"{action.pane.role}: {p}")
            if action.pane.command and not shutil.which(action.pane.command[0]):
                refused.append(f"{action.pane.role}: {action.pane.command[0]} not found on PATH")
        if refused:
            print(red("Refusing to build. Nothing was created:"))
            for line in refused:
                print(red(f"  {line}"))
            return 1
        print(f"About to {'add to' if exists else 'create'} the cockpit on {cfg.cockpit.socket}:")
        for i, action in enumerate(actions, start=1):
            print(f"  {i}. {action.describe()}")
        input("Press Enter to go ahead, or Control+C to cancel. ")
        cockpit_plan.execute(tmux, cfg.cockpit, actions, panes)
        print(green("done."))
        print("\n".join(cockpit_plan.status_lines(cfg.cockpit, tmux.panes(session))))
        print("Panes take a few seconds to start their programs. Attach with: bench cockpit attach")
        return 0

    if args.action == "restart":
        spec = next((p for w in cfg.cockpit.windows for p in w.panes if p.role == args.role), None)
        if spec is None:
            print(red(f"no pane with role {args.role!r} in config.toml"))
            return 1
        state = next((p for p in tmux.panes(session) if p.window == spec.window and p.role == spec.role), None)
        if state is None:
            print(red(f"{spec.role} is not in the running cockpit; run `bench cockpit build`"))
            return 1
        problems = safety.launch_folder_problems(spec.folder, cfg.cockpit.allowed_roots)
        if problems:
            print(red(f"Refusing: {'; '.join(problems)}"))
            return 1
        print(f"{spec.role}: {cockpit_plan.pane_status(spec, state)}")
        print(f"  restart runs: {' '.join(spec.command) if spec.command else 'a shell'} in {spec.folder}")
        if cockpit_plan.is_busy(spec, state):
            typed = input(f"A program is running in this pane and will be ended. Type {spec.role} to confirm: ")
            if typed.strip() != spec.role:
                print("Not confirmed. Nothing was changed.")
                return 1
        else:
            input("Press Enter to restart, or Control+C to cancel. ")
        command = cockpit_plan.shell_command(spec.command)
        tmux.run("respawn-pane", "-k", "-t", state.pane_id, "-c", str(spec.folder), *([command] if command else []))
        print(green(f"{spec.role} restarted."))
        return 0

    if args.action == "attach":
        if os.environ.get("TMUX"):
            print(red("This window is already inside tmux. Detach first (Control+Space, then d)."))
            return 1
        if not tmux.has_session(session):
            print(red("The cockpit is not running. Run `bench cockpit build` first."))
            return 1
        left_exists = args.left and tmux.has_session(cfg.cockpit.left_session)
        try:
            argv = cockpit_plan.attach_argv(cfg.cockpit, tmux.base(), args.left, left_exists, args.window)
        except ValueError as exc:
            print(red(str(exc)))
            return 1
        os.execvp(argv[0], argv)

    if args.action == "kill":
        if not tmux.socket.exists() or tmux.run("list-sessions", check=False).returncode != 0:
            print("cockpit: not running; nothing to stop")
            return 0
        print("This stops Bench's tmux server and every program in it, including any Claude Code sessions.")
        typed = input("Type kill to confirm: ")
        if typed.strip() != "kill":
            print("Not confirmed. Nothing was changed.")
            return 1
        tmux.run("kill-server", check=False)
        print(green("cockpit stopped."))
        return 0
    return 1


# ---------------------------------------------------------------- caffeinate

def cmd_caffeinate(args) -> int:
    if args.action == "status":
        print("\n".join(guard.status_lines()))
        return 0
    if args.action == "off":
        print("\n".join(guard.turn_off()))
        return 0
    if args.action == "kill":
        print("\n".join(guard.kill()))
        return 0
    cfg = load(paths.config_file())
    try:
        seconds = guard.parse_duration(args.duration)
    except ValueError as exc:
        print(red(str(exc)))
        return 2
    print("\n".join(guard.turn_on(cfg.caffeinate, seconds)))
    return 0
