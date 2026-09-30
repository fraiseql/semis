"""``semis apply``: a scenario written and applied in one transaction, committed on success."""

from collections.abc import Iterator
from pathlib import Path
from typing import LiteralString

import psycopg
import pytest
from typer.testing import CliRunner, Result

from fraiseql_semis.cli import app

SCHEMA = "semis_cli"
CONTINENT = f"{SCHEMA}.tb_continent"
COUNTRY = f"{SCHEMA}.tb_country"
LOCATION = f"{SCHEMA}.tb_location"
STAGING = f"{SCHEMA}_prep"
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
    fk_parent_location BIGINT REFERENCES {LOCATION} (pk_location),
    path LTREE,
    name VARCHAR(50) NOT NULL
);
CREATE SCHEMA {STAGING};
CREATE TABLE {STAGING}.tb_continent (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    name VARCHAR(50)
);
"""
SEMIS_YAML = f"""\
schema:
  ddl: schema.sql
prep_seed:
  prep_seed_schema: {STAGING}
table_codes:
  {CONTINENT}: 0x02030405
  {COUNTRY}: 0x03040506
  {LOCATION}: 0x05060708
"""
READ_BACK = f"""\
scenario_id: 0x5001
name: two_tables
mode: read-back
seed: 42
tables:
  - name: {COUNTRY}
    count: 4
  - name: {CONTINENT}
    count: 2
"""

runner = CliRunner()


@pytest.fixture
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS ltree")
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {STAGING} CASCADE")
        admin.execute(DDL)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {SCHEMA}, {STAGING} CASCADE")


def _apply(tmp_path: Path, database: str, scenario: str, *args: str) -> Result:
    (tmp_path / "schema.sql").write_text(DDL)
    (tmp_path / "semis.yaml").write_text(SEMIS_YAML)
    path = tmp_path / "scenario.yaml"
    path.write_text(scenario)
    config = ["-c", str(tmp_path / "semis.yaml"), "--database-url", database]
    return runner.invoke(app, ["apply", str(path), *config, *args])


def _count(database: str, table: LiteralString) -> int:
    with psycopg.connect(database) as connection:
        (count,) = connection.execute(f"SELECT count(*) FROM {table}").fetchone() or (None,)
    assert isinstance(count, int)
    return count


def test_apply_commits_a_read_back_run(database: str, tmp_path: Path) -> None:
    result = _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "out"))
    assert (result.exit_code, _count(database, CONTINENT), _count(database, COUNTRY)) == (0, 2, 4)


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
"""


def test_dry_run_rolls_back_and_reports_each_level(database: str, tmp_path: Path) -> None:
    result = _apply(tmp_path, database, LOCATIONS, "--dry-run")
    assert (result.exit_code, result.output, _count(database, LOCATION)) == (
        0,
        "scenario locations is unpinned: its schema is not checked\n"
        f"would apply 001_{LOCATION}.L1.sql  3 rows\n"
        f"would apply 002_{LOCATION}.L2.sql  6 rows\n"
        f"would apply 003_{LOCATION}.L3.sql  3 rows\n"
        "rolled back: nothing was written or applied\n",
        0,
    )


def test_apply_reports_each_file_the_pin_and_the_commit(database: str, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = _apply(tmp_path, database, READ_BACK, "-o", str(out))
    assert result.output.splitlines()[1:] == [
        f"applied 001_{CONTINENT}.sql  2 rows",
        f"applied 002_{COUNTRY}.sql    4 rows",
        f"wrote {out / 'schema_pin.yaml'}: copy it into the scenario to pin its schema",
        "committed",
    ]


def test_apply_writes_then_applies_a_prep_seed_scenario(database: str, tmp_path: Path) -> None:
    prep_seed = READ_BACK.replace("read-back", "prep-seed").replace(
        f"  - name: {COUNTRY}\n    count: 4\n", ""
    )
    result = _apply(tmp_path, database, prep_seed, "-o", str(tmp_path / "out"))
    staged = _count(database, f"{STAGING}.tb_continent")
    assert (result.exit_code, staged, _count(database, CONTINENT)) == (0, 2, 0)


def test_a_refused_table_rolls_back_every_table(database: str, tmp_path: Path) -> None:
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(f"ALTER TABLE {COUNTRY} ADD CHECK (iso_code = 'no')")
    result = _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "out"))
    assert (result.exit_code, "check constraint" in result.stderr) == (5, True)
    assert (_count(database, CONTINENT), _count(database, COUNTRY)) == (0, 0)


def test_generate_applies_a_read_back_scenario(database: str, tmp_path: Path) -> None:
    (tmp_path / "schema.sql").write_text(DDL)
    (tmp_path / "semis.yaml").write_text(SEMIS_YAML)
    table = ["table", CONTINENT, "--count=3", "--mode=read-back", "--scenario-id=0x5003"]
    options = ["-o", str(tmp_path / "out"), "-c", str(tmp_path / "semis.yaml"), "-d", database]
    result = runner.invoke(app, [*table, *options])
    assert (result.exit_code, _count(database, CONTINENT)) == (0, 3)
