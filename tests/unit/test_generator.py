"""FakeDataGenerator: a row assembled from facts and the UUID encoding."""

from unittest.mock import ANY
from uuid import UUID

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import RowContractError, ScenarioError
from fraiseql_semis.faker_provider import CustomProviderRegistry
from fraiseql_semis.generator import FakeDataGenerator, Row
from fraiseql_semis.providers import i18n
from fraiseql_semis.rows import check_row
from fraiseql_semis.schema import SchemaFacts
from fraiseql_semis.uuid_generator import SemanticUUIDGenerator
from tests.ddl import CODES, CONTRACT, CONTRACT_CODES, TRINITY

FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))


def test_row_carries_the_encoded_id_and_no_pk() -> None:
    rows = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42).generate_rows(
        "catalog.tb_continent", count=3
    )
    assert [SemanticUUIDGenerator.decode(UUID(str(row["id"]))) for row in rows] == [
        (0x02030405, 0x5001, 1, sequence) for sequence in (1, 2, 3)
    ]
    assert all("pk_continent" not in row for row in rows)


def _rows(scenario_id: int, seed: int, count: int = 3) -> list[dict[str, object]]:
    return list(
        FakeDataGenerator(FACTS, scenario_id=scenario_id, seed=seed).generate_rows(
            "catalog.tb_continent", count=count
        )
    )


def test_two_runs_with_one_seed_agree() -> None:
    assert _rows(0x5001, seed=42) == _rows(0x5001, seed=42)


def test_slug_carries_the_scenario() -> None:
    first = {row["identifier"] for row in _rows(0x5001, seed=42)}
    second = {row["identifier"] for row in _rows(0x5002, seed=42)}
    assert first.isdisjoint(second)


def test_slug_reads_back_against_the_uuid_text() -> None:
    row = _rows(0x5001, seed=42, count=0x42)[-1]
    assert str(row["id"]).endswith("-5001-8001-8000-000000000042")
    assert str(row["identifier"]).endswith("-5001-42")


def _generator_for(ddl: str, table: str) -> FakeDataGenerator:
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({table: 0x0A}))
    return FakeDataGenerator(facts, scenario_id=0x5001, seed=42)


def test_generate_rows_refuses_an_incompletable_row() -> None:
    ddl = "CREATE TABLE catalog.tb_host (id UUID NOT NULL, address INET NOT NULL);"
    with pytest.raises(RowContractError, match=r"catalog\.tb_host\.address is NOT NULL"):
        list(_generator_for(ddl, "catalog.tb_host").generate_rows("catalog.tb_host", count=1))


PRODUCTS = list(
    FakeDataGenerator(
        SchemaFacts.from_source(CONTRACT, table_codes=TableCodes(CONTRACT_CODES)),
        scenario_id=0x5001,
        seed=42,
    ).generate_rows("catalog.tb_product", count=20)
)


def test_a_not_null_column_with_a_default_is_omitted() -> None:
    assert all("status" not in row for row in PRODUCTS)


def test_a_nullable_column_with_a_default_is_omitted() -> None:
    ddl = "CREATE TABLE catalog.tb_note (id UUID NOT NULL, body TEXT DEFAULT 'none');"
    assert list(_generator_for(ddl, "catalog.tb_note").generate_rows("catalog.tb_note", 1)) == [
        {"id": ANY}
    ]


def test_the_natural_id_and_slug_are_written_despite_a_default() -> None:
    ddl = """CREATE TABLE catalog.tb_tag (
        pk_tag BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        id UUID NOT NULL DEFAULT gen_random_uuid(),
        identifier TEXT NOT NULL DEFAULT 'tag'
    );"""
    [row] = _generator_for(ddl, "catalog.tb_tag").generate_rows("catalog.tb_tag", 1)
    assert SemanticUUIDGenerator.decode(UUID(str(row["id"]))).table_code == 0x0A
    assert str(row["identifier"]).endswith("-5001-1")


def test_a_trusted_column_is_omitted_and_not_refused() -> None:
    ddl = "CREATE TABLE catalog.tb_host (id UUID NOT NULL, address INET NOT NULL);"
    rows = _generator_for(ddl, "catalog.tb_host").generate_rows(
        "catalog.tb_host", 2, trusted=frozenset({"address"})
    )
    assert all("address" not in row for row in rows)


def test_every_row_satisfies_the_contract() -> None:
    table = SchemaFacts.from_source(CONTRACT, table_codes=TableCodes(CONTRACT_CODES)).facts_for(
        "catalog.tb_product"
    )
    assert [check_row(table, row) for row in PRODUCTS] == [[]] * len(PRODUCTS)


def test_columns_are_the_same_for_every_row() -> None:
    assert {tuple(row) for row in PRODUCTS} == {tuple(PRODUCTS[0])}


CUSTOMER = "shop.tb_customer"
SHOP = """CREATE SCHEMA shop;
CREATE TABLE shop.tb_customer (pk_customer bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE, identifier text NOT NULL UNIQUE,
  name text NOT NULL, deleted_at timestamptz, created_by uuid, country_code char(2));"""


def _customers(registry: CustomProviderRegistry | None = None, **drawn: object) -> list[Row]:
    facts = SchemaFacts.from_source(SHOP, table_codes=TableCodes({CUSTOMER: 0x0B}))
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42, providers=registry)
    return list(generator.generate_rows(CUSTOMER, 3, **drawn))  # ty: ignore[invalid-argument-type]


def test_a_nullable_column_nobody_names_is_null() -> None:
    """fraiseql/semis#1: no row arrives soft-deleted, no audit column points at nobody."""
    rows = _customers()
    assert {(row["deleted_at"], row["created_by"]) for row in rows} == {(None, None)}
    assert all(row["identifier"] and row["name"] for row in rows)


def test_a_library_rule_does_not_name_a_column() -> None:
    """A library says how a column is filled when it is, not whether: a nullable column
    its rule matches is left NULL."""
    registry = CustomProviderRegistry()
    registry.register_library(i18n.LIBRARY)
    assert {row["country_code"] for row in _customers(registry)} == {None}


def test_an_overridden_nullable_column_is_written() -> None:
    rows = _customers(overrides={"deleted_at": None, "created_by": "a"})
    assert {row["created_by"] for row in rows} == {"a"}


def test_fill_draws_the_columns_it_lists() -> None:
    rows = _customers(fill=frozenset({"deleted_at"}))
    assert all(row["deleted_at"] is not None for row in rows)
    assert {row["created_by"] for row in rows} == {None}


def test_a_provider_by_name_draws_a_nullable_column() -> None:
    registry = CustomProviderRegistry()
    registry.register_column(CUSTOMER, "created_by", lambda _faker, _column: "me")
    assert {row["created_by"] for row in _customers(registry)} == {"me"}


def test_fill_all_draws_every_column_as_0_1_0_did() -> None:
    """0.1.0 drew a nullable column as it draws a NOT NULL one, from the same stream."""
    every = SHOP.replace("timestamptz,", "timestamptz NOT NULL,").replace(
        "uuid, country_code char(2))", "uuid NOT NULL, country_code char(2) NOT NULL)"
    )
    facts = SchemaFacts.from_source(every, table_codes=TableCodes({CUSTOMER: 0x0B}))
    drawn = list(FakeDataGenerator(facts, scenario_id=0x5001, seed=42).generate_rows(CUSTOMER, 3))
    assert _customers(fill="all") == drawn


REGIONS = SHOP.replace(
    "country_code char(2));",
    "country_code char(2), fk_region bigint REFERENCES shop.tb_customer (pk_customer),"
    " note text DEFAULT 'none');",
)


@pytest.mark.parametrize(
    ("column", "reason"),
    [
        ("nowhere", "is not a column semis writes"),
        ("pk_customer", "is not a column semis writes"),
        ("name", "is NOT NULL, so semis always draws it"),
        ("fk_region", "is a foreign key, whose value comes from the run's mode"),
        ("note", "has a default, which PostgreSQL applies"),
        ("created_by", "is trusted to a trigger, which fills it"),
    ],
)
def test_fill_naming_a_column_semis_would_not_leave_null_is_refused(
    column: str, reason: str
) -> None:
    facts = SchemaFacts.from_source(REGIONS, table_codes=TableCodes({CUSTOMER: 0x0B}))
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)
    with pytest.raises(ScenarioError, match=rf"shop\.tb_customer fills {column}, which {reason}"):
        generator.generate_rows(
            CUSTOMER, 3, fill=frozenset({column}), trusted=frozenset({"created_by"})
        )


OPTIONAL = SchemaFacts.from_source(
    TRINITY.replace("fk_continent BIGINT NOT NULL REFERENCES", "fk_continent BIGINT REFERENCES"),
    table_codes=TableCodes(CODES),
)


@pytest.mark.parametrize(
    ("counts", "trusted", "overrides", "left"),
    [
        ({"catalog.tb_country": 1}, frozenset(), {}, ("fk_continent",)),
        ({"catalog.tb_country": 1, "catalog.tb_continent": 1}, frozenset(), {}, ()),
        ({"catalog.tb_country": 1}, frozenset({"fk_continent"}), {}, ()),
        ({"catalog.tb_country": 1}, frozenset(), {"fk_continent": None}, ()),
    ],
    ids=["no-parent", "parent", "trusted", "overridden-null"],
)
def test_a_key_is_left_null_only_when_the_run_has_no_parent_and_nobody_names_it(
    counts: dict[str, int],
    trusted: frozenset[str],
    overrides: dict[str, object],
    left: tuple[str, ...],
) -> None:
    generator = FakeDataGenerator(OPTIONAL, scenario_id=0x5001, seed=42)
    assert (
        generator.left_null(
            "catalog.tb_country", trusted=trusted, overrides=overrides, counts=counts
        )
        == left
    )
