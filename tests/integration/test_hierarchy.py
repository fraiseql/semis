"""Read-back hierarchies: each level applied and learned before the next points at it."""

from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path

import psycopg
import pytest
from confiture.platform import SeedError

from fraiseql_semis import FakeDataGenerator, Hierarchy, SchemaFacts, TableCodes, emit, readback
from fraiseql_semis.schema import TableFacts
from tests.ddl import HIERARCHY

SCHEMA = "semis_tree"
LOCATION = f"{SCHEMA}.tb_location"
STAGING = f"{SCHEMA}_prep"
DDL = HIERARCHY.replace("catalog", SCHEMA).replace("prep_seed", STAGING)
FACTS = SchemaFacts.from_source(DDL, table_codes=TableCodes({LOCATION: 0x05060708}))
TREE = Hierarchy(parent="fk_parent_location", roots=2, fan_out=3)


@pytest.fixture
def schema(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS ltree")
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {STAGING} CASCADE")
        connection.execute(DDL)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA {SCHEMA}, {STAGING} CASCADE")


@pytest.fixture
def connection(schema: str) -> Iterator[psycopg.Connection]:
    """The caller's connection: its transaction is left open for the test to inspect."""
    with psycopg.connect(schema) as connection:
        yield connection
        connection.rollback()


def _read_back(
    connection: psycopg.Connection, out: Path, count: int, tree: Hierarchy = TREE
) -> list[Path]:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    written = emit.read_back(
        connection, generator, {LOCATION: count}, out, hierarchies={LOCATION: tree}
    )
    return [seed.path for seed in written]


def test_each_level_points_at_the_one_before(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    paths = _read_back(connection, tmp_path, 20)
    # In the order semis drew them: the encoded UUID carries the row's sequence.
    rows = connection.execute(
        f"SELECT pk_location, fk_parent_location FROM {LOCATION} ORDER BY id"
    ).fetchall()
    keys = [pk for pk, _ in rows]
    expected = [None if (up := TREE.parent_of(k)) is None else keys[up] for k in range(20)]
    assert [parent for _, parent in rows] == expected
    assert [path.name for path in paths] == [
        f"001_{LOCATION}.L1.sql",
        f"002_{LOCATION}.L2.sql",
        f"003_{LOCATION}.L3.sql",
    ]


def test_each_row_is_learned_once_by_its_level(
    connection: psycopg.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One read per level, with that level's ids: no row is read back twice."""
    learned: list[list[object]] = []
    learn = readback.learn

    def counted(
        connection: psycopg.Connection, table: TableFacts, uuids: Sequence[object]
    ) -> dict[object, int]:
        learned.append(list(uuids))
        return learn(connection, table, uuids)

    monkeypatch.setattr(readback, "learn", counted)
    _read_back(connection, tmp_path, 20)
    rows = [uuid for (uuid,) in connection.execute(f"SELECT id FROM {LOCATION}")]
    assert [len(level) for level in learned] == [2, 6, 12]
    assert Counter(uuid for level in learned for uuid in level) == Counter(rows)


def test_a_failure_rolls_back_every_level(connection: psycopg.Connection, tmp_path: Path) -> None:
    # PostgreSQL refuses the third level, once the first two are applied and learned.
    connection.execute(f"ALTER TABLE {LOCATION} ADD CHECK (pk_location <= 8)")
    with pytest.raises(SeedError, match="check constraint"):
        _read_back(connection, tmp_path, 20)
    applied = connection.execute(f"SELECT count(*) FROM {LOCATION}").fetchone()
    connection.rollback()
    left = connection.execute(f"SELECT count(*) FROM {LOCATION}").fetchone()
    assert (applied, left) == ((8,), (0,))


def test_path_is_parent_path_then_own_pk(connection: psycopg.Connection, tmp_path: Path) -> None:
    """The format a tree-path recalculation writes: pk_* labels, root first."""
    _read_back(connection, tmp_path, 20, Hierarchy("fk_parent_location", 2, 3, path="path"))
    rows = connection.execute(
        f"SELECT pk_location, fk_parent_location, path::text FROM {LOCATION} ORDER BY id"
    ).fetchall()
    paths = {pk: path for pk, _, path in rows}
    assert all(path == (f"{paths[up]}.{pk}" if up else f"{pk}") for pk, up, path in rows)
    assert [paths[pk].count(".") for pk, _, _ in rows] == [0] * 2 + [1] * 6 + [2] * 12
