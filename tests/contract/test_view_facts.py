"""What confiture says of a view, as ``semis coverage`` reads it (confiture#812, #815).

A view's output columns and the relations it reads, through other views, a
materialized view and a pg_tviews TVIEW, from DDL; the live reader is measured by
``tests/integration/test_view_coverage.py``.
"""

import pytest
from confiture.platform import ObjectRef, SchemaError, parse_schema, view_columns, view_reads

DDL = """
CREATE SCHEMA app;
CREATE TABLE app.tb_org (pk_org bigint PRIMARY KEY, id uuid NOT NULL, name text);
CREATE TABLE app.tb_item (pk_item bigint PRIMARY KEY, id uuid NOT NULL, tenant_id uuid,
  fk_org bigint REFERENCES app.tb_org);
CREATE FUNCTION app.item_count() RETURNS bigint LANGUAGE sql
  AS $$ SELECT count(*) FROM app.tb_item $$;
CREATE VIEW app.v_item AS SELECT id, tenant_id FROM app.tb_item;
CREATE VIEW app.v_item_org (id, tenant_id, org) AS
  SELECT v.id, v.tenant_id, o.name FROM app.v_item v JOIN app.tb_org o ON true;
CREATE MATERIALIZED VIEW app.mv_org AS SELECT * FROM app.tb_org;
CREATE TABLE app.tv_item AS SELECT pk_item, id, tenant_id FROM app.tb_item;
CREATE VIEW app.v_count AS SELECT app.item_count() AS items;
CREATE VIEW app.v_elsewhere AS SELECT * FROM outside.tb_x;
"""
MODEL = parse_schema(DDL)


def _view(name: str) -> ObjectRef:
    return next(ref for ref in (*MODEL.views, *MODEL.tviews) if ref.name == name)


def _reads(name: str) -> set[tuple[str, tuple[str, str]]]:
    return {(ref.kind, ref.identity) for ref in view_reads(MODEL, _view(name))}


def test_views_hold_views_and_materialized_views_and_tviews_hold_tviews() -> None:
    views = {(ref.kind, ref.name) for ref in MODEL.views}
    tviews = {(ref.kind, ref.name) for ref in MODEL.tviews}
    assert ("matview", "mv_org") in views
    assert ("view", "v_item") in views
    assert tviews == {("tview", "tv_item")}


def test_a_tview_is_not_a_table() -> None:
    assert "tv_item" not in {ref.name for ref in MODEL.tables}


def test_reads_follow_a_view_to_the_tables_it_reads() -> None:
    assert _reads("v_item_org") == {
        ("view", ("app", "v_item")),
        ("table", ("app", "tb_item")),
        ("table", ("app", "tb_org")),
    }


def test_a_tview_reads_its_tables() -> None:
    assert _reads("tv_item") == {("table", ("app", "tb_item"))}


def test_reads_do_not_follow_a_routine_body() -> None:
    assert _reads("v_count") == set()


def test_a_relation_the_model_lacks_is_read_as_a_relation() -> None:
    assert _reads("v_elsewhere") == {("relation", ("outside", "tb_x"))}


def test_columns_follow_a_column_list_and_expand_a_star() -> None:
    assert view_columns(MODEL, _view("v_item_org")) == ("id", "tenant_id", "org")
    assert view_columns(MODEL, _view("mv_org")) == ("pk_org", "id", "name")
    assert view_columns(MODEL, _view("tv_item")) == ("pk_item", "id", "tenant_id")


def test_a_star_the_model_cannot_expand_is_a_schema_error_naming_the_view() -> None:
    with pytest.raises(SchemaError, match=r"app\.v_elsewhere"):
        view_columns(MODEL, _view("v_elsewhere"))
