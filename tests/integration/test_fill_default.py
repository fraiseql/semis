"""fill: names a column with a default: semis draws it, and PostgreSQL keeps the value."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from fraiseql_semis import ScenarioManager, SchemaFacts, TableCodes

TABLE = "public.tb_semis_fill_order"
DDL = """
CREATE TABLE tb_semis_fill_order (
    pk_semis_fill_order INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'placed', 'paid')),
    note TEXT DEFAULT 'none'
);
"""
CODES = TableCodes({TABLE: 0x0A000003})


def _scenario(fill: str) -> str:
    return (
        "scenario_id: 0x5002\nname: filled\nmode: read-back\nseed: 42\ntables:\n"
        f"  - name: {TABLE}\n    count: 12\n{fill}"
    )


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP TABLE IF EXISTS {TABLE}")
        admin.execute(DDL)
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP TABLE IF EXISTS {TABLE}")


def _seeded(connection: psycopg.Connection, tmp_path: Path, fill: str) -> set[tuple[str, str]]:
    manager = ScenarioManager(
        SchemaFacts.from_database(connection, schemas=["public"], table_codes=CODES)
    )
    path = tmp_path / "filled.yaml"
    path.write_text(_scenario(fill))
    manager.execute(manager.load(path), tmp_path / "out", connection=connection)
    rows = connection.execute(f"SELECT status, note FROM {TABLE}").fetchall()
    return {(status, note) for status, note in rows}


def test_fill_draws_the_defaulted_columns_it_names(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    rows = _seeded(connection, tmp_path, "    fill: [status, note]\n")
    assert len({status for status, _ in rows}) > 1
    assert "none" not in {note for _, note in rows}


def test_unnamed_defaulted_columns_take_their_defaults(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    assert _seeded(connection, tmp_path, "    fill: all\n") == {("draft", "none")}
