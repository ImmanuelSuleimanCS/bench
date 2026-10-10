"""Timestamps in Immanuel's file naming style: yyyymmdd.HHMM, Central time, from the system clock."""
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")


def stamp(now: datetime | None = None) -> str:
    """Return the stamp for `now` (an aware datetime), or for the current moment."""
    if now is None:
        now = datetime.now(timezone.utc)
    return now.astimezone(CENTRAL).strftime("%Y%m%d.%H%M")


def stamped_path(folder: Path, name: str, suffix: str, now: datetime | None = None) -> Path:
    """A path like folder/20261010.0930.name.md that does not exist yet.

    If a file with that stamp and name already exists, a counter is added: name.2, name.3, and so on.
    """
    base = f"{stamp(now)}.{name}"
    candidate = folder / f"{base}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{base}.{counter}{suffix}"
        counter += 1
    return candidate
