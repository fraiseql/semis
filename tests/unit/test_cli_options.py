"""Every argument and option of the ``semis`` command is declared in cli/options.py.

A flag declared twice is two flags that drift: one command's help, default or type moves
and the other's does not.
"""

import ast

from tests.unit.guards import package_modules

OWNER = "cli/options.py"


def _declarations(tree: ast.Module) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"Option", "Argument"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "typer"
    ]


def test_options_are_declared_in_one_module() -> None:
    offenders = {
        name: lines
        for name, tree in package_modules().items()
        if name != OWNER and (lines := _declarations(tree))
    }
    assert offenders == {}


def test_the_owner_is_not_stale() -> None:
    assert _declarations(package_modules()[OWNER])


def test_guard_sees_an_option() -> None:
    assert _declarations(ast.parse('Seed = Annotated[int, typer.Option("--seed")]'))
