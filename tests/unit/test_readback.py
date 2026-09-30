"""readback.learn's refusals, which it makes before it reaches the database."""

from typing import Any

import pytest

from fraiseql_semis import readback
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ResolutionError
from fraiseql_semis.schema import SchemaFacts


class _Unreachable:
    """A connection that fails any test that uses it."""

    def cursor(self) -> Any:
        raise AssertionError("the connection was used")

    def execute(self, query: Any, params: Any = None) -> Any:
        raise AssertionError(f"the connection was used: {query}, {params}")

    def commit(self) -> None:
        raise AssertionError("the connection was used")

    def rollback(self) -> None:
        raise AssertionError("the connection was used")


def test_a_table_without_trinity_roles_is_refused_before_the_database() -> None:
    ddl = "CREATE SCHEMA catalog; CREATE TABLE catalog.tb_note (body TEXT);"
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({"catalog.tb_note": 0x0A}))
    with pytest.raises(ResolutionError, match=r"catalog\.tb_note shows no surrogate key"):
        readback.learn(_Unreachable(), facts.facts_for("catalog.tb_note"), ["a"])


def test_paths_without_a_natural_id_are_refused_before_the_database() -> None:
    ddl = "CREATE SCHEMA catalog; CREATE TABLE catalog.tb_note (body TEXT);"
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({"catalog.tb_note": 0x0A}))
    with pytest.raises(ResolutionError, match=r"catalog\.tb_note shows no natural id"):
        readback.set_paths(_Unreachable(), facts.facts_for("catalog.tb_note"), "path", {})
