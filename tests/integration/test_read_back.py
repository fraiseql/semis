"""Read-back mode: parents applied, their keys learned, children pointed at them."""

from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path

import psycopg
import pytest
from confiture.platform import SeedError

from fraiseql_semis import (
    FakeDataGenerator,
    ResolutionError,
    SchemaFacts,
    TableCodes,
    emit,
    readback,
)
from fraiseql_semis.schema import TableFacts

SCHEMA = "semis_rb"
CONTINENT = f"{SCHEMA}.tb_continent"
COUNTRY = f"{SCHEMA}.tb_country"
DDL = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {CONTINENT} (
    pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL
);
CREATE TABLE {COUNTRY} (
    pk_country BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_continent BIGINT NOT NULL REFERENCES {CONTINENT} (pk_continent),
    iso_code CHAR(2) NOT NULL
);
"""
FACTS = SchemaFacts.from_source(
    DDL, table_codes=TableCodes({CONTINENT: 0x02030405, COUNTRY: 0x03040506})
)


@pytest.fixture
def schema(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        connection.execute(DDL)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


@pytest.fixture
def connection(schema: str) -> Iterator[psycopg.Connection]:
    """The caller's connection: its transaction is left open for the test to inspect."""
    with psycopg.connect(schema) as connection:
        yield connection
        connection.rollback()


def _read_back(connection: psycopg.Connection, out: Path, counts: dict[str, int]) -> None:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    emit.read_back(connection, generator, counts, out)


def test_children_carry_learned_integers(connection: psycopg.Connection, tmp_path: Path) -> None:
    _read_back(connection, tmp_path, {CONTINENT: 2, COUNTRY: 6})
    parents = {pk for (pk,) in connection.execute(f"SELECT pk_continent FROM {CONTINENT}")}
    children = [fk for (fk,) in connection.execute(f"SELECT fk_continent FROM {COUNTRY}")]
    assert len(parents) == 2
    assert len(children) == 6
    assert set(children) <= parents


def test_children_are_spread_over_parents(connection: psycopg.Connection, tmp_path: Path) -> None:
    _read_back(connection, tmp_path, {CONTINENT: 2, COUNTRY: 6})
    spread = connection.execute(
        f"SELECT p.id, count(c.pk_country) FROM {CONTINENT} p"
        f" LEFT JOIN {COUNTRY} c ON c.fk_continent = p.pk_continent GROUP BY p.id"
    ).fetchall()
    assert sorted(count for _, count in spread) == [3, 3]


def test_the_shape_is_prep_seeds(connection: psycopg.Connection, tmp_path: Path) -> None:
    """Child k points at parent k mod n in both modes: the join is on the UUID (§7)."""
    _read_back(connection, tmp_path, {CONTINENT: 2, COUNTRY: 4})
    pointed = [
        parent
        for (parent,) in connection.execute(
            f"SELECT p.id FROM {COUNTRY} c JOIN {CONTINENT} p"
            " ON c.fk_continent = p.pk_continent ORDER BY c.id"
        )
    ]
    parents = [uuid for (uuid,) in connection.execute(f"SELECT id FROM {CONTINENT} ORDER BY id")]
    assert pointed == parents * 2


@pytest.mark.parametrize("children", [6, 60])
def test_each_parent_is_learned_once_whatever_its_children(
    connection: psycopg.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    children: int,
) -> None:
    """One read per table, with that table's ids: ten times the children, no more reads."""
    learned: list[tuple[str, list[object]]] = []
    learn = readback.learn

    def counted(
        connection: psycopg.Connection, table: TableFacts, uuids: Sequence[object]
    ) -> dict[object, int]:
        learned.append((table.ref.display, list(uuids)))
        return learn(connection, table, uuids)

    monkeypatch.setattr(readback, "learn", counted)
    _read_back(connection, tmp_path, {CONTINENT: 2, COUNTRY: children})
    parents = [uuid for (uuid,) in connection.execute(f"SELECT id FROM {CONTINENT}")]
    assert [table for table, _ in learned] == [CONTINENT, COUNTRY]
    assert Counter(learned[0][1]) == Counter(parents)


def test_no_parent_rows_raises_resolution_error(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    with pytest.raises(
        ResolutionError, match=rf"{COUNTRY}\.fk_continent references {CONTINENT}, which has no rows"
    ):
        _read_back(connection, tmp_path, {CONTINENT: 0, COUNTRY: 2})


def test_a_failure_rolls_back_every_table(
    connection: psycopg.Connection, schema: str, tmp_path: Path
) -> None:
    """The children fail on apply; the caller's rollback takes the parents with them."""
    connection.execute(f"ALTER TABLE {COUNTRY} ADD CHECK (iso_code = 'ZZ')")
    connection.commit()
    with pytest.raises(SeedError, match="check constraint"):
        _read_back(connection, tmp_path, {CONTINENT: 2, COUNTRY: 2})
    connection.rollback()
    with psycopg.connect(schema) as fresh:
        counts = [
            fresh.execute(f"SELECT count(*) FROM {table}").fetchone()
            for table in (CONTINENT, COUNTRY)
        ]
    assert counts == [(0,), (0,)]
