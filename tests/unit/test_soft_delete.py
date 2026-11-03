"""``fill: all`` leaves the soft-delete columns NULL: no row arrives deleted.

fraiseql/semis#12.
"""

from pathlib import Path

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.faker_provider import CustomProviderRegistry
from fraiseql_semis.generator import FakeDataGenerator, Row
from fraiseql_semis.scenario import ScenarioManager
from fraiseql_semis.schema import SchemaFacts

CUSTOMER = "shop.tb_customer"
SHOP = """CREATE SCHEMA shop;
CREATE TABLE shop.tb_customer (pk_customer bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL UNIQUE, identifier text NOT NULL UNIQUE, name text NOT NULL,
  note text, deleted_at timestamptz, deleted_by uuid);
CREATE TABLE shop.tb_order (pk_order bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL UNIQUE, fk_customer bigint NOT NULL REFERENCES shop.tb_customer,
  customer_deleted_at timestamptz);"""
CODES = {CUSTOMER: 0x0B, "shop.tb_order": 0x0E}
FACTS = SchemaFacts.from_source(SHOP, table_codes=TableCodes(CODES))


def _rows(registry: CustomProviderRegistry | None = None, **drawn: object) -> list[Row]:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42, providers=registry)
    return list(generator.generate_rows(CUSTOMER, 3, **drawn))  # ty: ignore[invalid-argument-type]


def test_fill_all_leaves_the_soft_delete_columns_null() -> None:
    rows = _rows(fill="all")
    assert {(row["deleted_at"], row["deleted_by"]) for row in rows} == {(None, None)}
    assert all(row["note"] is not None for row in rows)


def test_fill_all_names_the_soft_delete_columns_it_leaves_null() -> None:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    assert generator.left_null(CUSTOMER, fill="all") == ("deleted_at", "deleted_by")


def test_a_fill_list_writes_a_soft_delete_column() -> None:
    rows = _rows(fill=frozenset({"deleted_at"}))
    assert all(row["deleted_at"] is not None for row in rows)
    assert {row["deleted_by"] for row in rows} == {None}


def test_an_override_writes_a_soft_delete_column_under_fill_all() -> None:
    rows = _rows(fill="all", overrides={"deleted_by": "00000000-0000-0000-0000-000000000001"})
    assert {row["deleted_by"] for row in rows} == {"00000000-0000-0000-0000-000000000001"}


def test_a_provider_by_name_writes_a_soft_delete_column_under_fill_all() -> None:
    registry = CustomProviderRegistry()
    registry.register_column(CUSTOMER, "deleted_at", lambda _faker, _column: "2020-01-01")
    assert {row["deleted_at"] for row in _rows(registry, fill="all")} == {"2020-01-01"}


def _check(tmp_path: Path, tables: str) -> tuple[str, ...]:
    path = tmp_path / "customers.yaml"
    path.write_text(
        f"scenario_id: 0x5007\nname: customers\nmode: read-back\nseed: 42\ntables:\n{tables}"
    )
    manager = ScenarioManager(FACTS)
    return manager.check(manager.load(path), no_pin=True)[1:]


def test_the_run_says_fill_all_leaves_the_soft_delete_columns_null(tmp_path: Path) -> None:
    assert _check(tmp_path, f"  - name: {CUSTOMER}\n    count: 3\n    fill: all\n") == (
        "shop.tb_customer leaves deleted_at, deleted_by NULL; fill: all leaves soft-delete "
        "columns NULL: fill: [deleted_at] writes one",
    )


def test_a_copy_of_a_soft_delete_column_fill_all_leaves_null_is_refused(tmp_path: Path) -> None:
    tables = (
        f"  - name: {CUSTOMER}\n    count: 3\n    fill: all\n"
        "  - name: shop.tb_order\n    count: 3\n"
        "    copies: {customer_deleted_at: fk_customer.deleted_at}\n"
    )
    with pytest.raises(ScenarioError, match=r"deleted_at, which the run leaves NULL"):
        _check(tmp_path, tables)
