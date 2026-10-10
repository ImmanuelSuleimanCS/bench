"""Bench: a personal command center for the Mac."""

__version__ = "0.1.0"


def main() -> None:
    from bench.cli import main as cli_main

    raise SystemExit(cli_main())
