"""``semis reset``: the rows a scenario wrote deleted, and only those."""

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import LiteralString

import psycopg
import pytest
from typer.testing import CliRunner, Result

from fraiseql_semis import readback
from fraiseql_semis.cli import app
from fraiseql_semis.project import Project

SCHEMA = "semis_reset"
STAGING = f"{SCHEMA}_prep"
OUTSIDE = f"{SCHEMA}_outside"
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
    fk_parent_location BIGINT REFERENCES {LOCATION} (pk_location) ON DELETE RESTRICT,
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
PREP_SEED = f"""\
scenario_id: 0x5001
name: continents
mode: prep-seed
seed: 42
tables:
  - name: {CONTINENT}
    count: 2
"""
# Rows another tool wrote into a table the scenario writes too.
FOREIGN = f"""
INSERT INTO {CONTINENT} (id, identifier, name) VALUES
    (gen_random_uuid(), 'x1', 'One'), (gen_random_uuid(), 'x2', 'Two'),
    (gen_random_uuid(), 'x3', 'Three')
"""

runner = CliRunner()


@pytest.fixture
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {STAGING}, {OUTSIDE} CASCADE")
        admin.execute(DDL)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {STAGING}, {OUTSIDE} CASCADE")


def _semis(tmp_path: Path, database: str, command: str, scenario: str, *args: str) -> Result:
    (tmp_path / "schema.sql").write_text(DDL)
    (tmp_path / "semis.yaml").write_text(SEMIS_YAML)
    path = tmp_path / "scenario.yaml"
    path.write_text(scenario)
    config = ["-c", str(tmp_path / "semis.yaml"), "--database-url", database]
    return runner.invoke(app, [command, str(path), *config, *args])


def _execute(database: str, statement: LiteralString) -> None:
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(statement)


def _one(database: str, query: LiteralString) -> object:
    with psycopg.connect(database) as connection:
        (value,) = connection.execute(query).fetchone() or (None,)
    return value


def test_reset_deletes_the_scenarios_rows_and_keeps_the_others(
    database: str, tmp_path: Path
) -> None:
    _execute(database, FOREIGN)
    applied = _semis(tmp_path, database, "apply", READ_BACK, "-o", str(tmp_path / "out"))
    reset = _semis(tmp_path, database, "reset", READ_BACK)
    assert (applied.exit_code, reset.exit_code) == (0, 0), reset.output
    assert _one(database, f"SELECT string_agg(identifier, ',' ORDER BY 1) FROM {CONTINENT}") == (
        "x1,x2,x3"
    )
    assert _one(database, f"SELECT count(*) FROM {COUNTRY}") == 0
    # The sequence is the shared table's: five keys given, and none taken back.
    assert _one(database, f"SELECT max(pk_continent) FROM {CONTINENT}") == 3
    sequence = f"pg_get_serial_sequence('{CONTINENT}', 'pk_continent')"
    assert _one(database, f"SELECT nextval({sequence})") == 6


def test_a_prep_seed_reset_deletes_its_rows_from_the_twins_and_the_final_tables(
    database: str, tmp_path: Path
) -> None:
    """The staging twins hold the rows the seeds wrote, the final tables the rows a
    project's resolvers promoted from them, each carrying the scenario's UUIDs."""
    applied = _semis(tmp_path, database, "apply", PREP_SEED, "-o", str(tmp_path / "out"))
    _execute(
        database,
        f"INSERT INTO {CONTINENT} (id, identifier, name) "
        f"SELECT id, identifier, name FROM {STAGING}.tb_continent",
    )
    _execute(database, FOREIGN)
    reset = _semis(tmp_path, database, "reset", PREP_SEED)
    assert (applied.exit_code, reset.exit_code) == (0, 0), reset.output
    assert _one(database, f"SELECT count(*) FROM {STAGING}.tb_continent") == 0
    assert _one(database, f"SELECT count(*) FROM {CONTINENT}") == 3


def _counts(database: str) -> tuple[object, ...]:
    return tuple(
        _one(database, query)
        for query in (
            f"SELECT count(*) FROM {CONTINENT}",
            f"SELECT count(*) FROM {COUNTRY}",
        )
    )


@pytest.mark.parametrize("action", ["NO ACTION", "RESTRICT", "CASCADE", "SET NULL"], ids=str.lower)
def test_a_row_outside_the_run_pointing_at_a_scenario_row_blocks_the_reset(
    database: str, tmp_path: Path, action: LiteralString
) -> None:
    """Whatever its action: a CASCADE or SET NULL would otherwise reach a row semis did
    not write. Nothing is deleted."""
    applied = _semis(tmp_path, database, "apply", READ_BACK, "-o", str(tmp_path / "out"))
    _execute(
        database,
        f"CREATE SCHEMA {OUTSIDE}; CREATE TABLE {OUTSIDE}.tb_visit (fk_continent BIGINT, "
        f"CONSTRAINT visit_continent FOREIGN KEY (fk_continent) REFERENCES {CONTINENT} "
        f"ON DELETE {action}); "
        f"INSERT INTO {OUTSIDE}.tb_visit SELECT min(pk_continent) FROM {CONTINENT}",
    )
    reset = _semis(tmp_path, database, "reset", READ_BACK)
    assert (applied.exit_code, reset.exit_code) == (0, 1)
    assert reset.stderr.splitlines()[0] == (
        f"scenario two_tables: 1 row of {OUTSIDE}.tb_visit points at its rows of "
        f"{CONTINENT}, by visit_continent"
    )
    assert _counts(database) == (2, 4)
    assert _one(database, f"SELECT count(fk_continent) FROM {OUTSIDE}.tb_visit") == 1


def test_a_row_another_tool_wrote_into_a_run_table_blocks_the_reset(
    database: str, tmp_path: Path
) -> None:
    """A country the scenario did not write, on one of its continents: not its to delete."""
    _semis(tmp_path, database, "apply", READ_BACK, "-o", str(tmp_path / "out"))
    _execute(
        database,
        f"INSERT INTO {COUNTRY} (id, identifier, fk_continent, iso_code) "
        f"SELECT gen_random_uuid(), 'foreign', min(pk_continent), 'ZZ' FROM {CONTINENT}",
    )
    reset = _semis(tmp_path, database, "reset", READ_BACK)
    assert (reset.exit_code, reset.stderr.splitlines()[0]) == (
        1,
        f"scenario two_tables: 1 row of {COUNTRY} points at its rows of {CONTINENT}, "
        "by tb_country_fk_continent_fkey",
    )
    assert _counts(database) == (2, 5)


TREE = f"""\
scenario_id: 0x5002
name: tree
mode: read-back
seed: 42
tables:
  - name: {LOCATION}
    count: 20
    hierarchy: {{parent: fk_parent_location, roots: 2, fan_out: 3}}
"""


def test_a_tree_whose_key_restricts_deletes_is_reset_in_one_statement(
    database: str, tmp_path: Path
) -> None:
    """Every row of the tree is the scenario's: one DELETE takes them all, children and
    parents, even when the self-referencing key is ON DELETE RESTRICT."""
    applied = _semis(tmp_path, database, "apply", TREE, "-o", str(tmp_path / "out"))
    reset = _semis(tmp_path, database, "reset", TREE)
    assert (applied.exit_code, reset.exit_code) == (0, 0), reset.output
    assert _one(database, f"SELECT count(*) FROM {LOCATION}") == 0


def _seeds(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.sql"))}


def test_reset_then_apply_writes_the_first_applys_seeds_byte_for_byte(
    database: str, tmp_path: Path
) -> None:
    """On tables the scenario owns alone, each emptied table's identity restarts, so the
    keys read back, and the seeds carrying them, are the first apply's."""
    _semis(tmp_path, database, "apply", READ_BACK, "-o", str(tmp_path / "first"))
    reset = _semis(tmp_path, database, "reset", READ_BACK)
    again = _semis(tmp_path, database, "apply", READ_BACK, "-o", str(tmp_path / "again"))
    assert (reset.exit_code, again.exit_code) == (0, 0), again.output
    assert reset.output.splitlines() == [
        f"deleted 4 rows from {COUNTRY}; identity restarted",
        f"deleted 2 rows from {CONTINENT}; identity restarted",
        "committed",
    ]
    assert _seeds(tmp_path / "again") == _seeds(tmp_path / "first")


def test_a_table_that_keeps_other_rows_keeps_its_identity_and_says_so(
    database: str, tmp_path: Path
) -> None:
    _execute(database, FOREIGN)
    _semis(tmp_path, database, "apply", READ_BACK, "-o", str(tmp_path / "out"))
    reset = _semis(tmp_path, database, "reset", READ_BACK)
    assert reset.output.splitlines()[1] == (
        f"deleted 2 rows from {CONTINENT}; 3 other rows kept, identity not restarted"
    )


def test_a_dry_run_rolls_the_restart_back_with_the_delete(database: str, tmp_path: Path) -> None:
    _semis(tmp_path, database, "apply", READ_BACK, "-o", str(tmp_path / "out"))
    reset = _semis(tmp_path, database, "reset", READ_BACK, "--dry-run")
    assert reset.output.splitlines()[-1] == "rolled back: nothing was deleted"
    assert _counts(database) == (2, 4)
    sequence = f"pg_get_serial_sequence('{CONTINENT}', 'pk_continent')"
    assert _one(database, f"SELECT nextval({sequence})") == 3


def test_apply_reset_applies_again_over_the_first_run(database: str, tmp_path: Path) -> None:
    _semis(tmp_path, database, "apply", READ_BACK, "-o", str(tmp_path / "first"))
    again = _semis(tmp_path, database, "apply", READ_BACK, "--reset", "-o", str(tmp_path / "again"))
    assert again.exit_code == 0, again.output
    assert again.output.splitlines()[:2] == [
        f"deleted 4 rows from {COUNTRY}; identity restarted",
        f"deleted 2 rows from {CONTINENT}; identity restarted",
    ]
    assert again.output.splitlines()[-1] == "committed"
    assert _counts(database) == (2, 4)
    assert _seeds(tmp_path / "again") == _seeds(tmp_path / "first")


def test_apply_reset_on_a_fresh_database_applies(database: str, tmp_path: Path) -> None:
    applied = _semis(tmp_path, database, "apply", READ_BACK, "--reset", "-o", str(tmp_path / "o"))
    assert (applied.exit_code, _counts(database)) == (0, (2, 4))
    assert applied.output.splitlines()[0] == f"deleted 0 rows from {COUNTRY}; identity restarted"


def test_a_failed_apply_reset_keeps_the_first_runs_rows(database: str, tmp_path: Path) -> None:
    """The reset and the apply are one transaction: the apply's failure rolls the reset
    back too."""
    _semis(tmp_path, database, "apply", READ_BACK, "-o", str(tmp_path / "first"))
    before = _one(database, f"SELECT string_agg(id::text, ',' ORDER BY id) FROM {COUNTRY}")
    _execute(database, f"ALTER TABLE {COUNTRY} ADD CHECK (iso_code = 'no') NOT VALID")
    again = _semis(tmp_path, database, "apply", READ_BACK, "--reset", "-o", str(tmp_path / "again"))
    assert (again.exit_code, "check constraint" in again.stderr) == (5, True)
    assert _counts(database) == (2, 4)
    assert _one(database, f"SELECT string_agg(id::text, ',' ORDER BY id) FROM {COUNTRY}") == (
        before
    )


def test_a_second_apply_reset_waits_then_resets_the_firsts_rows(
    database: str, tmp_path: Path
) -> None:
    (tmp_path / "schema.sql").write_text(DDL)
    (tmp_path / "semis.yaml").write_text(SEMIS_YAML)
    (tmp_path / "held.yaml").write_text(READ_BACK)
    manager = Project.load(tmp_path / "semis.yaml").manager(database_url=database)
    scenario = manager.load(tmp_path / "held.yaml")
    results: list[Result] = []
    with readback.exclusive(database, scenario.id), readback.transaction(database) as held:
        manager.apply(scenario, tmp_path / "first", connection=held)
        thread = threading.Thread(
            target=lambda: results.append(
                _semis(tmp_path, database, "apply", READ_BACK, "--reset", "-o", str(tmp_path / "b"))
            )
        )
        thread.start()
        thread.join(timeout=1)
        assert thread.is_alive(), "the second apply did not wait for the first"
    thread.join(timeout=30)
    (second,) = results
    assert second.exit_code == 0, second.output
    assert second.output.splitlines()[1] == f"deleted 2 rows from {CONTINENT}; identity restarted"
    assert _counts(database) == (2, 4)
