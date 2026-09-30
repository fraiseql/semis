"""Hierarchies: a table whose foreign key points at itself, drawn breadth-first."""

from pathlib import Path

import pytest

from fraiseql_semis import emit
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ResolutionError, ScenarioError
from fraiseql_semis.generator import FakeDataGenerator, Row
from fraiseql_semis.hierarchy import Hierarchy, Paths
from fraiseql_semis.resolution import PrepSeedResolver
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import HIERARCHY, HIERARCHY_CODES

LOCATION = "catalog.tb_location"
TREE = Hierarchy(parent="fk_parent_location", roots=2, fan_out=3)
FACTS = SchemaFacts.from_source(HIERARCHY, table_codes=TableCodes(HIERARCHY_CODES))


def _prep_seed(
    facts: SchemaFacts, counts: dict[str, int], hierarchies: dict[str, Hierarchy]
) -> list[tuple[str, list[Row]]]:
    """A prep-seed walk, each batch remembered before the walk draws the next."""
    resolver = PrepSeedResolver()
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)
    batches: list[tuple[str, list[Row]]] = []
    for table, stream in generator.walk(counts, hierarchies=hierarchies, resolver=resolver):
        rows = list(stream)
        resolver.remember(table, rows)
        batches.append((table.ref.display, rows))
    return batches


def test_levels_are_breadth_first() -> None:
    tree = Hierarchy(parent="fk_parent_location", roots=2, fan_out=3)
    assert tree.levels(20) == [range(0, 2), range(2, 8), range(8, 20)]


def test_each_row_after_the_roots_has_an_earlier_parent() -> None:
    tree = Hierarchy(parent="fk_parent_location", roots=2, fan_out=3)
    assert [tree.parent_of(row) for row in range(10)] == [None, None, 0, 0, 0, 1, 1, 1, 2, 2]


def test_fewer_rows_than_roots_are_all_roots() -> None:
    tree = Hierarchy(parent="fk_parent_location", roots=5, fan_out=2)
    assert tree.levels(3) == [range(0, 3)]


def test_no_rows_is_one_empty_level() -> None:
    """A table of no rows is still walked once, as every other table is."""
    tree = Hierarchy(parent="fk_parent_location", roots=2, fan_out=3)
    assert tree.levels(0) == [range(0, 0)]


@pytest.mark.parametrize(("roots", "fan_out"), [(0, 3), (2, 0), (-1, 3), (True, 3), (2, 1.5)])
def test_roots_and_fan_out_below_one_are_refused(roots: int, fan_out: int) -> None:
    with pytest.raises(ScenarioError, match="fk_parent_location"):
        Hierarchy(parent="fk_parent_location", roots=roots, fan_out=fan_out)


def test_prep_seed_child_carries_its_parent_rows_uuid() -> None:
    rows = [
        row for _, level in _prep_seed(FACTS, {LOCATION: 20}, {LOCATION: TREE}) for row in level
    ]
    parents = [row["fk_parent_location"] for row in rows]
    expected = [None if (up := TREE.parent_of(k)) is None else rows[up]["id"] for k in range(20)]
    assert parents == expected


def _facts(ddl: str) -> SchemaFacts:
    return SchemaFacts.from_source(ddl, table_codes=TableCodes(HIERARCHY_CODES))


SECOND_SELF_FK = HIERARCHY.replace(
    "    path LTREE,",
    "    fk_generic_location BIGINT REFERENCES catalog.tb_location (pk_location),\n    path LTREE,",
)


def test_a_self_fk_without_a_hierarchy_is_refused_naming_the_column() -> None:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    # Refused when the walk is asked for, before any row is drawn.
    with pytest.raises(ScenarioError, match=r"catalog\.tb_location\.fk_parent_location"):
        generator.walk({LOCATION: 3}, resolver=PrepSeedResolver())
    with pytest.raises(ScenarioError, match="hierarchy"):
        generator.generate_rows(LOCATION, 3, resolver=PrepSeedResolver())


def test_a_not_null_parent_is_refused() -> None:
    facts = _facts(
        HIERARCHY.replace("fk_parent_location BIGINT", "fk_parent_location BIGINT NOT NULL")
    )
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)
    with pytest.raises(ResolutionError, match=r"fk_parent_location is NOT NULL.*root"):
        generator.walk({LOCATION: 3}, hierarchies={LOCATION: TREE})


def test_another_nullable_self_fk_is_null() -> None:
    batches = _prep_seed(_facts(SECOND_SELF_FK), {LOCATION: 8}, {LOCATION: TREE})
    rows = [row for _, level in batches for row in level]
    assert {row["fk_generic_location"] for row in rows} == {None}
    assert rows[-1]["fk_parent_location"] is not None


def test_another_not_null_self_fk_is_refused() -> None:
    facts = _facts(
        SECOND_SELF_FK.replace("fk_generic_location BIGINT", "fk_generic_location BIGINT NOT NULL")
    )
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)
    with pytest.raises(ResolutionError, match=r"fk_generic_location is NOT NULL"):
        generator.walk({LOCATION: 3}, hierarchies={LOCATION: TREE})


@pytest.mark.parametrize("parent", ["name", "fk_nowhere"])
def test_a_parent_that_is_no_self_fk_is_refused(parent: str) -> None:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    tree = Hierarchy(parent=parent, roots=1, fan_out=2)
    with pytest.raises(
        ScenarioError, match=rf"{parent} is not a foreign key to catalog\.tb_location"
    ):
        generator.walk({LOCATION: 3}, hierarchies={LOCATION: tree})


def test_a_child_level_without_a_resolver_is_refused() -> None:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    walk = generator.walk({LOCATION: 3}, hierarchies={LOCATION: TREE})
    list(next(walk)[1])  # the roots point at nothing
    with pytest.raises(ResolutionError, match="no resolver"):
        list(next(walk)[1])


def test_a_level_drawn_before_its_parents_are_remembered_is_refused() -> None:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    resolver = PrepSeedResolver()
    walk = generator.walk({LOCATION: 12}, hierarchies={LOCATION: TREE}, resolver=resolver)
    table, roots = next(walk)
    resolver.remember(table, list(roots))
    list(next(walk)[1])  # the second level, drawn and never remembered
    with pytest.raises(
        ResolutionError, match=r"row 3 of catalog\.tb_location, which is not remembered"
    ):
        list(next(walk)[1])


def test_paths_are_the_parent_path_then_the_own_key() -> None:
    paths = Paths(parent="fk_parent_location", natural_id="id")
    roots = [{"id": "a", "fk_parent_location": None}, {"id": "b", "fk_parent_location": None}]
    children = [{"id": "c", "fk_parent_location": 1}, {"id": "d", "fk_parent_location": 2}]
    grandchild = [{"id": "e", "fk_parent_location": 3}]
    assert paths.extend(roots, {"a": 1, "b": 2}) == {"a": "1", "b": "2"}
    assert paths.extend(children, {"c": 3, "d": 4}) == {"c": "1.3", "d": "2.4"}
    assert paths.extend(grandchild, {"e": 9}) == {"e": "1.3.9"}


PATHED = Hierarchy(parent="fk_parent_location", roots=2, fan_out=3, path="path")


def test_the_path_column_is_left_to_read_back() -> None:
    """semis sets the path once the keys exist; no provider draws it."""
    rows = [
        row for _, level in _prep_seed(FACTS, {LOCATION: 3}, {LOCATION: PATHED}) for row in level
    ]
    assert all("path" not in row for row in rows)


@pytest.mark.parametrize(
    ("ddl", "path", "reason"),
    [
        (HIERARCHY, "name", "is not an ltree column"),
        (HIERARCHY, "fk_nowhere", "is not a column semis writes"),
        (HIERARCHY.replace("path LTREE,", "path LTREE NOT NULL,"), "path", "is NOT NULL"),
        (HIERARCHY.replace("id UUID NOT NULL UNIQUE,", "ref TEXT,"), "path", "cannot be set"),
    ],
    ids=["not-ltree", "no-column", "not-null", "no-natural-id"],
)
def test_a_path_semis_cannot_set_is_refused(ddl: str, path: str, reason: str) -> None:
    generator = FakeDataGenerator(_facts(ddl), scenario_id=0x5001, seed=42)
    tree = Hierarchy(parent="fk_parent_location", roots=2, fan_out=3, path=path)
    with pytest.raises(ScenarioError, match=rf"catalog\.tb_location\.{path} {reason}"):
        generator.walk({LOCATION: 3}, hierarchies={LOCATION: tree})


def test_an_overridden_or_trusted_path_is_refused() -> None:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    with pytest.raises(ScenarioError, match=r"path is the hierarchy's path"):
        generator.walk(
            {LOCATION: 3}, hierarchies={LOCATION: PATHED}, overrides={LOCATION: {"path": "1"}}
        )
    with pytest.raises(ScenarioError, match=r"path is the hierarchy's path"):
        generator.walk(
            {LOCATION: 3}, hierarchies={LOCATION: PATHED}, trusted={LOCATION: frozenset({"path"})}
        )


def test_prep_seed_refuses_a_path_and_writes_nothing(tmp_path: Path) -> None:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    with pytest.raises(ScenarioError, match=r"prep-seed.*catalog\.tb_location\.path"):
        emit.prep_seed(generator, {LOCATION: 3}, tmp_path, hierarchies={LOCATION: PATHED})
    assert list(tmp_path.iterdir()) == []
