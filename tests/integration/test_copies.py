"""copies: applied, every child holds the value of the parent row its key points at."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from fraiseql_semis import ScenarioManager, SchemaFacts, TableCodes
from fraiseql_semis.staging import Staging
from tests.ddl import COPIES, COPIES_CODES

SCHEMA = "semis_copies"
STAGING = f"{SCHEMA}_prep"
DDL = (
    COPIES.replace("SCHEMA tenant", f"SCHEMA {SCHEMA}")
    .replace("tenant.", f"{SCHEMA}.")
    .replace("prep_seed", STAGING)
)
FACTS = SchemaFacts.from_source(
    DDL,
    table_codes=TableCodes(
        {name.replace("tenant.", f"{SCHEMA}."): code for name, code in COPIES_CODES.items()}
    ),
)
SCENARIO = f"""\
scenario_id: 0x5005
name: contacts
mode: {{mode}}
seed: 42
tables:
  - name: {SCHEMA}.tb_organization
    count: 6
    overrides: {{{{fk_parent_organization: null}}}}
  - name: {SCHEMA}.tb_contact
    count: 12
    overrides: {{{{fk_referrer: null}}}}
    copies:
      tenant_id: fk_customer_org.id
"""


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    """The tables in a throwaway schema, and the caller's connection, rolled back."""
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS ltree")
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {STAGING} CASCADE")
        admin.execute(DDL)
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {SCHEMA}, {STAGING} CASCADE")


def _apply(connection: psycopg.Connection, tmp_path: Path, mode: str) -> None:
    path = tmp_path / "contacts.yaml"
    path.write_text(SCENARIO.format(mode=mode))
    manager = ScenarioManager(FACTS, staging=Staging(STAGING))
    manager.apply(manager.load(path), tmp_path / "out", connection=connection)


def _mismatches(connection: psycopg.Connection, schema: str, joined_on: str) -> int:
    (mismatched,) = connection.execute(
        f"SELECT count(*) FROM {schema}.tb_contact AS c JOIN {schema}.tb_organization AS p "
        f"ON {joined_on} WHERE c.tenant_id IS DISTINCT FROM p.id"
    ).fetchone() or (None,)
    return mismatched


def test_read_back_copies_the_parents_id(connection: psycopg.Connection, tmp_path: Path) -> None:
    _apply(connection, tmp_path, "read-back")
    assert _mismatches(connection, SCHEMA, "p.pk_organization = c.fk_customer_org") == 0
    (count,) = connection.execute(f"SELECT count(*) FROM {SCHEMA}.tb_contact").fetchone() or (0,)
    assert count == 12


def test_prep_seed_copies_the_parents_id(connection: psycopg.Connection, tmp_path: Path) -> None:
    _apply(connection, tmp_path, "prep-seed")
    assert _mismatches(connection, STAGING, "p.id = c.fk_customer_org_id") == 0
    (count,) = connection.execute(f"SELECT count(*) FROM {STAGING}.tb_contact").fetchone() or (0,)
    assert count == 12
