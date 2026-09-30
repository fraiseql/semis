"""generate → write → apply → SELECT: a row semis invented, in PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from fraiseql_semis import FakeDataGenerator, SchemaFacts, TableCodes, seeds

SCHEMA = "semis_it"
TABLE = f"{SCHEMA}.tb_continent"
DDL = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {TABLE} (
    pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL
);
"""
CODES = TableCodes({TABLE: 0x02030405})


@pytest.fixture
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        connection.execute(DDL)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


def _generate() -> list[dict[str, object]]:
    facts = SchemaFacts.from_source(DDL, table_codes=CODES)
    return list(FakeDataGenerator(facts, scenario_id=0x5001, seed=42).generate_rows(TABLE, count=3))


def test_generate_write_apply_read_back(database: str, tmp_path: Path) -> None:
    facts = SchemaFacts.from_source(DDL, table_codes=CODES)
    rows = _generate()
    seed = seeds.write(
        tmp_path / "010_tb_continent.sql", TABLE, rows, facts=facts, mode="read-back"
    )
    seeds.apply(database, [seed.path])

    with psycopg.connect(database) as connection:
        found = connection.execute(f"SELECT pk_continent, id FROM {TABLE} ORDER BY id").fetchall()

    generated = {row["id"] for row in rows}
    keys = [pk for pk, _ in found]
    assert {uuid for _, uuid in found} == generated
    assert len(found) == 3
    assert len(set(keys)) == 3
    assert all(isinstance(pk, int) for pk in keys)
    assert all("pk_continent" not in row for row in rows)
    assert {row["id"] for row in _generate()} == generated


def test_from_database_reads_the_table_from_source_reads(database: str) -> None:
    """Names and roles only: the two sources spell defaults and checks differently (§8)."""
    live = SchemaFacts.from_database(database, schemas=[SCHEMA], table_codes=CODES).facts_for(TABLE)
    ddl = SchemaFacts.from_source(DDL, table_codes=CODES).facts_for(TABLE)
    assert [c.name for c in live.columns] == [c.name for c in ddl.columns]
    assert (live.surrogate_pk, live.natural_id) == (ddl.surrogate_pk, ddl.natural_id)
