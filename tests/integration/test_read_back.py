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
    ScenarioManager,
    SchemaFacts,
    SchemaNotBuiltError,
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
CODES = TableCodes({CONTINENT: 0x02030405, COUNTRY: 0x03040506})
FACTS = SchemaFacts.from_source(DDL, table_codes=CODES)
# The same tables with a nullable fk_continent, as a test's ALTER makes them.
OPTIONAL = SchemaFacts.from_source(
    DDL.replace("fk_continent BIGINT NOT NULL", "fk_continent BIGINT"), table_codes=CODES
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


def test_a_nullable_key_without_a_parent_is_applied_null(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    connection.execute(f"ALTER TABLE {COUNTRY} ALTER fk_continent DROP NOT NULL")
    generator = FakeDataGenerator(OPTIONAL, scenario_id=0x5001, seed=42)
    emit.read_back(connection, generator, {COUNTRY: 3}, tmp_path)
    keys = [fk for (fk,) in connection.execute(f"SELECT fk_continent FROM {COUNTRY}")]
    assert keys == [None, None, None]


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


SEEDED = f"""
INSERT INTO {CONTINENT} (id, identifier, name) VALUES
    (gen_random_uuid(), 'eu', 'Europe'),
    (gen_random_uuid(), 'as', 'Asia'),
    (gen_random_uuid(), 'af', 'Africa');
"""
COUNTRIES = f"""\
scenario_id: 0x5003
name: countries
mode: read-back
seed: 42
tables:
  - name: {COUNTRY}
    count: 10
existing:
  - name: {CONTINENT}
"""


def _countries(connection: psycopg.Connection, tmp_path: Path, text: str = COUNTRIES) -> list[str]:
    """The run applied, then each country's continent by identifier, in the order drawn."""
    connection.execute(SEEDED)
    path = tmp_path / "countries.yaml"
    path.write_text(text)
    manager = ScenarioManager(FACTS)
    manager.execute(manager.load(path), tmp_path / "out", connection=connection)
    return [
        identifier
        for (identifier,) in connection.execute(
            f"SELECT c.identifier FROM {COUNTRY} AS k JOIN {CONTINENT} AS c"
            " ON c.pk_continent = k.fk_continent ORDER BY k.id"
        )
    ]


def test_children_point_at_existing_rows_round_robin(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    """In surrogate-key order: the rows the schema inserted, never generated again."""
    assert _countries(connection, tmp_path) == ["eu", "as", "af"] * 3 + ["eu"]
    (count,) = connection.execute(f"SELECT count(*) FROM {CONTINENT}").fetchone() or (0,)
    assert count == 3


def test_where_narrows_and_orders_the_existing_rows(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    text = COUNTRIES + "    where: {identifier: [af, eu]}\n"
    assert _countries(connection, tmp_path, text) == ["af", "eu"] * 5


@pytest.mark.parametrize(
    ("where", "match"),
    [
        ("", rf"scenario countries: existing {CONTINENT} holds no rows"),
        (
            "    where: {identifier: [eu, oc]}\n",
            rf"scenario countries: existing {CONTINENT} has no row whose identifier is 'oc'",
        ),
    ],
    ids=["no-rows", "unmatched"],
)
def test_existing_rows_that_are_not_there_are_refused(
    connection: psycopg.Connection, tmp_path: Path, where: str, match: str
) -> None:
    path = tmp_path / "countries.yaml"
    path.write_text(COUNTRIES + where)
    if where:
        connection.execute(SEEDED)
    manager = ScenarioManager(FACTS)
    with pytest.raises(ResolutionError, match=match):
        manager.execute(manager.load(path), tmp_path / "out", connection=connection)


def test_an_empty_existing_table_is_refused_for_a_nullable_key_too(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    """Listing a table under existing: asks for its rows; it is not a parent left out."""
    connection.execute(f"ALTER TABLE {COUNTRY} ALTER fk_continent DROP NOT NULL")
    path = tmp_path / "countries.yaml"
    path.write_text(COUNTRIES)
    manager = ScenarioManager(OPTIONAL)
    with pytest.raises(
        ResolutionError, match=rf"^scenario countries: existing {CONTINENT} holds no rows"
    ):
        manager.execute(manager.load(path), tmp_path / "out", connection=connection)


@pytest.mark.parametrize("where", ["", "    where: {identifier: [eu]}\n"], ids=["all", "where"])
def test_an_existing_table_the_database_does_not_hold_is_refused(
    connection: psycopg.Connection, tmp_path: Path, where: str
) -> None:
    connection.execute(f"DROP TABLE {CONTINENT} CASCADE")
    path = tmp_path / "countries.yaml"
    path.write_text(COUNTRIES + where)
    manager = ScenarioManager(FACTS)
    with pytest.raises(
        SchemaNotBuiltError, match=rf"^scenario countries: the database holds no {CONTINENT}\n"
    ):
        manager.execute(manager.load(path), tmp_path / "out", connection=connection)
