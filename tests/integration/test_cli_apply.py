"""``semis apply``: a scenario written and applied in one transaction, committed on success."""

import shlex
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import LiteralString

import psycopg
import pytest
from typer.testing import CliRunner, Result

from fraiseql_semis import readback
from fraiseql_semis.cli import app
from fraiseql_semis.project import Project
from fraiseql_semis.scenario import Scenario, ScenarioManager

SCHEMA = "semis_cli"
CONTINENT = f"{SCHEMA}.tb_continent"
COUNTRY = f"{SCHEMA}.tb_country"
LOCATION = f"{SCHEMA}.tb_location"
CUSTOMER = f"{SCHEMA}.tb_customer"
TAG = f"{SCHEMA}.tb_tag"
STAGING = f"{SCHEMA}_prep"
RESET = f'TRUNCATE "{SCHEMA}"."tb_continent", "{SCHEMA}"."tb_country" RESTART IDENTITY'
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
CREATE TABLE {CUSTOMER} (
    pk_customer BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    deleted_at TIMESTAMPTZ,
    created_by UUID,
    note TEXT
);
CREATE TABLE {TAG} (
    pk_tag BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL
);
CREATE SCHEMA {STAGING};
CREATE TABLE {STAGING}.tb_tag (id TEXT NOT NULL UNIQUE, identifier TEXT NOT NULL, label TEXT);
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
  {CUSTOMER}: 0x06070809
  {TAG}: 0x07080900
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


@pytest.fixture
def unbuilt(database_url: str) -> str:
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {STAGING} CASCADE")
    return database_url


@pytest.mark.parametrize("mode", ["read-back", "prep-seed"])
def test_a_database_without_the_schema_is_refused_naming_the_table(
    unbuilt: str, tmp_path: Path, mode: str
) -> None:
    result = _apply(
        tmp_path, unbuilt, READ_BACK.replace("read-back", mode), "-o", str(tmp_path / "out")
    )
    assert (result.exit_code, result.exception.__class__) == (1, SystemExit)
    assert result.stderr.splitlines() == [
        f"scenario two_tables: the database holds no {CONTINENT}",
        "Hint: Build the schema in this database, then apply the scenario.",
    ]


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


CUSTOMERS = f"""\
scenario_id: 0x5004
name: customers
mode: read-back
seed: 42
tables:
  - name: {CUSTOMER}
    count: 3
    fill: [note]
"""


def test_apply_says_which_columns_it_left_null(database: str, tmp_path: Path) -> None:
    """One line per table, so a nullable column left empty is never a surprise."""
    result = _apply(tmp_path, database, CUSTOMERS, "--dry-run")
    assert result.output.splitlines()[:2] == [
        "scenario customers is unpinned: its schema is not checked",
        f"{CUSTOMER} leaves deleted_at, created_by NULL; fill: draws them",
    ]


def test_a_second_apply_is_refused_before_it_writes(database: str, tmp_path: Path) -> None:
    """A scenario applies once to a reset database: a re-run names the reset, and the
    database keeps exactly the first run's rows."""
    first = _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "first"))
    again = _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "again"))
    assert (first.exit_code, again.exit_code) == (0, 1)
    assert again.stderr.splitlines() == [
        f"scenario two_tables is already applied: {CONTINENT} holds its rows",
        "Hint: A scenario applies once, to a reset database. Reset it with "
        f"psql -c '{RESET}', "
        "or recreate the database, then apply again.",
    ]
    assert not (tmp_path / "again").exists()
    assert (_count(database, CONTINENT), _count(database, COUNTRY)) == (2, 4)


def test_a_dry_run_reports_the_same_refusal(database: str, tmp_path: Path) -> None:
    _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "first"))
    again = _apply(tmp_path, database, READ_BACK, "--dry-run")
    assert (again.exit_code, again.stderr.splitlines()) == (
        1,
        [
            f"scenario two_tables is already applied: {CONTINENT} holds its rows",
            "Hint: A scenario applies once, to a reset database. Reset it with "
            f"psql -c '{RESET}', "
            "or recreate the database, then apply again.",
        ],
    )


def test_another_scenario_id_applies_beside_the_first(database: str, tmp_path: Path) -> None:
    """The range is the scenario's: a run under another id is not a re-apply."""
    _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "first"))
    other = _apply(
        tmp_path, database, READ_BACK, "-o", str(tmp_path / "b"), "--scenario-id", "0x5009"
    )
    assert (other.exit_code, _count(database, CONTINENT)) == (0, 4)


def test_a_prep_seed_reapply_is_found_in_the_staging_twin(database: str, tmp_path: Path) -> None:
    prep_seed = READ_BACK.replace("read-back", "prep-seed").replace(
        f"  - name: {COUNTRY}\n    count: 4\n", ""
    )
    _apply(tmp_path, database, prep_seed, "-o", str(tmp_path / "first"))
    again = _apply(tmp_path, database, prep_seed, "-o", str(tmp_path / "again"))
    assert (again.exit_code, again.stderr.splitlines()) == (
        1,
        [
            f"scenario two_tables is already applied: {STAGING}.tb_continent holds its rows",
            "Hint: A scenario applies once, to a reset database. Reset it with "
            f'psql -c \'TRUNCATE "{SCHEMA}"."tb_continent", "{STAGING}"."tb_continent" '
            "RESTART IDENTITY', "
            "or recreate the database, then apply again.",
        ],
    )


def _printed_reset(result: Result) -> str:
    """The statement the refusal's hint tells the reader to run with ``psql -c``."""
    hint = result.stderr.splitlines()[1]
    quoted = hint.split("psql -c ", 1)[1].rsplit(", or recreate the database", 1)[0]
    (statement,) = shlex.split(quoted)
    return statement


def test_the_printed_reset_lets_the_scenario_apply_again(database: str, tmp_path: Path) -> None:
    """Run as printed, the reset empties the run's tables and restarts their keys at 1."""
    _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "first"))
    again = _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "again"))
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(_printed_reset(again).encode())
    third = _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "third"))
    with psycopg.connect(database) as connection:
        keys = connection.execute(f"SELECT min(pk_continent), count(*) FROM {CONTINENT}").fetchone()
    assert (third.exit_code, keys) == (0, (1, 2))


def test_the_reset_meets_a_foreign_key_from_outside_the_run(database: str, tmp_path: Path) -> None:
    """No CASCADE: PostgreSQL refuses to empty a table another table references, and
    empties nothing."""
    _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "first"))
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            f"CREATE TABLE {SCHEMA}.tb_outside (fk_continent BIGINT REFERENCES {CONTINENT})"
        )
    again = _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "again"))
    with (
        psycopg.connect(database, autocommit=True) as connection,
        pytest.raises(psycopg.errors.FeatureNotSupported) as refused,
    ):
        connection.execute(_printed_reset(again).encode())
    assert (refused.value.diag.message_primary, refused.value.diag.message_hint) == (
        "cannot truncate a table referenced in a foreign key constraint",
        'Truncate table "tb_outside" at the same time, or use TRUNCATE ... CASCADE.',
    )
    assert (_count(database, CONTINENT), _count(database, COUNTRY)) == (2, 4)


def test_a_prep_seed_table_whose_id_is_text_applies(database: str, tmp_path: Path) -> None:
    """0.1.0 applied such a table; the check skips it, since its range is uuids."""
    tags = READ_BACK.replace("read-back", "prep-seed").replace(
        f"  - name: {COUNTRY}\n    count: 4\n  - name: {CONTINENT}\n    count: 2\n",
        f"  - name: {TAG}\n    count: 3\n",
    )
    result = _apply(tmp_path, database, tags, "-o", str(tmp_path / "out"))
    assert (result.exit_code, _count(database, f"{STAGING}.tb_tag")) == (0, 3)


def _held_apply(tmp_path: Path, database: str) -> tuple[ScenarioManager, Scenario]:
    (tmp_path / "schema.sql").write_text(DDL)
    (tmp_path / "semis.yaml").write_text(SEMIS_YAML)
    (tmp_path / "held.yaml").write_text(READ_BACK)
    manager = Project.load(tmp_path / "semis.yaml").manager(database_url=database)
    return manager, manager.load(tmp_path / "held.yaml")


def _in_a_thread(run: Callable[[], Result]) -> tuple[threading.Thread, list[Result]]:
    results: list[Result] = []
    thread = threading.Thread(target=lambda: results.append(run()))
    thread.start()
    return thread, results


@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "dry run"])
def test_a_second_apply_waits_for_the_first_and_is_refused_once_it_commits(
    database: str, tmp_path: Path, dry_run: bool
) -> None:
    args = ["--dry-run"] if dry_run else ["-o", str(tmp_path / "second")]
    manager, scenario = _held_apply(tmp_path, database)
    with readback.exclusive(database, scenario.id), readback.transaction(database) as held:
        manager.apply(scenario, tmp_path / "first", connection=held)
        thread, results = _in_a_thread(lambda: _apply(tmp_path, database, READ_BACK, *args))
        thread.join(timeout=1)
        assert thread.is_alive(), "the second apply did not wait for the first"
    thread.join(timeout=30)
    (second,) = results
    assert (second.exit_code, second.stderr.splitlines()[0]) == (
        1,
        f"scenario two_tables is already applied: {CONTINENT} holds its rows",
    )
    assert (_count(database, CONTINENT), _count(database, COUNTRY)) == (2, 4)


def test_a_second_apply_proceeds_once_the_first_rolls_back(database: str, tmp_path: Path) -> None:
    manager, scenario = _held_apply(tmp_path, database)
    with (
        pytest.raises(RuntimeError),
        readback.exclusive(database, scenario.id),
        readback.transaction(database) as held,
    ):
        manager.apply(scenario, tmp_path / "first", connection=held)
        thread, results = _in_a_thread(
            lambda: _apply(tmp_path, database, READ_BACK, "-o", str(tmp_path / "second"))
        )
        thread.join(timeout=1)
        assert thread.is_alive(), "the second apply did not wait for the first"
        raise RuntimeError
    thread.join(timeout=30)
    (second,) = results
    assert (second.exit_code, _count(database, CONTINENT), _count(database, COUNTRY)) == (0, 2, 4)
