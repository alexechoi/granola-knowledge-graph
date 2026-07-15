"""Tests for the Graphify-style packaged skill installer."""

from pathlib import Path

import pytest

from granola_kg.installer import default_skills_directory, install_skill


def test_default_directory_honors_codex_home(tmp_path: Path) -> None:
    """Installation should support an explicit Codex home directory."""
    assert default_skills_directory({"CODEX_HOME": str(tmp_path)}) == tmp_path / "skills"


def test_installs_packaged_skill_and_metadata(tmp_path: Path) -> None:
    """The built package should carry both instructions and UI metadata."""
    destination = install_skill(tmp_path)

    skill_text = (destination / "SKILL.md").read_text(encoding="utf-8")
    metadata = (destination / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert "name: granola-kg" in skill_text
    assert "get_evidence" in skill_text
    assert 'display_name: "Granola Knowledge Graph"' in metadata


def test_refuses_overwrite_without_force(tmp_path: Path) -> None:
    """Existing user skills should only be replaced explicitly."""
    destination = install_skill(tmp_path)
    marker = destination / "marker.txt"
    marker.write_text("user change", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--force"):
        install_skill(tmp_path)

    replaced = install_skill(tmp_path, force=True)
    assert replaced == destination
    assert marker.exists() is False
