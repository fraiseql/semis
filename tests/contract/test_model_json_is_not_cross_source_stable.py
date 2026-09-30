"""DDL and a live database read one schema into models that disagree (ARCHITECTURE §8).

The pin carries its source kind because of these measurements: ``to_json()`` is stable
within a source and not across sources, and the column facts semis digests spell a
default and a check differently by source. When confiture closes the gap, these fail,
and the incomparable-pin refusal can be revisited.
"""

from collections.abc import Iterator

import psycopg
import pytest
from confiture.platform import column_facts, introspect, parse_schema

pytestmark = pytest.mark.integration

SCHEMA = "semis_xsrc"
DDL = f"""
CREATE SCHEMA {SCHEMA};
CREATE TYPE {SCHEMA}.status AS ENUM ('draft', 'live');
CREATE TABLE {SCHEMA}.tb_country (
    pk_country BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    iso_code CHAR(2) NOT NULL CHECK (iso_code ~ '^[A-Z]{{2}}$'),
    label VARCHAR(255),
    status {SCHEMA}.status NOT NULL DEFAULT 'draft'
);
"""
TABLE = f"{SCHEMA}.tb_country"


@pytest.fixture(scope="module")
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        connection.execute(DDL)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


def test_to_json_is_stable_within_a_source(database: str) -> None:
    assert parse_schema(DDL).to_json() == parse_schema(DDL).to_json()
    live = [introspect(database, schemas=[SCHEMA]).to_json() for _ in range(2)]
    assert live[0] == live[1]


def test_to_json_differs_between_ddl_and_live(database: str) -> None:
    assert parse_schema(DDL).to_json() != introspect(database, schemas=[SCHEMA]).to_json()


@pytest.mark.parametrize(
    ("column", "fact", "from_ddl", "from_live"),
    [
        ("status", "default", "'draft'", f"CAST('draft' AS {SCHEMA}.status)"),
        (
            "iso_code",
            "checks",
            ("iso_code ~ '^[A-Z]{2}$'",),
            ("iso_code ~ CAST('^[A-Z]{2}$' AS text)",),
        ),
    ],
)
def test_the_facts_semis_digests_are_spelled_by_source(
    database: str, column: str, fact: str, from_ddl: object, from_live: object
) -> None:
    ddl = column_facts(parse_schema(DDL), TABLE, column)
    live = column_facts(introspect(database, schemas=[SCHEMA]), TABLE, column)
    assert (getattr(ddl, fact), getattr(live, fact)) == (from_ddl, from_live)
