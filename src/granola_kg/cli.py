"""Command-line entrypoint for Granola Knowledge Graph."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from granola_kg.version import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level command parser."""
    parser = argparse.ArgumentParser(
        prog="granola-kg",
        description="Build and query a local knowledge graph from Granola meetings.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
