"""The already-applied check: which tables it asks, and which refusal it makes."""

from typing import Any

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import AlreadyAppliedError
from fraiseql_semis.scenario import Scenario, ScenarioManager, TableSpec
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, TRINITY


class _Found:
    def __init__(self, found: bool) -> None:
        self._found = found

    def fetchone(self) -> tuple[bool]:
        return (self._found,)


class _Recording:
    """A connection that answers the check from *holding*, and records what it asked."""

    def __init__(self, *holding: str) -> None:
        self.holding = set(holding)
        self.asked: list[str] = []

    def execute(self, query: Any, _params: Any = None) -> _Found:
        table = query.as_string().split(" FROM ", 1)[1].split(" WHERE ", 1)[0]
        self.asked.append(table)
        return _Found(table in self.holding)


def _scenario(mode: str, *tables: str) -> Scenario:
    return Scenario(
        id=0x5001,
        name="s",
        mode=mode,  # type: ignore[arg-type]
        tables=tuple(TableSpec(table, 2) for table in tables),
    )


def _refusal(manager: ScenarioManager, scenario: Scenario, connection: _Recording) -> str:
    with pytest.raises(AlreadyAppliedError) as refused:
        manager._refuse_reapply(scenario, connection)  # type: ignore[arg-type]
    return refused.value.message


TRINITY_MANAGER = ScenarioManager(SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES)))
BOTH = ("catalog.tb_continent", "catalog.tb_country")


def test_only_the_child_holding_rows_is_refused_naming_it() -> None:
    connection = _Recording('"catalog"."tb_country"')
    assert _refusal(TRINITY_MANAGER, _scenario("read-back", *BOTH), connection) == (
        "scenario s is already applied: catalog.tb_country holds its rows"
    )
    assert connection.asked == ['"catalog"."tb_continent"', '"catalog"."tb_country"']


def test_prep_seed_with_only_the_final_table_holding_rows_is_refused_naming_it() -> None:
    connection = _Recording('"catalog"."tb_country"')
    assert _refusal(TRINITY_MANAGER, _scenario("prep-seed", *BOTH), connection) == (
        "scenario s is already applied: catalog.tb_country holds its rows"
    )


UNCHECKABLE = (
    TRINITY
    + """
CREATE TABLE catalog.tb_note (
    pk_note BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    body TEXT NOT NULL
);
CREATE TABLE catalog.tb_tag (
    pk_tag BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL
);
CREATE TABLE prep_seed.tb_tag (id TEXT NOT NULL UNIQUE, identifier TEXT NOT NULL, label TEXT);
"""
)
UNCHECKABLE_CODES = {**CODES, "catalog.tb_note": 0x0A0B0C0D, "catalog.tb_tag": 0x0B0C0D0E}


@pytest.mark.parametrize("mode", ["read-back", "prep-seed"])
@pytest.mark.parametrize(
    "table", ["catalog.tb_note", "catalog.tb_tag"], ids=["no natural id", "a text id"]
)
def test_a_table_the_check_cannot_read_is_not_asked(mode: str, table: str) -> None:
    manager = ScenarioManager(
        SchemaFacts.from_source(UNCHECKABLE, table_codes=TableCodes(UNCHECKABLE_CODES))
    )
    connection = _Recording()
    manager._refuse_reapply(_scenario(mode, "catalog.tb_continent", table), connection)  # type: ignore[arg-type]
    assert [asked for asked in connection.asked if table.split(".")[1] in asked] == []
