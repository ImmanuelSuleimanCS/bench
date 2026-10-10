"""List the Claude Code context files that load automatically for a launch folder.

This only reports. It reads file names and the allow rule list, never file contents otherwise.
"""
import json
from pathlib import Path


def ancestor_claude_md(folder: Path) -> list[Path]:
    """CLAUDE.md and CLAUDE.local.md files in `folder` and every folder above it."""
    found = []
    for directory in [folder.resolve(), *folder.resolve().parents]:
        for name in ("CLAUDE.md", "CLAUDE.local.md"):
            candidate = directory / name
            if candidate.is_file():
                found.append(candidate)
    return found


def user_level(home: Path | None = None) -> list[str]:
    """One line per user level item that applies to every Claude Code session."""
    home = home or Path.home()
    claude = home / ".claude"
    lines = []
    for name in ("CLAUDE.md", "settings.json", "settings.local.json"):
        path = claude / name
        if path.is_file():
            lines.append(f"{path} ({path.stat().st_size} bytes)")
    local = claude / "settings.local.json"
    if local.is_file():
        try:
            allow = json.loads(local.read_text()).get("permissions", {}).get("allow", [])
            lines.append(f"  user level allow rules: {len(allow)}")
            for rule in allow:
                if "git push" in rule:
                    lines.append(f"  warning: user level rule allows pushing without asking: {rule}")
        except (json.JSONDecodeError, AttributeError):
            lines.append(f"  could not read {local} as JSON")
    for name in ("skills", "plugins", "hooks", "agents", "commands"):
        path = claude / name
        if path.is_dir():
            count = sum(1 for _ in path.iterdir())
            lines.append(f"{path}/ ({count} entries)")
    return lines
