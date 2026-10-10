"""Bench: a personal command center for the Mac."""

__version__ = "0.2.0"


def main() -> None:
    from bench.cli import main as cli_main

    raise SystemExit(cli_main())
