"""What the reset's safety check reads: every foreign key into a table, from any schema.

``introspect`` with no schemas reads every user schema, and names each foreign key with
its constraint, its columns, the table it points at, the columns there — even when the
DDL named none, the referenced primary key's — and its ``ON DELETE`` (ARCHITECTURE D40).
A partitioned table's key is reported once, on the partitioned table.
"""

from collections.abc import Iterator

import psycopg
import pytest
from confiture.platform import RelationName, introspect

pytestmark = pytest.mark.integration

TARGET = "semis_refs_a"
OTHER = "semis_refs_b"
DDL = f"""
CREATE SCHEMA {TARGET};
CREATE SCHEMA {OTHER};
CREATE TABLE {TARGET}.tb_org (
    pk_org BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE
);
CREATE TABLE {OTHER}.tb_user (fk_org BIGINT REFERENCES {TARGET}.tb_org ON DELETE CASCADE);
CREATE TABLE {OTHER}.tb_log (
    fk_org BIGINT,
    CONSTRAINT log_org FOREIGN KEY (fk_org) REFERENCES {TARGET}.tb_org (pk_org) ON DELETE SET NULL
);
CREATE TABLE {OTHER}.tb_part (fk_org BIGINT REFERENCES {TARGET}.tb_org, k INT)
    PARTITION BY RANGE (k);
CREATE TABLE {OTHER}.tb_part_1 PARTITION OF {OTHER}.tb_part FOR VALUES FROM (0) TO (10);
"""


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    """The schemas built inside a transaction that is rolled back."""
    with psycopg.connect(database_url) as connection:
        connection.execute(DDL)
        yield connection
        connection.rollback()


def test_every_key_into_a_table_is_named_from_any_schema(connection: psycopg.Connection) -> None:
    model = introspect(connection)
    keys = sorted(
        (
            ref.display,
            constraint.name,
            constraint.columns,
            constraint.ref_columns,
            constraint.on_delete,
        )
        for ref, table in model.tables.items()
        for constraint in table.constraints
        if constraint.ref_table == RelationName(TARGET, "tb_org")
    )
    assert keys == [
        (f"{OTHER}.tb_log", "log_org", ("fk_org",), ("pk_org",), "SET NULL"),
        (f"{OTHER}.tb_part", "tb_part_fk_org_fkey", ("fk_org",), ("pk_org",), None),
        (f"{OTHER}.tb_user", "tb_user_fk_org_fkey", ("fk_org",), ("pk_org",), "CASCADE"),
    ]
