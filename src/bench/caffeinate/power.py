"""Read the power source and battery level from `pmset -g batt`."""
import os
import re
import subprocess


def parse(text: str) -> tuple[str, int] | None:
    """("AC" or "Battery", percent), or None when the output cannot be trusted."""
    source = re.search(r"Now drawing from '([^']+)'", text)
    percent = re.search(r"(\d+)%", text)
    if not source or not percent:
        return None
    kind = {"AC Power": "AC", "Battery Power": "Battery"}.get(source.group(1))
    if kind is None:
        return None
    return kind, int(percent.group(1))


def read() -> tuple[str, int] | None:
    command = os.environ.get("BENCH_PMSET", "pmset")
    try:
        out = subprocess.run([command, "-g", "batt"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse(out)
