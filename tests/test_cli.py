"""Tests for the package command line."""

import pytest

from granola_kg.cli import build_parser, main
from granola_kg.version import __version__


def test_parser_uses_public_command_name() -> None:
    """The installed command should retain its documented name."""
    assert build_parser().prog == "granola-kg"


def test_empty_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """The empty command should be safe and informative."""
    assert main([]) == 0
    capture = capsys.readouterr()
    assert "local knowledge graph" in capture.out


def test_package_version_is_pep440_compatible() -> None:
    """The initial version should be exposed by the package."""
    assert __version__ == "0.1.0"
