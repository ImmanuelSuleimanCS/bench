"""One line per area database, for `bench check` and the dashboard's Home view. Reads only."""
import time

from bench.config import Config
from bench.db import backup, core, migrate


def area_lines(cfg: Config) -> list[tuple[str, bool]]:
    """(text, ok) per area. Never creates a database: a missing file is reported, not made."""
    lines = []
    for area in core.AREAS:
        label = cfg.areas.get(area, area)
        path = core.db_path(area)
        head = f"{area:<9} {label:<14}"
        if not path.exists():
            lines.append((f"{head} no database yet; run `bench init`", False))
            continue
        mode = path.stat().st_mode & 0o777
        conn = core.connect(path)
        try:
            current = migrate.versions(conn)
            waiting = migrate.pending(conn, area)
        finally:
            conn.close()
        scopes = ", ".join(f"{scope} {version}" for scope, version in sorted(current.items())) or "empty"
        newest = backup.backups(area, "daily")
        if newest:
            hours = (time.time() - newest[0].stat().st_mtime) / 3600
            last = f"backup {hours:.0f}h ago" if hours >= 1 else "backup <1h ago"
        else:
            last = "no backup yet"
        text = f"{head} {scopes:<10} {last}"
        ok = True
        if mode != 0o600:
            text += f"  (file mode {mode:o}, should be 600)"
            ok = False
        if waiting:
            text += f"  ({len(waiting)} migration(s) pending; run `bench init`)"
            ok = False
        lines.append((text, ok))
    return lines
