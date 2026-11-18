"""Run Momo's command-line interface with ``python -m momo``."""

from __future__ import annotations

import typer

from interface.app import main as _cli_main


def main() -> None:
    typer.run(_cli_main)


if __name__ == "__main__":
    main()
