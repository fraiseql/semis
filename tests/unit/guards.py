"""Shared plumbing for the seam guards: the package's modules, parsed."""

import ast
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
