"""SchemaFacts: confiture's model as semis reads it, plus the table codes."""

from pathlib import Path

import pytest
from confiture.platform import ConfigurationError, NotInModelError, parse_schema

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import CodeRegistryError
from fraiseql_semis.schema import LiveSchema, SchemaFacts, qualified
from tests.ddl import CODES, TRINITY


def _facts() -> SchemaFacts:
    return SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))


def test_from_source_takes_ddl_text() -> None:
    table = _facts().facts_for("catalog.tb_continent")
    assert (table.ref.display, table.table_code, table.natural_id) == (
        "catalog.tb_continent",
        0x02030405,
        "id",
    )


def test_writable_columns_exclude_the_identity_pk() -> None:
    columns = [column.name for column in _facts().facts_for("catalog.tb_continent").columns]
    assert columns == ["id", "identifier", "name"]


def test_generated_column_is_excluded() -> None:
    columns = [column.name for column in _facts().facts_for("catalog.tb_country").columns]
    assert "full_label" not in columns


def test_hints_name_the_surrogate_and_the_natural_id() -> None:
    table = _facts().facts_for("catalog.tb_country")
    assert (table.surrogate_pk, table.natural_id) == ("pk_country", "id")


def test_facts_carry_the_fk_target() -> None:
    columns = {column.name: column for column in _facts().facts_for("catalog.tb_country").columns}
    reference = columns["fk_continent"].foreign_key
    assert reference is not None
    assert (reference.table.display, reference.column) == ("catalog.tb_continent", "pk_continent")


def test_insert_order_puts_parents_first() -> None:
    assert [ref.display for ref in _facts().insert_order()] == [
        "catalog.tb_continent",
        "catalog.tb_country",
        "prep_seed.tb_continent",
        "prep_seed.tb_country",
    ]


def test_a_table_the_model_lacks_is_confitures_refusal() -> None:
    with pytest.raises(NotInModelError, match=r"catalog\.tb_city"):
        _facts().facts_for("catalog.tb_city")


def test_a_table_without_a_code_is_refused() -> None:
    facts = SchemaFacts.from_source(
        TRINITY, table_codes=TableCodes({"catalog.tb_continent": 0x02030405})
    )
    with pytest.raises(
        CodeRegistryError,
        match=r"^catalog\.tb_country has no table code\n"
        r"Hint: Register it schema-qualified, with a hex code of its own\.$",
    ):
        facts.facts_for("catalog.tb_country")


def test_from_env_outside_a_project_propagates_confitures_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        SchemaFacts.from_env("development", project_dir=tmp_path, table_codes=TableCodes(CODES))


def test_a_table_the_ddl_leaves_unqualified_is_named_in_public() -> None:
    """confiture's DDL reader displays it bare and a live database `public.`-qualified:
    semis names it the same from both."""
    ddl = """
    CREATE TABLE tb_thing (
        pk_thing INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        id UUID NOT NULL UNIQUE
    );
    """
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({"public.tb_thing": 0x0A000001}))
    assert [facts.facts_for("public.tb_thing").table_code, facts.ref("tb_thing")] == [
        0x0A000001,
        None,
    ]


def test_references_into_match_a_target_by_its_identity() -> None:
    """A key whose REFERENCES leaves the schema out still names its table: confiture's
    identity folds the default schema in, whichever reader built the model."""
    model = parse_schema(
        "CREATE TABLE tb_a (pk_a int PRIMARY KEY);"
        "CREATE TABLE tb_b (pk_b int PRIMARY KEY, fk_a int REFERENCES tb_a);"
    )
    (target,) = (ref for ref in model.tables if ref.name == "tb_a")
    (reference,) = LiveSchema(model).references_into([target])
    assert (qualified(reference.table), reference.target) == ("public.tb_b", target)


VIEWS = """
CREATE SCHEMA app;
CREATE TABLE app.tb_item (pk_item bigint PRIMARY KEY, id uuid NOT NULL, tenant_id uuid);
CREATE VIEW app.v_item AS SELECT id, tenant_id FROM app.tb_item;
CREATE VIEW app.v_ids AS SELECT id FROM app.v_item;
CREATE MATERIALIZED VIEW app.mv_item AS SELECT id FROM app.tb_item;
CREATE TABLE app.tv_item AS SELECT pk_item, id, tenant_id FROM app.tb_item;
"""


def _views() -> SchemaFacts:
    return SchemaFacts.from_source(VIEWS, table_codes=TableCodes({}))


def test_views_are_the_views_matviews_and_tviews_by_name() -> None:
    assert [(qualified(ref), ref.kind) for ref in _views().views()] == [
        ("app.mv_item", "matview"),
        ("app.tv_item", "tview"),
        ("app.v_ids", "view"),
        ("app.v_item", "view"),
    ]


def test_a_view_reads_the_tables_under_the_views_it_reads() -> None:
    facts = _views()
    (ids,) = (ref for ref in facts.views() if ref.name == "v_ids")
    assert facts.reads(ids) == {("app", "v_item"), ("app", "tb_item")}


def test_a_view_columns_are_confitures() -> None:
    facts = _views()
    (tview,) = (ref for ref in facts.views() if ref.kind == "tview")
    assert facts.view_columns(tview) == ("pk_item", "id", "tenant_id")
