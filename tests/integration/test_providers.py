"""PostgreSQL judges the provider library: every rule's values, into the types and CHECKs
a column declares."""

from collections.abc import Iterator
from pathlib import Path
from typing import LiteralString

import psycopg
import pytest

from fraiseql_semis import CustomProviderRegistry, FakeDataGenerator, SchemaFacts, TableCodes, seeds
from fraiseql_semis.providers import SHIPPED

SCHEMA = "semis_prov"
TABLES: dict[str, LiteralString] = {
    "i18n": f"""
CREATE TABLE {SCHEMA}.tb_i18n (
    pk_i18n BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    country_code CHAR(2) NOT NULL CHECK (country_code ~ '^[A-Z]{{2}}$'),
    iso3 CHAR(3) NOT NULL CHECK (iso3 ~ '^[A-Z]{{3}}$'),
    country_name TEXT NOT NULL,
    lang VARCHAR(2) NOT NULL CHECK (lang ~ '^[a-z]{{2}}$'),
    language_name TEXT NOT NULL,
    locale VARCHAR(10) NOT NULL CHECK (locale ~ '^[a-z]{{2,3}}_[A-Z]{{2}}$'),
    currency CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
    currency_name TEXT NOT NULL,
    currency_symbol VARCHAR(4) NOT NULL,
    timezone TEXT NOT NULL CHECK ((now() AT TIME ZONE timezone) IS NOT NULL)
);""",
    "organization": f"""
CREATE TABLE {SCHEMA}.tb_organization (
    pk_organization BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    legal_name TEXT NOT NULL,
    siren CHAR(9) NOT NULL CHECK (siren ~ '^[0-9]{{9}}$'),
    legal_identifier TEXT NOT NULL CHECK (legal_identifier ~ '^[0-9]{{14}}$'),
    vat_identifier TEXT NOT NULL CHECK (vat_identifier ~ '^FR[0-9]{{11}}$'),
    job_title TEXT NOT NULL,
    department TEXT NOT NULL,
    email_address TEXT NOT NULL UNIQUE CHECK (email_address ~ '^[^@]+@[^@]+$'),
    website TEXT NOT NULL CHECK (website ~ '^https?://'),
    office_phone VARCHAR(20) NOT NULL CHECK (office_phone ~ '^\\+[0-9]+$')
);""",
}
DDL: LiteralString = f"CREATE SCHEMA {SCHEMA};" + TABLES["i18n"] + TABLES["organization"]
CODES = TableCodes(
    {
        f"{SCHEMA}.tb_i18n": 0x21,
        f"{SCHEMA}.tb_organization": 0x22,
    }
)
TABLE_OF: dict[str, LiteralString] = {
    "i18n": "tb_i18n",
    "organization": "tb_organization",
}


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(DDL)
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


@pytest.mark.parametrize("library", sorted(SHIPPED))
def test_every_rules_values_are_accepted(
    library: str, connection: psycopg.Connection, tmp_path: Path
) -> None:
    facts = SchemaFacts.from_source(DDL, table_codes=CODES)
    table = f"{SCHEMA}.{TABLE_OF[library]}"
    columns = facts.facts_for(table).columns
    unmatched = [
        rule.provider
        for rule in SHIPPED[library].rules
        if not any(rule.matches(column) for column in columns)
    ]
    registry = CustomProviderRegistry()
    registry.register_library(SHIPPED[library])
    rows = FakeDataGenerator(facts, 0x5001, seed=42, providers=registry).generate_rows(
        table, count=200
    )
    seed = seeds.write(tmp_path / "rows.sql", table, rows, facts=facts, mode="read-back")
    seeds.apply(connection, [seed.path])
    found = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert (unmatched, found) == ([], (200,))
