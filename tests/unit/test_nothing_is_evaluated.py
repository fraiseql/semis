"""No string is evaluated as code anywhere in the package.

A scenario names a registered provider; it never carries one. The PRD sketched
``eval(provider_code, ...)`` for scenario providers, and that sketch does not ship. A
project's own providers are a module ``semis.yaml`` names, imported by one module.
"""

import ast

from tests.unit.guards import package_modules

# The modules allowed to import a module by its name, each with its reason. An entry that
# no longer does is stale and fails.
IMPORTERS = {"project.py": "semis.yaml names a project's providers as module:attribute"}

_EVALUATORS = {"eval", "exec", "compile", "__import__"}


def _evaluations(tree: ast.Module) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _EVALUATORS
    ]


def test_no_module_evaluates_a_string() -> None:
    offenders = {
        name: lines for name, tree in package_modules().items() if (lines := _evaluations(tree))
    }
    assert offenders == {}


def test_guard_sees_an_evaluation() -> None:
    assert _evaluations(ast.parse("provider = eval('lambda f: f.country()')"))


def _imports_by_name(tree: ast.Module) -> bool:
    return any(
        (isinstance(node, ast.Attribute) and node.attr == "import_module")
        or (isinstance(node, ast.Name) and node.id == "import_module")
        for node in ast.walk(tree)
    )


def test_only_the_project_imports_a_module_by_name() -> None:
    importing = {name for name, tree in package_modules().items() if _imports_by_name(tree)}
    assert importing == set(IMPORTERS)


def test_guard_sees_an_import_by_name() -> None:
    assert _imports_by_name(ast.parse("importlib.import_module('x')"))
    assert _imports_by_name(ast.parse("from importlib import import_module\nimport_module('x')"))
