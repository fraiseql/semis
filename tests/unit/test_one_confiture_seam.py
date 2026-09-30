"""Only the seam modules import confiture (D1), so a rename there lands in one place."""

import ast

from tests.unit.guards import imported_roots, package_modules

# The modules allowed to import confiture, each with its reason. An entry that no
# longer imports it is stale and fails.
SEAM = {
    "schema.py": "reads the schema, and re-exports the confiture types semis passes around",
    "seeds.py": "writes and applies seed files",
}


def _imports_confiture(tree: ast.Module) -> bool:
    return "confiture" in imported_roots(tree)


def test_only_the_seam_imports_confiture() -> None:
    offenders = [
        name
        for name, tree in package_modules().items()
        if name not in SEAM and _imports_confiture(tree)
    ]
    assert offenders == []


def test_seam_entries_are_not_stale() -> None:
    modules = package_modules()
    stale = [name for name in SEAM if not _imports_confiture(modules[name])]
    assert stale == []


def test_guard_sees_both_import_forms() -> None:
    assert _imports_confiture(ast.parse("import confiture.platform"))
    assert _imports_confiture(ast.parse("from confiture.platform import diff"))
