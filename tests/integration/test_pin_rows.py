"""``semis pin -d`` on a prep-seed scenario: the existing rows it points at, by UUID."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import LiteralString

import psycopg
import pytest
from typer.testing import CliRunner, Result

from fraiseql_semis.cli import app

SCHEMA = "semis_pinrows"
STAGING = f"{SCHEMA}_prep"
CATEGORY = f"{SCHEMA}.tb_category"
UNIT = f"{SCHEMA}.tb_unit"
CUSTOMER = f"{SCHEMA}.tb_customer"
DDL = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {CATEGORY} (
    pk_category BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    identifier TEXT NOT NULL UNIQUE
);
CREATE TABLE {UNIT} (
    pk_unit BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid()
);
CREATE TABLE {CUSTOMER} (
    pk_customer BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    fk_category BIGINT NOT NULL REFERENCES {CATEGORY} (pk_category),
    fk_unit BIGINT REFERENCES {UNIT} (pk_unit)
);
CREATE SCHEMA {STAGING};
CREATE TABLE {STAGING}.tb_customer (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    name TEXT,
    fk_category_id UUID,
    fk_unit_id UUID
);
"""
ROWS = f"""
INSERT INTO {CATEGORY} (identifier) VALUES ('books'), ('music');
INSERT INTO {UNIT} DEFAULT VALUES;
INSERT INTO {UNIT} DEFAULT VALUES;
"""
SEMIS_YAML = f"""\
schema:
  ddl: schema.sql
prep_seed:
  prep_seed_schema: {STAGING}
table_codes:
  {CUSTOMER}: 0x0A0B0C0D
"""
SCENARIO = f"""\
scenario_id: 0x5006
name: customers
mode: prep-seed
seed: 42
tables:
  - name: {CUSTOMER}
    count: 4
existing:
  - name: {CATEGORY}
  - name: {UNIT}
"""

runner = CliRunner()


@pytest.fixture
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {STAGING} CASCADE")
        admin.execute(DDL)
        admin.execute(ROWS)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {STAGING} CASCADE")


def _execute(database: str, statement: LiteralString) -> None:
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(statement)


def _pin(tmp_path: Path, database: str | None, *args: str, scenario: str = SCENARIO) -> Result:
    (tmp_path / "schema.sql").write_text(DDL)
    (tmp_path / "semis.yaml").write_text(SEMIS_YAML)
    (tmp_path / "customers.yaml").write_text(scenario)
    url = ["-d", database] if database is not None else []
    config = ["-c", str(tmp_path / "semis.yaml"), *url]
    return runner.invoke(app, ["pin", str(tmp_path / "customers.yaml"), *config, *args])


def _rows(tmp_path: Path) -> dict[str, list[list[object]]]:
    pinned = json.loads((tmp_path / "customers.pin.json").read_text())
    return {read["table"]: read["rows"] for read in pinned["rows"]["reads"]}


def _uuids(database: str, query: LiteralString) -> list[list[object]]:
    with psycopg.connect(database) as connection:
        return [[value, str(uuid)] for value, uuid in connection.execute(query).fetchall()]


def test_pin_records_the_rows_a_prep_seed_scenario_points_at(database: str, tmp_path: Path) -> None:
    """In read-back's order, each by its identifier when the table has one."""
    result = _pin(tmp_path, database)
    assert result.exit_code == 0, result.output
    assert _rows(tmp_path) == {
        CATEGORY: _uuids(database, f"SELECT identifier, id FROM {CATEGORY} ORDER BY pk_category"),
        UNIT: _uuids(database, f"SELECT NULL, id FROM {UNIT} ORDER BY pk_unit"),
    }


def test_pin_records_rows_named_by_a_unique_column(database: str, tmp_path: Path) -> None:
    """A prep-seed where: reads by its column too, repeated values kept, and the run takes them."""
    _execute(database, f"ALTER TABLE {CATEGORY} ADD COLUMN code TEXT UNIQUE")
    _execute(database, f"UPDATE {CATEGORY} SET code = upper(identifier)")
    scenario = SCENARIO.replace(
        f"  - name: {CATEGORY}\n",
        f"  - name: {CATEGORY}\n    where: {{code: [MUSIC, MUSIC, BOOKS]}}\n",
    )
    ddl = DDL.replace(
        "identifier TEXT NOT NULL UNIQUE\n",
        "identifier TEXT NOT NULL UNIQUE,\n    code TEXT UNIQUE\n",
        1,
    )
    (tmp_path / "schema.sql").write_text(ddl)
    (tmp_path / "semis.yaml").write_text(SEMIS_YAML)
    (tmp_path / "customers.yaml").write_text(scenario)
    config = ["-c", str(tmp_path / "semis.yaml")]
    pinned = runner.invoke(app, ["pin", str(tmp_path / "customers.yaml"), *config, "-d", database])
    assert pinned.exit_code == 0, pinned.output
    reads = json.loads((tmp_path / "customers.pin.json").read_text())["rows"]["reads"]
    music, books = (
        _uuids(database, f"SELECT code, id FROM {CATEGORY} WHERE code = 'MUSIC'")[0],
        _uuids(database, f"SELECT code, id FROM {CATEGORY} WHERE code = 'BOOKS'")[0],
    )
    read = next(read for read in reads if read["table"] == CATEGORY)
    assert (read["by"], read["where"], read["rows"]) == (
        "code",
        ["MUSIC", "MUSIC", "BOOKS"],
        [music, music, books],
    )
    seeded = runner.invoke(
        app, ["seeds", str(tmp_path / "customers.yaml"), *config, "-o", str(tmp_path / "out")]
    )
    assert seeded.exit_code == 0, seeded.output
    written = next((tmp_path / "out").iterdir()).read_text()
    assert written.count(str(music[1])) == 3
    assert written.count(str(books[1])) == 1


def test_a_second_pin_of_the_same_rows_is_unchanged(database: str, tmp_path: Path) -> None:
    _pin(tmp_path, database)
    again = _pin(tmp_path, database)
    assert again.output == f"{tmp_path / 'customers.pin.json'} is unchanged\n"


def test_a_pinned_row_whose_uuid_changed_is_rewritten_and_fails_the_check(
    database: str, tmp_path: Path
) -> None:
    _pin(tmp_path, database)
    (old,) = [uuid for value, uuid in _rows(tmp_path)[CATEGORY] if value == "books"]
    _execute(database, f"UPDATE {CATEGORY} SET id = gen_random_uuid() WHERE identifier = 'books'")
    checked = _pin(tmp_path, database, "--check")
    assert (checked.exit_code, checked.output.splitlines()[:2]) == (
        1,
        ["What moved:", f"  {CATEGORY}: books {old} is gone"],
    )
    assert any(value == "books" and uuid == old for value, uuid in _rows(tmp_path)[CATEGORY])
    repinned = _pin(tmp_path, database)
    assert repinned.exit_code == 0
    assert [value for value, _ in _rows(tmp_path)[CATEGORY]] == ["music", "books"]


def test_a_row_added_to_a_whole_table_is_appended_and_passes_the_check(
    database: str, tmp_path: Path
) -> None:
    _pin(tmp_path, database)
    before = _rows(tmp_path)[CATEGORY]
    _execute(database, f"INSERT INTO {CATEGORY} (identifier) VALUES ('film')")
    checked = _pin(tmp_path, database, "--check")
    assert (checked.exit_code, checked.output.splitlines()[:2]) == (
        0,
        ["What moved:", f"  {CATEGORY}: 1 row added"],
    )
    assert _rows(tmp_path)[CATEGORY] == before
    _pin(tmp_path, database)
    assert [value for value, _ in _rows(tmp_path)[CATEGORY]] == ["books", "music", "film"]


def test_the_same_rows_in_another_order_write_nothing(database: str, tmp_path: Path) -> None:
    """A database built in another order does not churn the pin, or the seeds."""
    _pin(tmp_path, database)
    pin = tmp_path / "customers.pin.json"
    before = pin.read_bytes()
    _execute(
        database,
        f"DELETE FROM {CATEGORY} WHERE identifier = 'books'; "
        f"INSERT INTO {CATEGORY} (id, identifier) "
        f"VALUES ('{_rows(tmp_path)[CATEGORY][0][1]}', 'books')",
    )
    again = _pin(tmp_path, database)
    assert (again.output, pin.read_bytes()) == (f"{pin} is unchanged\n", before)


def test_an_ambient_database_url_alone_is_refused(
    database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UUIDs become a reviewed file: they are read only from a database named on
    purpose."""
    monkeypatch.delenv("CONFITURE_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", database)
    result = _pin(tmp_path, None)
    assert result.exit_code != 0
    assert not (tmp_path / "customers.pin.json").exists()


def test_pinning_rows_without_a_database_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("CONFITURE_DATABASE_URL", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    result = _pin(tmp_path, None)
    assert (result.exit_code, result.stderr.splitlines()[0]) == (
        1,
        f"scenario customers: pinning {CATEGORY}'s rows needs a database; pass -d",
    )


def test_semis_pin_repairs_a_scenario_its_pin_no_longer_covers(
    database: str, tmp_path: Path
) -> None:
    """The scenario narrowed its categories since it was pinned: a run is refused, and
    the command that repairs the pin is not."""
    _pin(tmp_path, database)
    narrowed = SCENARIO.replace(
        f"  - name: {CATEGORY}\n", f"  - name: {CATEGORY}\n    where: {{identifier: [music]}}\n"
    )
    (tmp_path / "customers.yaml").write_text(narrowed)
    config = ["-c", str(tmp_path / "semis.yaml")]
    refused = runner.invoke(app, ["validate", str(tmp_path / "customers.yaml"), *config])
    assert (refused.exit_code, refused.stderr.splitlines()[0]) == (
        1,
        f"scenario customers: prep-seed points at {CATEGORY}'s rows by UUID, and its pin "
        "records none",
    )
    repaired = _pin(tmp_path, database, scenario=narrowed)
    validated = runner.invoke(app, ["validate", str(tmp_path / "customers.yaml"), *config])
    assert (repaired.exit_code, validated.exit_code) == (0, 0), validated.output


def test_a_where_value_two_rows_share_is_refused_by_the_pin(database: str, tmp_path: Path) -> None:
    """As in read-back: which of the two a child would point at is arbitrary."""
    _execute(
        database,
        f"ALTER TABLE {CATEGORY} DROP CONSTRAINT tb_category_identifier_key; "
        f"INSERT INTO {CATEGORY} (identifier) VALUES ('books')",
    )
    narrowed = SCENARIO.replace(
        f"  - name: {CATEGORY}\n", f"  - name: {CATEGORY}\n    where: {{identifier: [books]}}\n"
    )
    result = _pin(tmp_path, database, scenario=narrowed)
    assert (result.exit_code, result.stderr.splitlines()[0]) == (
        1,
        f"scenario customers: existing {CATEGORY} has 2 rows whose identifier is 'books'",
    )
