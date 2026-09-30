"""readback.py is the only module that imports psycopg, and holds the only SQL semis writes (D9).

Everything else about a database goes through confiture; a second SQL site is a second
place an identifier could be spliced into a query.
"""

import ast
import re

from tests.unit.guards import imported_roots, package_modules

OWNER = "readback.py"

# A string that reads as a statement. Docstrings are prose, not queries, and are skipped.
_STATEMENT = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\S+(\s+AS\s+\S+)?\s+SET|DELETE\s+FROM|COPY\s|CREATE\s|ALTER\s"
    r"|DROP\s|TRUNCATE\s|WITH\s+\S+\s+AS)"
)


def _docstrings(tree: ast.Module) -> set[int]:
    nodes = [
        tree,
        *(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        ),
    ]
    return {
        id(node.body[0].value)
        for node in nodes
        if node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def _sql_sites(tree: ast.Module) -> list[str]:
    found = [f"imports {root}" for root in sorted(imported_roots(tree) & {"psycopg"})]
    prose = _docstrings(tree)
    found.extend(
        f"line {node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
        and _STATEMENT.search(node.value)
    )
    return found


def test_only_readback_imports_psycopg_or_writes_sql() -> None:
    offenders = {
        name: sites
        for name, tree in package_modules().items()
        if name != OWNER and (sites := _sql_sites(tree))
    }
    assert offenders == {}


def test_the_owner_is_not_stale() -> None:
    assert _sql_sites(package_modules()[OWNER])


def test_guard_sees_an_import_and_a_statement() -> None:
    imported = ast.parse("from psycopg import sql")
    written = ast.parse('query = f"SELECT pk FROM {table}"')
    aliased = ast.parse('query = "UPDATE {schema}.{table} AS t SET {path} = v.path"')
    assert all(_sql_sites(tree) for tree in (imported, written, aliased))


def test_guard_reads_a_docstring_as_prose() -> None:
    assert _sql_sites(ast.parse('"""SELECT pk_x FROM t: the one query."""')) == []
