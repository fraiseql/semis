"""The modules that decide what a row holds reach no database and no file system.

That is what lets the row contract be tested against a DDL string (ARCHITECTURE §3).
"""

import ast

import pytest

from tests.unit.guards import imported_roots, package_modules

PURE = (
    "generator.py",
    "rows.py",
    "faker_provider.py",
    "resolution.py",
    "pin.py",
    "hierarchy.py",
    "staging.py",
    "providers/__init__.py",
    "providers/i18n.py",
    "providers/organization.py",
)

# What would let a module reach a database or the file system.
_OUTSIDE_WORLD = {"confiture", "psycopg", "os", "pathlib", "io", "shutil", "tempfile"}


@pytest.mark.parametrize("module", PURE)
def test_module_holds_no_database_and_no_file_system(module: str) -> None:
    tree = package_modules()[module]
    opens = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"
    ]
    assert (imported_roots(tree) & _OUTSIDE_WORLD, opens) == (set(), [])
