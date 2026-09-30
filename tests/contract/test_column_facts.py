"""What ColumnFacts says about a column — the spellings the row contract reads."""

from confiture.platform import column_facts, parse_schema

from tests.ddl import CONTRACT

MODEL = parse_schema(CONTRACT)
TABLE = "catalog.tb_product"


def test_type_key_spells_a_declared_length_as_the_row_contract_reads_it() -> None:
    assert column_facts(MODEL, TABLE, "name").type_key == "varchar(50)"
    assert column_facts(MODEL, TABLE, "code").type_key == "char(2)"


def test_raw_sql_type_spells_char_as_bpchar() -> None:
    """Why the length rule reads type_key rather than raw_sql_type."""
    assert column_facts(MODEL, TABLE, "code").raw_sql_type == "bpchar(2)"


def test_an_enum_column_carries_its_labels_in_order() -> None:
    assert column_facts(MODEL, TABLE, "status").enum_values == ("draft", "live", "gone")


def test_a_not_null_column_with_a_default_reports_both() -> None:
    facts = column_facts(MODEL, TABLE, "status")
    assert (facts.not_null, facts.default) == (True, "'draft'")


def test_type_key_names_the_families_the_provider_rules_match() -> None:
    """A library rule matches ``type_key`` without its modifiers: these are its spellings."""
    model = parse_schema(
        "CREATE SCHEMA t; CREATE TABLE t.x (a MACADDR, b INET, c CHARACTER VARYING(3),"
        " d CHAR(2), e TEXT, f CITEXT);"
    )
    keys = [column_facts(model, "t.x", name).type_key for name in "abcdef"]
    assert keys == ["macaddr", "inet", "varchar(3)", "char(2)", "text", "citext"]
