"""Shared plumbing for the guards: the package's modules, parsed, and the files that ship."""

import ast
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "fraiseql_semis"


def package_modules() -> dict[str, ast.Module]:
    """Every module in the package, by its path relative to the package."""
    return {
        path.relative_to(PACKAGE).as_posix(): ast.parse(path.read_text(), filename=str(path))
        for path in sorted(PACKAGE.rglob("*.py"))
    }


def imported_roots(tree: ast.Module) -> set[str]:
    """The top-level package of every import in *tree*."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def tracked_text_files(root: Path) -> list[Path]:
    """Every text file git tracks under *root*: what ``git archive`` publishes."""
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    )
    return text_files(root / name for name in listed.stdout.split("\0") if name)


def text_files(paths: Iterable[Path]) -> list[Path]:
    """*paths* that are files holding text: a NUL byte marks a binary one, as git judges."""
    return [path for path in paths if path.is_file() and b"\0" not in path.read_bytes()[:8000]]


def named_in(root: Path, files: Iterable[Path], pattern: re.Pattern[str]) -> list[str]:
    """The files, relative to *root*, whose name or text *pattern* finds."""
    return [
        path.relative_to(root).as_posix()
        for path in files
        if pattern.search(path.relative_to(root).as_posix())
        or pattern.search(path.read_bytes().decode(errors="replace"))
    ]
