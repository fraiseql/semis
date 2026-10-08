"""Parents and children: the order a run walks, and the value a foreign key receives."""

from collections.abc import Iterable

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ResolutionError
from fraiseql_semis.generator import FakeDataGenerator, Row
from fraiseql_semis.resolution import PrepSeedResolver, ReadBackResolver, require_parents
from fraiseql_semis.schema import (
    ColumnFacts,
    DependencyCycleError,
    ObjectRef,
    SchemaFacts,
    TableFacts,
    TableKeys,
)
from tests.ddl import CODES, TRINITY

FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))


def _prep_seed_run(facts: SchemaFacts, counts: dict[str, int]) -> dict[str, list[Row]]:
    """A prep-seed walk, the resolver told each table's rows as the walk passes them."""
    resolver = PrepSeedResolver()
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)
    run: dict[str, list[Row]] = {}
    for table, stream in generator.walk(counts, resolver=resolver):
        rows = list(stream)
        resolver.remember(table, rows)
        run[table.ref.display] = rows
    return run


def test_parents_come_before_children() -> None:
    run = _prep_seed_run(FACTS, {"catalog.tb_country": 2, "catalog.tb_continent": 2})
    assert list(run) == ["catalog.tb_continent", "catalog.tb_country"]


CYCLE = """
CREATE SCHEMA catalog;
CREATE TABLE catalog.tb_egg (
    pk_egg BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    fk_hen BIGINT NOT NULL
);
CREATE TABLE catalog.tb_hen (
    pk_hen BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    fk_egg BIGINT NOT NULL REFERENCES catalog.tb_egg (pk_egg)
);
ALTER TABLE catalog.tb_egg ADD FOREIGN KEY (fk_hen) REFERENCES catalog.tb_hen (pk_hen);
"""


def test_a_cycle_raises_dependency_cycle_naming_the_tables() -> None:
    facts = SchemaFacts.from_source(
        CYCLE, table_codes=TableCodes({"catalog.tb_egg": 0x0A, "catalog.tb_hen": 0x0B})
    )
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)
    # Refused when the walk is asked for, before any table is generated.
    with pytest.raises(DependencyCycleError) as refusal:
        generator.walk({"catalog.tb_egg": 1, "catalog.tb_hen": 1})
    assert [ref.display for ref in refusal.value.tables] == ["catalog.tb_egg", "catalog.tb_hen"]


def test_the_order_is_taken_once_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
    taken: list[Iterable[str] | None] = []
    insert_order = facts.insert_order

    def spy(tables: Iterable[str] | None = None) -> list[ObjectRef]:
        taken.append(tables)
        return insert_order(tables)

    monkeypatch.setattr(facts, "insert_order", spy)
    _prep_seed_run(facts, {"catalog.tb_country": 2, "catalog.tb_continent": 2})
    assert len(taken) == 1


def test_prep_seed_fk_carries_the_parents_uuid() -> None:
    run = _prep_seed_run(FACTS, {"catalog.tb_continent": 2, "catalog.tb_country": 4})
    parents = [row["id"] for row in run["catalog.tb_continent"]]
    assert [row["fk_continent"] for row in run["catalog.tb_country"]] == parents * 2


UNCONVENTIONAL = """
CREATE SCHEMA catalog;
CREATE TABLE catalog.tb_continent (
    pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE
);
CREATE TABLE catalog.tb_country (
    pk_country BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    parent_ref BIGINT NOT NULL REFERENCES catalog.tb_continent (pk_continent),
    fk_continent BIGINT NOT NULL
);
"""


def test_resolver_never_reads_a_naming_convention() -> None:
    """The FK is the declared one, whatever it is called; a name alone makes none."""
    facts = SchemaFacts.from_source(UNCONVENTIONAL, table_codes=TableCodes(CODES))
    run = _prep_seed_run(facts, {"catalog.tb_continent": 2, "catalog.tb_country": 4})
    parents = [row["id"] for row in run["catalog.tb_continent"]]
    children = run["catalog.tb_country"]
    assert [row["parent_ref"] for row in children] == parents * 2
    assert all(isinstance(row["fk_continent"], int) for row in children)


def test_a_parent_outside_the_run_is_refused_naming_column_and_parent() -> None:
    with pytest.raises(
        ResolutionError,
        match=r"catalog\.tb_country\.fk_continent references catalog\.tb_continent",
    ):
        _prep_seed_run(FACTS, {"catalog.tb_country": 1})


OPTIONAL = SchemaFacts.from_source(
    TRINITY.replace("fk_continent BIGINT NOT NULL REFERENCES", "fk_continent BIGINT REFERENCES"),
    table_codes=TableCodes(CODES),
)


def test_a_nullable_key_whose_parent_has_no_rows_needs_none() -> None:
    require_parents(OPTIONAL.facts_for("catalog.tb_country"), {"catalog.tb_country": 2})


def test_a_nullable_key_whose_parent_is_outside_the_run_is_drawn_null() -> None:
    run = _prep_seed_run(OPTIONAL, {"catalog.tb_country": 2})
    assert [row["fk_continent"] for row in run["catalog.tb_country"]] == [None, None]


def test_a_foreign_key_without_a_resolver_is_refused() -> None:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    with pytest.raises(ResolutionError, match=r"catalog\.tb_country\.fk_continent"):
        list(generator.generate_rows("catalog.tb_country", count=1))


def test_a_column_that_is_no_foreign_key_is_not_resolved() -> None:
    name = FACTS.facts_for("catalog.tb_continent").columns[-1]
    with pytest.raises(ValueError, match=r"catalog\.tb_continent\.name is not a foreign key"):
        PrepSeedResolver().value_for(name, table="catalog.tb_continent")


def _continents(count: int) -> tuple[TableFacts, list[Row]]:
    run = _prep_seed_run(FACTS, {"catalog.tb_continent": count})
    return FACTS.facts_for("catalog.tb_continent"), run["catalog.tb_continent"]


def _fk_continent() -> ColumnFacts:
    columns = FACTS.facts_for("catalog.tb_country").columns
    return next(column for column in columns if column.name == "fk_continent")


def test_a_resolver_told_of_no_parent_refuses_the_child() -> None:
    """The walk refuses first; a caller driving a resolver itself is refused too."""
    with pytest.raises(ResolutionError, match=r"catalog\.tb_continent, which has no rows"):
        PrepSeedResolver().value_for(_fk_continent(), table="catalog.tb_country")


def test_a_resolver_told_of_an_empty_parent_refuses_the_child() -> None:
    resolver = PrepSeedResolver()
    resolver.remember(FACTS.facts_for("catalog.tb_continent"), [])
    with pytest.raises(ResolutionError, match=r"catalog\.tb_continent, which has no rows"):
        resolver.value_for(_fk_continent(), table="catalog.tb_country")


def test_read_back_fk_carries_the_learned_key_in_turn() -> None:
    continents, rows = _continents(2)
    resolver = ReadBackResolver()
    resolver.remember(continents, rows, {rows[0]["id"]: 7, rows[1]["id"]: 9})
    turns = [resolver.value_for(_fk_continent(), table="catalog.tb_country") for _ in range(4)]
    assert turns == [7, 9, 7, 9]


def test_a_partial_read_back_is_refused_with_the_counts() -> None:
    continents, rows = _continents(3)
    with pytest.raises(ResolutionError, match=r"catalog\.tb_continent: 3 rows were written and 1"):
        ReadBackResolver().remember(continents, rows, {rows[0]["id"]: 7})


def test_read_back_refuses_a_foreign_key_to_another_column() -> None:
    ddl = """
    CREATE SCHEMA catalog;
    CREATE TABLE catalog.tb_continent (
        pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        id UUID NOT NULL UNIQUE,
        code TEXT NOT NULL UNIQUE
    );
    CREATE TABLE catalog.tb_country (
        id UUID NOT NULL UNIQUE,
        continent_code TEXT NOT NULL REFERENCES catalog.tb_continent (code)
    );
    """
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES))
    continents = facts.facts_for("catalog.tb_continent")
    code = facts.facts_for("catalog.tb_country").columns[-1]
    row = {"id": "a", "code": "x"}
    resolver = ReadBackResolver()
    resolver.remember(continents, [row], {"a": 1})
    with pytest.raises(
        ResolutionError,
        match=r"catalog\.tb_country\.continent_code references catalog\.tb_continent\.code",
    ):
        resolver.value_for(code, table="catalog.tb_country")


def test_a_parent_without_a_natural_id_is_refused_as_such() -> None:
    ddl = """
    CREATE SCHEMA catalog;
    CREATE TABLE catalog.tb_continent (pk_continent BIGINT PRIMARY KEY, name TEXT);
    CREATE TABLE catalog.tb_country (
        id UUID NOT NULL UNIQUE,
        fk_continent BIGINT NOT NULL REFERENCES catalog.tb_continent (pk_continent)
    );
    """
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES))
    with pytest.raises(ResolutionError, match=r"catalog\.tb_continent, which shows no natural id"):
        _prep_seed_run(facts, {"catalog.tb_continent": 2, "catalog.tb_country": 1})


def test_an_existing_parent_needs_no_natural_id() -> None:
    """Its keys are read, not learned by natural id: children spread over them."""
    resolver = ReadBackResolver()
    continent = FACTS.keys_for("catalog.tb_continent")
    resolver.existing(TableKeys(continent.ref, continent.surrogate_pk, None), [7, 9])
    column = next(
        c for c in FACTS.facts_for("catalog.tb_country").columns if c.name == "fk_continent"
    )
    assert [resolver.value_for(column, table="catalog.tb_country") for _ in range(3)] == [7, 9, 7]
