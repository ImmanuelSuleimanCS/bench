"""Bench: a personal command center for the Mac."""

__version__ = "0.3.1"


def main() -> None:
    from bench.cli import main as cli_main

    raise SystemExit(cli_main())
