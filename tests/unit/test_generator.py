"""FakeDataGenerator: a row assembled from facts and the UUID encoding."""

from unittest.mock import ANY
from uuid import UUID

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import RowContractError
from fraiseql_semis.generator import FakeDataGenerator
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
    assert str(row["id"]).endswith("-5001-0001-0000-000000000042")
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
