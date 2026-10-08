"""A scenario file, executed: its tables walked in order, in its declared mode."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from fraiseql_semis import IncomparablePinError, PinError, ScenarioManager, SchemaFacts, TableCodes

SCHEMA = "semis_scn"
CONTINENT = f"{SCHEMA}.tb_continent"
COUNTRY = f"{SCHEMA}.tb_country"
LOCATION = f"{SCHEMA}.tb_location"
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
CREATE TABLE {LOCATION} (
    pk_location BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_continent BIGINT NOT NULL REFERENCES {CONTINENT} (pk_continent),
    fk_parent_location BIGINT REFERENCES {LOCATION} (pk_location),
    path LTREE,
    name VARCHAR(50) NOT NULL
);
"""
CODES = TableCodes({CONTINENT: 0x02030405, COUNTRY: 0x03040506, LOCATION: 0x05060708})
SCENARIO = f"""\
scenario_id: 0x5001
name: two_tables
mode: read-back
seed: 42
tables:
  - name: {COUNTRY}
    count: 4
  - name: {CONTINENT}
    count: 2
    overrides:
      name: [Africa, Europe]
"""


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS ltree")
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(DDL)
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


def _scenario(tmp_path: Path) -> Path:
    path = tmp_path / "two_tables.yaml"
    path.write_text(SCENARIO)
    return path


def test_minimal_scenario_seeds_two_tables(connection: psycopg.Connection, tmp_path: Path) -> None:
    manager = ScenarioManager(SchemaFacts.from_source(DDL, table_codes=CODES))
    manager.execute(manager.load(_scenario(tmp_path)), tmp_path / "out", connection=connection)
    continents = connection.execute(f"SELECT pk_continent, name FROM {CONTINENT}").fetchall()
    countries = connection.execute(f"SELECT fk_continent FROM {COUNTRY}").fetchall()
    assert sorted(name for _, name in continents) == ["Africa", "Europe"]
    assert sorted(fk for (fk,) in countries) == sorted([pk for pk, _ in continents] * 2)


def _live(connection: psycopg.Connection) -> ScenarioManager:
    return ScenarioManager(
        SchemaFacts.from_database(connection, schemas=[SCHEMA], table_codes=CODES)
    )


def test_a_live_pin_names_the_column_that_moved(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    """A database: source keeps no DDL, and its refusal still names what moved."""
    connection.execute(f"ALTER TABLE {COUNTRY} ADD COLUMN note TEXT")
    manager = _live(connection)
    path = _scenario(tmp_path)
    taken = manager.pin(manager.load(path), path)
    assert taken.pin.source == "live"
    connection.rollback()
    connection.execute(f"ALTER TABLE {COUNTRY} ADD COLUMN note TEXT")
    connection.execute(f"ALTER TABLE {COUNTRY} ALTER note SET NOT NULL")
    moved = _live(connection)
    with pytest.raises(PinError, match=rf"{COUNTRY}\.note: not_null false → true"):
        moved.execute(moved.load(path), tmp_path / "out", connection=connection)


def test_a_ddl_pin_replayed_against_a_live_schema_is_incomparable(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    ddl = ScenarioManager(SchemaFacts.from_source(DDL, table_codes=CODES))
    path = _scenario(tmp_path)
    ddl.pin(ddl.load(path), path)
    live = ScenarioManager(
        SchemaFacts.from_database(connection, schemas=[SCHEMA], table_codes=CODES)
    )
    with pytest.raises(IncomparablePinError, match="pinned against a ddl schema"):
        live.execute(live.load(path), tmp_path / "out", connection=connection)


LOCATIONS = f"""\
scenario_id: 0x5002
name: locations
mode: read-back
seed: 42
tables:
  - name: {LOCATION}
    count: 12  # levels of 3, 6 and 3
    hierarchy:
      parent: fk_parent_location
      roots: 3
      fan_out: 2
      path: path
  - name: {CONTINENT}
    count: 2
"""


def test_a_hierarchy_scenario_seeds_a_pk_labelled_tree(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    scenario = tmp_path / "locations.yaml"
    scenario.write_text(LOCATIONS)
    manager = ScenarioManager(SchemaFacts.from_source(DDL, table_codes=CODES))
    run = manager.execute(manager.load(scenario), tmp_path / "out", connection=connection)
    assert [seed.path.name for seed in run.seeds] == [
        f"001_{CONTINENT}.sql",
        f"002_{LOCATION}.L1.sql",
        f"003_{LOCATION}.L2.sql",
        f"004_{LOCATION}.L3.sql",
    ]
    rows = connection.execute(
        f"SELECT pk_location, fk_parent_location, path::text FROM {LOCATION} ORDER BY id"
    ).fetchall()
    paths = {pk: path for pk, _, path in rows}
    assert [up is None for _, up, _ in rows] == [True] * 3 + [False] * 9
    assert all(path == (f"{paths[up]}.{pk}" if up else f"{pk}") for pk, up, path in rows)
