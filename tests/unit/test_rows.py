"""The row contract (ARCHITECTURE §6): is this row complete and valid enough to emit."""

import inspect
from uuid import UUID

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import RowContractError
from fraiseql_semis.rows import check_row, require_row
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CONTRACT, CONTRACT_CODES

TABLE = SchemaFacts.from_source(CONTRACT, table_codes=TableCodes(CONTRACT_CODES)).facts_for(
    "catalog.tb_product"
)

# Every NOT NULL column without a default, and nothing else.
COMPLETE = {
    "id": UUID(int=1),
    "identifier": "widget-5001-1",
    "name": "Widget",
    "created_by": UUID(int=2),
}


def _without(column: str) -> dict[str, object]:
    return {name: value for name, value in COMPLETE.items() if name != column}


def test_missing_not_null_without_default_is_refused() -> None:
    assert [violation.column for violation in check_row(TABLE, _without("name"))] == ["name"]


def test_missing_not_null_with_default_is_allowed() -> None:
    assert "status" not in COMPLETE
    assert check_row(TABLE, COMPLETE) == []


def test_none_in_a_not_null_column_is_refused() -> None:
    assert [violation.column for violation in check_row(TABLE, {**COMPLETE, "name": None})] == [
        "name"
    ]


def test_a_value_outside_the_enum_is_refused() -> None:
    assert [v.column for v in check_row(TABLE, {**COMPLETE, "kind": "archived"})] == ["kind"]


def test_a_declared_enum_label_is_accepted() -> None:
    assert check_row(TABLE, {**COMPLETE, "kind": "live"}) == []


def test_a_value_longer_than_the_declared_length_is_refused() -> None:
    assert [v.column for v in check_row(TABLE, {**COMPLETE, "name": "x" * 51})] == ["name"]
    assert [v.column for v in check_row(TABLE, {**COMPLETE, "code": "FRA"})] == ["code"]


def test_a_value_at_the_declared_length_is_accepted() -> None:
    assert check_row(TABLE, {**COMPLETE, "name": "x" * 50, "code": "FR"}) == []


def test_a_unique_value_used_earlier_in_the_run_is_refused() -> None:
    seen = {"sku": {"A-1"}}
    assert [v.column for v in check_row(TABLE, {**COMPLETE, "sku": "A-1"}, seen=seen)] == ["sku"]
    assert check_row(TABLE, {**COMPLETE, "sku": "A-2"}, seen=seen) == []


def test_null_never_repeats_on_a_unique_column() -> None:
    assert check_row(TABLE, {**COMPLETE, "sku": None}, seen={"sku": {None}}) == []


def test_none_in_a_not_null_column_with_a_default_is_refused() -> None:
    assert [v.column for v in check_row(TABLE, {**COMPLETE, "status": None})] == ["status"]


def test_refusal_names_table_column_and_fact() -> None:
    with pytest.raises(RowContractError) as refused:
        require_row(TABLE, _without("name"))
    assert str(refused.value).startswith(
        "catalog.tb_product.name is NOT NULL with no default, but the row does not carry it"
    )
    assert refused.value.error_code == "SEMIS_ROWS_001"


def test_trusts_trigger_suppresses_one_column() -> None:
    assert check_row(TABLE, _without("created_by"), trusted=frozenset({"created_by"})) == []


def test_trusts_trigger_does_not_leak_to_another_column() -> None:
    row = {name: value for name, value in COMPLETE.items() if name not in {"name", "created_by"}}
    assert [v.column for v in check_row(TABLE, row, trusted=frozenset({"created_by"}))] == ["name"]


def test_trusts_trigger_still_judges_a_value_the_row_carries() -> None:
    row = {**COMPLETE, "name": "x" * 51}
    assert [v.column for v in check_row(TABLE, row, trusted=frozenset({"name"}))] == ["name"]


def test_no_scenario_wide_trust_switch_exists() -> None:
    """Trust is named column by column; nothing turns the contract off for a whole row."""
    for check in (check_row, require_row):
        keywords = {
            name
            for name, parameter in inspect.signature(check).parameters.items()
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        }
        assert keywords == {"trusted", "seen"}
    assert check_row(TABLE, {}, trusted=frozenset({"*"})) != []


def test_trusted_is_a_set_of_names_not_a_substring() -> None:
    with pytest.raises(TypeError, match="set of column names"):
        check_row(TABLE, _without("id"), trusted="identifier")  # ty: ignore[invalid-argument-type]


def test_require_row_honours_trust() -> None:
    require_row(TABLE, _without("created_by"), trusted=frozenset({"created_by"}))
