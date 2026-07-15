"""Enforce repository constraints that are not covered by Ruff."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

MAX_FILE_LINES = 1_000
SOURCE_ROOTS = (Path("src"), Path("scripts"), Path("tests"))


def python_files() -> list[Path]:
    """Return checked Python files in deterministic order."""
    return sorted(path for root in SOURCE_ROOTS for path in root.rglob("*.py"))


def qualified_name(node: ast.expr) -> str | None:
    """Return a dotted name for a simple expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def forbidden_type_errors(path: Path, tree: ast.AST) -> list[str]:
    """Find explicit dynamic types and type-casting calls."""
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            errors.append(f"{path}:{node.lineno}: dynamic type is forbidden")
        if isinstance(node, ast.Attribute) and node.attr == "Any":
            errors.append(f"{path}:{node.lineno}: dynamic type is forbidden")
        if isinstance(node, ast.Call):
            name = qualified_name(node.func)
            if name in {"cast", "typing.cast"}:
                errors.append(f"{path}:{node.lineno}: type casting is forbidden")
    return errors


def check_file(path: Path) -> list[str]:
    """Validate one Python file."""
    source = path.read_text(encoding="utf-8")
    errors: list[str] = []
    line_count = len(source.splitlines())
    if line_count > MAX_FILE_LINES:
        errors.append(f"{path}: {line_count} lines exceeds {MAX_FILE_LINES}")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        errors.append(f"{path}:{error.lineno}: {error.msg}")
        return errors
    return [*errors, *forbidden_type_errors(path, tree)]


def main() -> int:
    """Run all repository constraint checks."""
    errors = [error for path in python_files() for error in check_file(path)]
    if errors:
        joined_errors = "\n".join(errors)
        sys.stderr.write(f"{joined_errors}\n")
        return 1
    sys.stdout.write(f"Checked {len(python_files())} Python files\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
