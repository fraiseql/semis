"""The reset: which tables it deletes from, and which it refuses to."""

from typing import Any

import pytest

from fraiseql_semis import readback
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ResetScopeError
from fraiseql_semis.scenario import Scenario, ScenarioManager, TableSpec
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, TRINITY

UNSCOPED = (
    TRINITY
    + """
CREATE TABLE catalog.tb_badge (
    pk_badge BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label TEXT NOT NULL
);
CREATE TABLE catalog.tb_tag (
    pk_tag BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL
);
"""
)
MANAGER = ScenarioManager(
    SchemaFacts.from_source(
        UNSCOPED,
        table_codes=TableCodes(
            CODES | {"catalog.tb_badge": 0x0A0B0C0D, "catalog.tb_tag": 0x0B0C0D0E}
        ),
    )
)


class _Recording:
    """A connection that records each statement, and answers a count with none."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, query: Any, _params: Any = None) -> Any:
        self.statements.append(query.as_string())
        return self

    rowcount = 0

    def fetchone(self) -> tuple[int]:
        return (0,)


def _scenario(*tables: str) -> Scenario:
    return Scenario(
        id=0x5001,
        name="s",
        mode="read-back",
        tables=tuple(TableSpec(table, 2) for table in tables),
    )


@pytest.mark.parametrize("table", ["catalog.tb_badge", "catalog.tb_tag"], ids=["no-id", "text-id"])
def test_a_table_without_a_uuid_id_is_refused_naming_it(table: str) -> None:
    with pytest.raises(
        ResetScopeError,
        match=rf"^scenario s: {table} has no uuid id, so the reset cannot tell the "
        "scenario's rows there from others",
    ):
        MANAGER.reset(_scenario(table), connection=_Recording())  # type: ignore[arg-type]


def test_an_unscoped_table_is_refused_before_any_row_is_deleted() -> None:
    """tb_badge is inserted first, so deleted last: its refusal must not wait for it."""
    connection = _Recording()
    with pytest.raises(ResetScopeError, match=r"^scenario s: catalog\.tb_badge has no uuid id"):
        MANAGER.reset(
            _scenario("catalog.tb_badge", "catalog.tb_continent"),
            connection=connection,  # type: ignore[arg-type]
        )
    assert [s for s in connection.statements if s.startswith("DELETE")] == []


def test_no_referencing_table_takes_no_lock() -> None:
    """``LOCK TABLE`` names at least one table: with none, nothing is sent."""
    connection = _Recording()
    readback.lock_shared(connection, [])  # type: ignore[arg-type]
    assert connection.statements == []
