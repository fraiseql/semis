"""How confiture reads a self-referencing table and its ltree path (ARCHITECTURE §5's premises)."""

from confiture.platform import column_facts, dependency_order, parse_schema

from tests.ddl import HIERARCHY

MODEL = parse_schema(HIERARCHY)
TABLE = "catalog.tb_location"


def test_a_self_reference_is_no_dependency_cycle() -> None:
    assert [ref.display for ref in dependency_order(MODEL)] == [TABLE, "prep_seed.tb_location"]


def test_a_self_fk_names_its_own_table_and_pk() -> None:
    reference = column_facts(MODEL, TABLE, "fk_parent_location").foreign_key
    assert reference is not None
    assert (reference.table.display, reference.column) == (TABLE, "pk_location")


def test_an_ltree_column_reads_as_ltree() -> None:
    facts = column_facts(MODEL, TABLE, "path")
    assert (facts.type_key, facts.raw_sql_type, facts.not_null) == ("ltree", "ltree", False)
