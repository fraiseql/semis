"""SchemaFacts: confiture's model as semis reads it, plus the table codes."""

from pathlib import Path

import pytest
from confiture.platform import ConfigurationError, NotInModelError

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import CodeRegistryError
from fraiseql_semis.schema import SchemaFacts
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
    with pytest.raises(CodeRegistryError, match=r"catalog\.tb_country"):
        facts.facts_for("catalog.tb_country")


def test_from_env_outside_a_project_propagates_confitures_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        SchemaFacts.from_env("development", project_dir=tmp_path, table_codes=TableCodes(CODES))
