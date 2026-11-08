"""Which views a scenario's tables reach, and how a view's count is marked."""

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.schema import SchemaFacts, qualified
from fraiseql_semis.view_coverage import ViewCount, reached

DDL = """
CREATE SCHEMA app;
CREATE TABLE app.tb_org (pk_org bigint PRIMARY KEY, id uuid NOT NULL);
CREATE TABLE app.tb_item (pk_item bigint PRIMARY KEY, id uuid NOT NULL, tenant_id uuid,
  fk_org bigint REFERENCES app.tb_org);
CREATE TABLE app.tb_other (pk_other bigint PRIMARY KEY, id uuid NOT NULL);
CREATE FUNCTION app.items() RETURNS SETOF app.tb_item LANGUAGE sql
  AS $$ SELECT * FROM app.tb_item $$;
CREATE VIEW app.v_item AS SELECT id, tenant_id FROM app.tb_item;
CREATE VIEW app.v_item_ids AS SELECT id FROM app.v_item;
CREATE VIEW app.v_other AS SELECT id FROM app.tb_other;
CREATE VIEW app.v_through_routine AS SELECT id FROM app.items();
CREATE TABLE app.tv_org AS SELECT pk_org, id FROM app.tb_org;
"""
FACTS = SchemaFacts.from_source(DDL, table_codes=TableCodes({}))


def _reached(*tables: str) -> list[str]:
    return [qualified(view) for view in reached(FACTS, tables)]


def test_a_view_reading_a_written_table_is_reached() -> None:
    assert _reached("app.tb_item") == ["app.v_item", "app.v_item_ids"]


def test_a_tview_reading_a_written_table_is_reached() -> None:
    assert _reached("app.tb_org") == ["app.tv_org"]


def test_a_view_reading_only_tables_not_written_is_not_reached() -> None:
    assert "app.v_other" not in _reached("app.tb_item", "app.tb_org")


def test_a_view_reaching_a_table_only_through_a_routine_is_not_reached() -> None:
    assert "app.v_through_routine" not in _reached("app.tb_item")


@pytest.mark.parametrize(
    ("count", "mark", "single"),
    [
        (ViewCount("app.v", "view", 0, None), "no rows", False),
        (ViewCount("app.v", "view", 3, None), "", False),
        (ViewCount("app.v", "view", 0, 0), "no rows", True),
        (ViewCount("app.v", "view", 3, 0), "no tenant", True),
        (ViewCount("app.v", "view", 3, 1), "one tenant", True),
        (ViewCount("app.v", "view", 3, 2), "", False),
    ],
)
def test_a_count_is_marked_and_a_multi_tenant_view_under_two_tenants_is_single(
    count: ViewCount, mark: str, single: bool
) -> None:
    assert (count.mark, count.single_tenant) == (mark, single)
