#!/usr/bin/env python3
"""Compatibility shim that forwards to the new src.cli.main entry point."""
from __future__ import annotations

from src.cli.main import main as cli_main


def main() -> None:
    cli_main()


if __name__ == "__main__":
    main()
