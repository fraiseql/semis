"""A prep-seed scenario judged by confiture's five levels, on semis' own connection.

The database is the project's, already built: it holds the catalog and staging tables
and the resolvers. Levels 4 and 5 load the scenario's seeds and run the resolvers
inside a savepoint confiture rolls back, so nothing outlives the validation.
"""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from confiture.platform import PrepSeedPattern, ViolationSeverity
from typer.testing import CliRunner

from fraiseql_semis import ScenarioManager, SchemaFacts, TableCodes, seeds
from fraiseql_semis.cli import app
from fraiseql_semis.staging import Staging

CATALOG, PREP = "semis_vs_catalog", "semis_vs_prep"
OWNER, WIDGET = f"{CATALOG}.tb_owner", f"{CATALOG}.tb_widget"
TABLES = f"""
CREATE SCHEMA {CATALOG};
CREATE SCHEMA {PREP};
CREATE TABLE {OWNER} (
    pk_owner BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL
);
CREATE TABLE {WIDGET} (
    pk_widget BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_owner BIGINT REFERENCES {OWNER} (pk_owner)
);
CREATE TABLE {PREP}.tb_owner (id UUID NOT NULL UNIQUE, identifier TEXT NOT NULL, name VARCHAR(50));
CREATE TABLE {PREP}.tb_widget (id UUID NOT NULL UNIQUE, identifier TEXT NOT NULL, fk_owner_id UUID);
"""
# The widget's resolver never reads fk_owner_id, so every widget lands without its owner.
RESOLVERS = f"""
CREATE FUNCTION {CATALOG}.fn_resolve_tb_owner() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO {OWNER} (id, identifier, name) SELECT id, identifier, name FROM {PREP}.tb_owner;
END $$;
CREATE FUNCTION {CATALOG}.fn_resolve_tb_widget() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO {WIDGET} (id, identifier) SELECT id, identifier FROM {PREP}.tb_widget;
END $$;
"""
CODES = TableCodes({OWNER: 0x02030405, WIDGET: 0x03040506})
SCENARIO = f"""\
scenario_id: 0x5001
name: widgets
mode: prep-seed
seed: 42
tables:
  - name: {OWNER}
    count: 2
  - name: {WIDGET}
    count: 3
"""


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {CATALOG}, {PREP} CASCADE")
        admin.execute(TABLES + RESOLVERS)
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {CATALOG}, {PREP} CASCADE")


@pytest.fixture
def schema_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "schema"
    directory.mkdir()
    (directory / "010_tables.sql").write_text(TABLES)
    (directory / "020_resolvers.sql").write_text(RESOLVERS)
    return directory


@pytest.fixture
def manager() -> ScenarioManager:
    return ScenarioManager(
        SchemaFacts.from_source(TABLES, table_codes=CODES), staging=Staging(PREP)
    )


def test_level_5_reports_a_null_foreign_key(
    connection: psycopg.Connection, schema_dir: Path, manager: ScenarioManager, tmp_path: Path
) -> None:
    path = tmp_path / "widgets.yaml"
    path.write_text(SCENARIO)
    validation = manager.validate(
        manager.load(path), schema_dir=schema_dir, max_level=5, connection=connection
    )
    assert [
        (violation.severity, violation.pattern, violation.message)
        for violation in validation.report.violations
    ] == [
        (
            ViolationSeverity.ERROR,
            PrepSeedPattern.MISSING_FK_TRANSFORMATION,
            f"fn_resolve_tb_widget fills {WIDGET} but never joins tb_owner on fk_owner_id: "
            "fk_owner is not resolved",
        ),
        (
            ViolationSeverity.CRITICAL,
            PrepSeedPattern.NULL_FK_AFTER_RESOLUTION,
            f"Found 3 NULL values in {WIDGET}.fk_owner after resolution",
        ),
    ]


def _counts(connection: psycopg.Connection) -> list[tuple[int, ...]]:
    """Rows in the catalog tables and their twins, as *connection* sees them."""
    return [
        tuple(row)
        for row in connection.execute(
            f"SELECT (SELECT count(*) FROM {OWNER}), (SELECT count(*) FROM {WIDGET}), "
            f"(SELECT count(*) FROM {PREP}.tb_owner), (SELECT count(*) FROM {PREP}.tb_widget)"
        )
    ]


def test_levels_4_and_5_leave_nothing_behind_on_the_callers_connection(
    connection: psycopg.Connection,
    database_url: str,
    schema_dir: Path,
    manager: ScenarioManager,
    tmp_path: Path,
) -> None:
    path = tmp_path / "widgets.yaml"
    path.write_text(SCENARIO)
    manager.validate(manager.load(path), schema_dir=schema_dir, max_level=5, connection=connection)
    assert _counts(connection) == [(0, 0, 0, 0)]
    connection.commit()
    with psycopg.connect(database_url) as other:
        assert _counts(other) == [(0, 0, 0, 0)]


def test_levels_4_and_5_leave_nothing_behind_on_a_url(
    connection: psycopg.Connection,
    database_url: str,
    schema_dir: Path,
    manager: ScenarioManager,
    tmp_path: Path,
) -> None:
    path = tmp_path / "widgets.yaml"
    path.write_text(SCENARIO)
    manager.execute(manager.load(path), tmp_path / "seeds")
    report = seeds.validate(
        tmp_path / "seeds",
        schema_dir=schema_dir,
        max_level=5,
        database=database_url,
        prep_seed_schema=PREP,
    )
    assert PrepSeedPattern.NULL_FK_AFTER_RESOLUTION in {v.pattern for v in report.violations}
    assert _counts(connection) == [(0, 0, 0, 0)]


@pytest.mark.usefixtures("schema_dir")
def test_the_command_runs_the_five_levels_on_a_url_most_severe_first(
    connection: psycopg.Connection, database_url: str, tmp_path: Path
) -> None:
    (tmp_path / "widgets.yaml").write_text(SCENARIO)
    config = tmp_path / "semis.yaml"
    config.write_text(
        "schema:\n  ddl: schema\ntable_codes:\n"
        f"  {OWNER}: 0x02030405\n  {WIDGET}: 0x03040506\n"
        f"prep_seed:\n  prep_seed_schema: {PREP}\n"
    )
    result = CliRunner().invoke(
        app,
        ["validate-seeds", str(tmp_path / "widgets.yaml"), "-c", str(config), "-d", database_url],
    )
    headings = [line for line in result.stdout.splitlines() if not line.startswith(" ")]
    assert result.exit_code == 1
    assert headings[1:] == [
        "validated 2 seed files at levels 1-5",
        "CRITICAL NULL_FK_AFTER_RESOLUTION schema/010_tables.sql:10",
        "ERROR MISSING_FK_TRANSFORMATION schema/020_resolvers.sql:8",
        "2 findings: 1 CRITICAL, 1 ERROR",
    ]
    assert _counts(connection) == [(0, 0, 0, 0)]
