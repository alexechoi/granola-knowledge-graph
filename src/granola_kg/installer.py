"""Install the packaged assistant skill into a local skills directory."""

from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from importlib.resources.abc import Traversable

SKILL_NAME = "granola-kg"


def default_skills_directory(environ: Mapping[str, str] = os.environ) -> Path:
    """Return the Codex-compatible local skills directory."""
    codex_home = environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "skills"
    return Path.home() / ".codex" / "skills"


def install_skill(
    skills_directory: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    """Copy the packaged Granola skill into a discoverable local directory."""
    root = skills_directory or default_skills_directory()
    destination = root / SKILL_NAME
    if destination.exists():
        if not force:
            msg = f"Skill already exists: {destination}; pass --force to replace it"
            raise FileExistsError(msg)
        shutil.rmtree(destination)
    source = resources.files("granola_kg").joinpath("skill", SKILL_NAME)
    _copy_resource_tree(source, destination)
    return destination


def _copy_resource_tree(source: Traversable, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            _copy_resource_tree(child, target)
        else:
            target.write_bytes(child.read_bytes())
