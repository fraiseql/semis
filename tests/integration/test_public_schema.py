"""A schema whose DDL names no schema: its tables are in public, and named public.tb_x."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from fraiseql_semis import ScenarioManager, SchemaFacts, TableCodes

PARENT = "public.tb_semis_pub_parent"
CHILD = "public.tb_semis_pub_child"
DDL = """
CREATE TABLE tb_semis_pub_parent (
    pk_semis_pub_parent INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE tb_semis_pub_child (
    pk_semis_pub_child INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    fk_semis_pub_parent INT NOT NULL REFERENCES tb_semis_pub_parent (pk_semis_pub_parent),
    quantity INT NOT NULL
);
"""
CODES = TableCodes({PARENT: 0x0A000001, CHILD: 0x0A000002})
SCENARIO = f"""\
scenario_id: 0x5001
name: in_public
mode: read-back
seed: 42
tables:
  - name: {PARENT}
    count: 2
  - name: {CHILD}
    count: 6
"""


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    def drop(admin: psycopg.Connection) -> None:
        admin.execute(f"DROP TABLE IF EXISTS {CHILD}, {PARENT}")

    with psycopg.connect(database_url, autocommit=True) as admin:
        drop(admin)
        admin.execute(DDL)
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()
    with psycopg.connect(database_url, autocommit=True) as admin:
        drop(admin)


@pytest.mark.parametrize("source", ["ddl", "live"])
def test_a_scenario_seeds_tables_the_ddl_left_in_public(
    connection: psycopg.Connection, tmp_path: Path, source: str
) -> None:
    facts = (
        SchemaFacts.from_source(DDL, table_codes=CODES)
        if source == "ddl"
        else SchemaFacts.from_database(connection, schemas=["public"], table_codes=CODES)
    )
    manager = ScenarioManager(facts)
    path = tmp_path / "in_public.yaml"
    path.write_text(SCENARIO)
    manager.execute(manager.load(path), tmp_path / "out", connection=connection)
    parents = connection.execute(f"SELECT pk_semis_pub_parent FROM {PARENT}").fetchall()
    children = connection.execute(f"SELECT fk_semis_pub_parent FROM {CHILD}").fetchall()
    assert sorted(fk for (fk,) in children) == sorted([pk for (pk,) in parents] * 3)
