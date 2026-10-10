"""Checks run before Bench starts anything in a folder.

What these checks do: refuse a pane whose folder is missing, outside the allowed roots, the home folder
itself, or inside a git repository whose root is the home folder (the condition behind the demo leak).

What they do not do: they cannot stop Claude Code from loading user level settings, skills, and plugins
from ~/.claude, which apply to every session, and they do nothing for `claude` started by hand outside
Bench. The zsh guard in ~/.zshrc covers the most common hand started case.
"""
import subprocess
from pathlib import Path


def git_root(folder: Path) -> Path | None:
    """The git repository root containing `folder`, or None when it is not in a repository."""
    try:
        result = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def launch_folder_problems(folder: Path, allowed_roots: list[Path], home: Path | None = None) -> list[str]:
    """Every reason `folder` must not be used as a launch folder. An empty list means it is allowed."""
    home = (home or Path.home()).resolve()
    if not folder.exists():
        return [f"{folder} does not exist"]
    if not folder.is_dir():
        return [f"{folder} is not a folder"]
    real = folder.resolve()
    problems = []
    if real == home:
        problems.append(f"{folder} is your home folder")
    roots = [r.expanduser().resolve() for r in allowed_roots]
    if not any(real == r or r in real.parents for r in roots):
        problems.append(f"{folder} is outside the allowed roots ({', '.join(str(r) for r in roots)})")
    root = git_root(real)
    if root is not None and root == home:
        problems.append(f"{folder} is inside a git repository rooted at your home folder")
    return problems
