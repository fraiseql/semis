"""Tenants, applied: every row of a tenant points at parents of that tenant."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from fraiseql_semis import ScenarioManager, SchemaFacts, TableCodes
from fraiseql_semis.staging import Staging
from fraiseql_semis.tenancy import TenancyRule
from tests.ddl import TENANTS, TENANTS_CODES

SCHEMA = "semis_tenants"
STAGING = f"{SCHEMA}_prep"
DDL = (
    TENANTS.replace("SCHEMA app", f"SCHEMA {SCHEMA}")
    .replace("app.", f"{SCHEMA}.")
    .replace("prep_seed", STAGING)
)
FACTS = SchemaFacts.from_source(
    DDL,
    table_codes=TableCodes(
        {name.replace("app.", f"{SCHEMA}."): code for name, code in TENANTS_CODES.items()}
    ),
)
RULE = TenancyRule("tenant_id", table=f"{SCHEMA}.tb_organization")
TABLES = f"""\
  - name: {SCHEMA}.tb_category
    count: 2
  - name: {SCHEMA}.tb_contact
    count: 6
  - name: {SCHEMA}.tb_project
    count: 4
  - name: {SCHEMA}.tb_task
    count: 12
  - name: {SCHEMA}.tb_folder
    count: 9
    hierarchy: {{parent: fk_parent_folder, roots: 3, fan_out: 2}}
"""
WRITTEN = f"""\
scenario_id: 0x5010
name: two_tenants
mode: read-back
seed: 42
tables:
  - name: {SCHEMA}.tb_organization
    count: 3
    overrides: {{fk_parent_organization: null}}
{TABLES}"""
READ = f"""\
scenario_id: 0x5011
name: tenants_read
mode: read-back
seed: 42
existing:
  - name: {SCHEMA}.tb_organization
    where: {{identifier: [org-c, org-a]}}
tables:
{TABLES}"""


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    """The tables in a throwaway schema, and the caller's connection, rolled back."""
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {STAGING} CASCADE")
        admin.execute(DDL)
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {SCHEMA}, {STAGING} CASCADE")


def _apply(connection: psycopg.Connection, tmp_path: Path, text: str) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(text)
    manager = ScenarioManager(FACTS, staging=Staging(STAGING), tenancy=RULE)
    manager.apply(manager.load(path), tmp_path / "out", connection=connection)


def _one(connection: psycopg.Connection, query: str) -> object:
    (value,) = connection.execute(query).fetchone() or (None,)
    return value


def _crossings(connection: psycopg.Connection) -> dict[str, object]:
    """Per key, the rows whose parent is of another tenant: none, when spread."""
    s = SCHEMA
    return {
        "tasks assigned": _one(connection, f"SELECT count(fk_assignee) FROM {s}.tb_task"),
        "contact→organization": _one(
            connection,
            f"SELECT count(*) FROM {s}.tb_contact c JOIN {s}.tb_organization o "
            "ON o.pk_organization = c.fk_organization WHERE c.tenant_id <> o.id",
        ),
        "task→project": _one(
            connection,
            f"SELECT count(*) FROM {s}.tb_task t JOIN {s}.tb_project p "
            "ON p.pk_project = t.fk_project JOIN {s}.tb_contact c ON c.pk_contact = "
            "t.fk_assignee WHERE p.tenant_id <> c.tenant_id".replace("{s}", s),
        ),
        "folder→parent": _one(
            connection,
            f"SELECT count(*) FROM {s}.tb_folder f JOIN {s}.tb_folder p "
            "ON p.pk_folder = f.fk_parent_folder WHERE f.tenant_id <> p.tenant_id",
        ),
    }


def test_a_written_tenant_table_s_rows_are_the_tenants(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    _apply(connection, tmp_path, WRITTEN)
    tenants = _one(connection, f"SELECT count(DISTINCT tenant_id) FROM {SCHEMA}.tb_project")
    assert (tenants, _crossings(connection)) == (
        3,
        {"tasks assigned": 12, "contact→organization": 0, "task→project": 0, "folder→parent": 0},
    )


def test_an_existing_tenant_table_s_named_rows_are_the_tenants(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    connection.execute(
        f"INSERT INTO {SCHEMA}.tb_organization (id, identifier, name) VALUES "
        "(gen_random_uuid(), 'org-a', 'A'), (gen_random_uuid(), 'org-b', 'B'), "
        "(gen_random_uuid(), 'org-c', 'C')"
    )
    _apply(connection, tmp_path, READ)
    held = _one(
        connection,
        f"SELECT array_agg(DISTINCT o.identifier ORDER BY o.identifier) FROM {SCHEMA}.tb_project p "
        f"JOIN {SCHEMA}.tb_organization o ON o.id = p.tenant_id",
    )
    assert (held, _crossings(connection)) == (
        ["org-a", "org-c"],
        {"tasks assigned": 12, "contact→organization": 0, "task→project": 0, "folder→parent": 0},
    )
