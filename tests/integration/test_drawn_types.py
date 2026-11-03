"""Values semis draws from the schema alone, applied: PostgreSQL accepts every one.

json and arrays (fraiseql/semis#10) and CHECK value lists (#11), each read from DDL and
from the live database, and written by both writers.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import psycopg
import pytest

from fraiseql_semis import FakeDataGenerator, SchemaFacts, TableCodes, seeds

SCHEMA = "semis_drawn"
TABLE = f"{SCHEMA}.tb_template"
AGENT = f"{SCHEMA}.tb_agent"
DDL = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {TABLE} (
    pk_template BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    variables JSONB NOT NULL,
    metadata JSON,
    tags TEXT[] NOT NULL,
    owner_ids UUID[] NOT NULL,
    codes VARCHAR(4)[] NOT NULL,
    levels INTEGER[][] NOT NULL
);
CREATE TABLE {AGENT} (
    pk_agent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('manager', 'sales', 'support')),
    status VARCHAR(20) NOT NULL,
    level INTEGER NOT NULL CHECK (level IN (1, 2)),
    CONSTRAINT tb_agent_status_check CHECK (status IN ('open', 'closed'))
);
"""
CODES = TableCodes({TABLE: 0x0C, AGENT: 0x0F})


@pytest.fixture
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        connection.execute(DDL)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


def _facts(database: str, source: str) -> SchemaFacts:
    if source == "ddl":
        return SchemaFacts.from_source(DDL, table_codes=CODES)
    return SchemaFacts.from_database(database, schemas=[SCHEMA], table_codes=CODES)


@pytest.mark.parametrize("format", ["copy", "insert"])
@pytest.mark.parametrize("source", ["ddl", "live"])
def test_json_and_arrays_are_applied_as_drawn(
    database: str, tmp_path: Path, format: Literal["copy", "insert"], source: str
) -> None:
    facts = _facts(database, source)
    rows = list(
        FakeDataGenerator(facts, scenario_id=0x5001, seed=42).generate_rows(TABLE, 6, fill="all")
    )
    seed = seeds.write(
        tmp_path / "010_tb_template.sql", TABLE, rows, facts=facts, mode="read-back", format=format
    )
    seeds.apply(database, [seed.path])

    with psycopg.connect(database) as connection:
        found = connection.execute(
            f"SELECT variables, metadata, tags, owner_ids, codes, levels FROM {TABLE} ORDER BY id"
        ).fetchall()
    drawn = sorted(rows, key=lambda row: str(row["id"]))
    assert found == [
        (
            row["variables"],
            row["metadata"],
            row["tags"],
            row["owner_ids"],
            row["codes"],
            row["levels"],
        )
        for row in drawn
    ]


@pytest.mark.parametrize("format", ["copy", "insert"])
@pytest.mark.parametrize("source", ["ddl", "live"])
def test_a_check_value_list_is_drawn_and_applied(
    database: str, tmp_path: Path, format: Literal["copy", "insert"], source: str
) -> None:
    facts = _facts(database, source)
    rows = FakeDataGenerator(facts, scenario_id=0x5001, seed=42).generate_rows(AGENT, 5)
    seed = seeds.write(
        tmp_path / "010_tb_agent.sql", AGENT, rows, facts=facts, mode="read-back", format=format
    )
    seeds.apply(database, [seed.path])

    with psycopg.connect(database) as connection:
        found = connection.execute(
            f"SELECT role, status, level FROM {AGENT} ORDER BY id"
        ).fetchall()
    assert found == [
        ("manager", "open", 1),
        ("sales", "closed", 2),
        ("support", "open", 1),
        ("manager", "closed", 2),
        ("sales", "open", 1),
    ]
