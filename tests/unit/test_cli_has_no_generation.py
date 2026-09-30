"""The ``semis`` command delegates: it decides nothing about what a row holds.

Its modules may import from the package only what this allow-list names, each entry with
its reason. Generating, resolving, checking and emitting rows stay in the library, where
a Python caller (specql, fraiseql) reaches them without the command. An entry nothing
imports any more is stale and fails.
"""

import ast

from tests.unit.guards import package_modules

# module → the names the CLI may import from it (None: any), and why.
ALLOWED: dict[str, tuple[frozenset[str] | None, str]] = {
    "fraiseql_semis.cli.main": (None, "the package re-exports the app and its entry point"),
    "fraiseql_semis.cli.options": (None, "the command line's own declarations"),
    "fraiseql_semis.errors": (None, "the refusals the error boundary turns into exit codes"),
    "fraiseql_semis.project": (None, "semis.yaml: the schema, codes and scenarios to hand over"),
    "fraiseql_semis.scenario": (None, "the runs every command delegates to"),
    "fraiseql_semis.readback": (frozenset({"transaction"}), "the one transaction apply owns"),
    "fraiseql_semis.schema": (frozenset({"ConfiturError"}), "confiture's refusals, unwrapped"),
    "fraiseql_semis.seeds": (
        frozenset({"Format", "Mode", "PrepSeedReport"}),
        "the choices --format and --mode take, and the report validate-seeds prints",
    ),
    "fraiseql_semis.uuid_generator": (
        frozenset({"SemanticUUIDGenerator"}),
        "decode-uuid decodes, and generates nothing",
    ),
}


def _imports(tree: ast.Module) -> list[tuple[str, str | None]]:
    """Each (module, name) *tree* imports from the package; name ``None`` for a bare import."""
    found: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("fraiseql_semis"):
            found.extend((node.module or "", alias.name) for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend(
                (alias.name, None)
                for alias in node.names
                if alias.name.startswith("fraiseql_semis")
            )
    return found


def _cli_imports() -> list[tuple[str, str | None]]:
    return [
        imported
        for name, tree in package_modules().items()
        if name.startswith("cli/")
        for imported in _imports(tree)
    ]


def _refused(module: str, name: str | None) -> bool:
    if module not in ALLOWED:
        return True
    names, _ = ALLOWED[module]
    return names is not None and name not in names


def test_the_cli_imports_only_what_it_delegates_to() -> None:
    assert [imported for imported in _cli_imports() if _refused(*imported)] == []


def test_allowed_entries_are_not_stale() -> None:
    used = {module for module, _ in _cli_imports()}
    assert sorted(set(ALLOWED) - used) == []


def test_guard_refuses_the_generator_and_a_second_readback_name() -> None:
    tree = ast.parse(
        "from fraiseql_semis.generator import FakeDataGenerator\n"
        "from fraiseql_semis.readback import learn\n"
        "import fraiseql_semis.emit\n"
    )
    assert [imported for imported in _imports(tree) if _refused(*imported)] == [
        ("fraiseql_semis.generator", "FakeDataGenerator"),
        ("fraiseql_semis.readback", "learn"),
        ("fraiseql_semis.emit", None),
    ]
