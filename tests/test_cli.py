"""Tests for the package command line."""

from pathlib import Path

import pytest

from granola_kg.cli import CliNamespace, build_parser, main
from granola_kg.version import __version__

ERROR_EXIT_CODE = 2
CUSTOM_EXPLORER_PORT = 9000


def test_parser_uses_public_command_name() -> None:
    """The installed command should retain its documented name."""
    assert build_parser().prog == "granola-kg"


def test_parser_exposes_local_explorer_options() -> None:
    """The explorer command should support a custom local port and headless launch."""
    args = build_parser().parse_args(
        ["explore", "--port", str(CUSTOM_EXPLORER_PORT), "--no-open"],
        namespace=CliNamespace(),
    )

    assert args.command == "explore"
    assert args.port == CUSTOM_EXPLORER_PORT
    assert args.no_open is True


def test_empty_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """The empty command should be safe and informative."""
    assert main([]) == 0
    capture = capsys.readouterr()
    assert "local knowledge graph" in capture.out


def test_package_version_is_pep440_compatible() -> None:
    """The initial version should be exposed by the package."""
    assert __version__ == "0.1.0"


def test_init_status_and_empty_search_are_json_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Local commands should initialize and query one explicit database."""
    database = tmp_path / "graph.db"

    assert main(["--db", str(database), "init"]) == 0
    assert '"initialized": true' in capsys.readouterr().out
    assert main(["--db", str(database), "status"]) == 0
    assert '"pending": 0' in capsys.readouterr().out
    assert main(["--db", str(database), "search", "nothing"]) == 0
    assert '"results": []' in capsys.readouterr().out


def test_sync_requires_environment_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote commands should fail before networking when secrets are absent."""
    monkeypatch.delenv("GRANOLA_API_KEY", raising=False)
    monkeypatch.delenv("GRANOLA_KG_LLM_MODEL", raising=False)

    assert main(["--db", str(tmp_path / "graph.db"), "sync"]) == ERROR_EXIT_CODE
    assert "Set GRANOLA_API_KEY" in capsys.readouterr().err


def test_reprocess_requires_exactly_one_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reprocessing cannot ambiguously select no notes or two selectors."""
    database = tmp_path / "graph.db"
    main(["--db", str(database), "init"])
    capsys.readouterr()

    assert main(["--db", str(database), "reprocess"]) == ERROR_EXIT_CODE
    assert "exactly one" in capsys.readouterr().err


def test_install_command_copies_packaged_skill(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The distribution CLI should install its bundled assistant skill."""
    skills = tmp_path / "skills"

    assert main(["install", "--skills-dir", str(skills)]) == 0

    assert (skills / "granola-kg" / "SKILL.md").is_file()
    assert '"mcp_command": "granola-kg-mcp"' in capsys.readouterr().out
