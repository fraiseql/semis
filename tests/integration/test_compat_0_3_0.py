"""0.4.0 is additive: a read-back scenario semis 0.3.0 applied writes the same bytes.

``tests/fixtures/compat-0.3.0/seeds`` holds the files semis 0.3.0 wrote for
``compat.yaml`` (an ``existing:`` table read by ``where:``, ``fill:``, an override, a
hierarchy, an enum) applied to ``schema.sql`` in a fresh schema.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import LiteralString, cast

import psycopg
import pytest

from fraiseql_semis import ScenarioManager, SchemaFacts, TableCodes

FIXTURE = Path(__file__).parent.parent / "fixtures" / "compat-0.3.0"
DDL = cast("LiteralString", (FIXTURE / "schema.sql").read_text())
CODES = {
    "semis_compat.tb_country": 0x30010001,
    "semis_compat.tb_city": 0x30010002,
    "semis_compat.tb_location": 0x30010003,
}


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    """The fixture's schema, built inside a transaction the test's end rolls back."""
    with psycopg.connect(database_url) as connection:
        connection.execute("DROP SCHEMA IF EXISTS semis_compat CASCADE")
        connection.execute(DDL)
        yield connection
        connection.rollback()


def test_a_0_3_0_read_back_scenario_applies_byte_identically(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    manager = ScenarioManager(SchemaFacts.from_source(DDL, table_codes=TableCodes(CODES)))
    run = manager.apply(
        manager.load(FIXTURE / "compat.yaml"), tmp_path / "out", connection=connection
    )
    written = {seed.path.name: seed.path.read_bytes() for seed in run.seeds}
    kept = {path.name: path.read_bytes() for path in (FIXTURE / "seeds").iterdir()}
    assert written == kept
