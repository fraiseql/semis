"""A 100,000-row scenario, written and applied: the PRD's scale, end to end.

The warehouse's stock scenario with ``inventory.tb_item`` raised to 100,000, in its
declared prep-seed mode: every row drawn, streamed to its seed file and applied into the
staging twins. The warehouse's schemas have names a test database may already hold, so
they are created in a database of their own, dropped when the test ends.
"""

import os
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from fraiseql_semis import Project

WAREHOUSE = Path(__file__).parent.parent / "warehouse"
ROWS = 100_000
BUDGET = 120  # seconds: docs/PRD.md, "under two minutes"

pytestmark = pytest.mark.slow


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    name = f"semis_scale_{os.getpid()}"
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        with psycopg.connect(make_conninfo(database_url, dbname=name)) as connection:
            for ddl in sorted((WAREHOUSE / "schema").glob("*.sql")):
                connection.execute(ddl.read_bytes())
            yield connection
            connection.rollback()
    finally:
        with psycopg.connect(database_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_a_hundred_thousand_rows_write_and_apply_in_two_minutes(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    project = Project.load(WAREHOUSE / "semis.yaml")
    manager = project.manager()
    scenario = manager.load(WAREHOUSE / "scenarios" / "stock.yaml")
    others = sum(spec.count for spec in scenario.tables if spec.name != "inventory.tb_item")
    scenario = replace(
        scenario,
        tables=tuple(
            replace(spec, count=ROWS - others) if spec.name == "inventory.tb_item" else spec
            for spec in scenario.tables
        ),
    )
    started = time.perf_counter()
    run = manager.apply(scenario, tmp_path, connection=connection)
    elapsed = time.perf_counter() - started
    assert sum(seed.rows for seed in run.seeds) == ROWS
    [(applied,)] = connection.execute("SELECT count(*) FROM prep_seed.tb_item").fetchall()
    assert applied == ROWS - others
    assert elapsed < BUDGET, f"{ROWS:,} rows written and applied in {elapsed:.0f}s"
