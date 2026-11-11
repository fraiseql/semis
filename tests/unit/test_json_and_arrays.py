"""A json or array column is drawn; a type semis cannot draw is refused before a row is.

fraiseql/semis#10.
"""

import json
from pathlib import Path
from uuid import UUID

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.faker_provider import CustomProviderRegistry, FakerProvider
from fraiseql_semis.generator import FakeDataGenerator, Row, TablePlan
from fraiseql_semis.scenario import ScenarioManager
from fraiseql_semis.schema import ColumnFacts, SchemaFacts

TEMPLATE = "app.tb_template"
APP = """CREATE SCHEMA app;
CREATE TYPE app.mood AS ENUM ('calm', 'busy');
CREATE TABLE app.tb_template (
  pk_template bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL UNIQUE,
  identifier text NOT NULL UNIQUE,
  variables jsonb NOT NULL,
  metadata json,
  tags text[],
  owner_ids uuid[] NOT NULL,
  emails text[],
  codes varchar(4)[],
  levels integer[][],
  moods app.mood[],
  address inet
);"""
FACTS = SchemaFacts.from_source(APP, table_codes=TableCodes({TEMPLATE: 0x0C}))
COLUMNS = {column.name: column for column in FACTS.facts_for(TEMPLATE).columns}


def _drawn(column: str, *, seed: int = 7, count: int = 30) -> list[object]:
    provider = FakerProvider(seed=seed)
    return [provider.value_for(COLUMNS[column], table=TEMPLATE) for _ in range(count)]


@pytest.mark.parametrize("column", ["variables", "metadata"])
def test_a_json_column_is_a_small_object_of_words(column: str) -> None:
    values = _drawn(column)
    assert all(isinstance(value, dict) and 1 <= len(value) <= 3 for value in values)
    assert all(
        isinstance(key, str) and isinstance(item, str)
        for value in values
        for key, item in value.items()  # ty: ignore[missing-attribute]
    )
    assert len({json.dumps(value, sort_keys=True) for value in values}) > 1
    assert json.loads(json.dumps(values)) == values


def test_an_array_holds_one_to_three_values_of_its_element_type() -> None:
    values = _drawn("owner_ids")
    assert all(isinstance(value, list) and 1 <= len(value) <= 3 for value in values)
    assert all(isinstance(item, UUID) for value in values for item in value)  # ty: ignore[not-iterable]
    assert {len(value) for value in values} == {1, 2, 3}  # ty: ignore[invalid-argument-type]


def test_an_array_element_is_chosen_by_the_column_name() -> None:
    assert all("@" in item for value in _drawn("emails") for item in value)  # ty: ignore[not-iterable]


def test_an_array_element_is_cut_to_its_declared_length() -> None:
    assert all(len(item) <= 4 for value in _drawn("codes") for item in value)  # ty: ignore[not-iterable]


def test_a_multidimensional_array_is_drawn_flat() -> None:
    assert all(
        isinstance(item, int)
        for value in _drawn("levels")
        for item in value  # ty: ignore[not-iterable]
    )


def test_json_and_arrays_are_the_same_for_one_seed() -> None:
    for column in ("variables", "tags", "owner_ids"):
        assert _drawn(column) == _drawn(column)
        assert _drawn(column) != _drawn(column, seed=8)


def test_a_provider_registered_for_an_array_draws_the_whole_array() -> None:
    registry = CustomProviderRegistry()
    registry.register_column(TEMPLATE, "tags", lambda _faker, _column: ["a", "b"])
    provider = FakerProvider(seed=7, registry=registry)
    assert provider.value_for(COLUMNS["tags"], table=TEMPLATE) == ["a", "b"]


def test_a_global_provider_draws_each_element_of_an_array() -> None:
    registry = CustomProviderRegistry()
    registry.register_global("tag", lambda _faker, _column: "t")
    provider = FakerProvider(seed=7, registry=registry)
    value = provider.value_for(COLUMNS["tags"], table=TEMPLATE)
    assert isinstance(value, list)
    assert set(value) == {"t"}


@pytest.mark.parametrize(
    ("column", "drawn"),
    [("variables", True), ("tags", True), ("address", False), ("moods", False)],
)
def test_draws_says_whether_semis_can_draw_a_column(column: str, *, drawn: bool) -> None:
    assert FakerProvider(seed=7).draws(COLUMNS[column], table=TEMPLATE) is drawn


@pytest.mark.parametrize(("column", "type_key"), [("address", "inet"), ("moods", r"app\.mood\[\]")])
def test_drawing_a_type_semis_cannot_draw_is_refused(column: str, type_key: str) -> None:
    with pytest.raises(
        ScenarioError,
        match=rf"app\.tb_template\.{column} is {type_key}, a type semis does not draw",
    ):
        FakerProvider(seed=7).value_for(COLUMNS[column], table=TEMPLATE)


def test_a_unique_json_column_is_compared_by_its_content() -> None:
    column = ColumnFacts(
        name="variables",
        type_key="jsonb",
        raw_sql_type="JSONB",
        not_null=True,
        default=None,
        unique=True,
    )
    provider = FakerProvider(seed=7)
    values = [provider.value_for(column, table=TEMPLATE) for _ in range(40)]
    assert len({json.dumps(value, sort_keys=True) for value in values}) == 40


def _generator() -> FakeDataGenerator:
    return FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)


def _rows(**drawn: object) -> list[Row]:
    return list(_generator().generate_rows(TEMPLATE, 3, **drawn))  # ty: ignore[invalid-argument-type]


def test_a_not_null_json_column_is_drawn_without_an_override() -> None:
    assert all(isinstance(row["variables"], dict) for row in _rows())


def test_fill_all_draws_json_and_arrays_and_leaves_what_semis_cannot_draw_null() -> None:
    rows = _rows(fill="all")
    assert all(isinstance(row["metadata"], dict) for row in rows)
    assert all(isinstance(row["tags"], list) for row in rows)
    assert {(row["address"], row["moods"]) for row in rows} == {(None, None)}


def test_fill_all_names_the_columns_semis_cannot_draw() -> None:
    assert _generator().left_null(TEMPLATE, fill="all") == ("moods", "address")


@pytest.mark.parametrize(("column", "type_key"), [("address", "inet"), ("moods", r"app\.mood\[\]")])
def test_fill_naming_a_column_semis_cannot_draw_is_refused(column: str, type_key: str) -> None:
    with pytest.raises(
        ScenarioError,
        match=rf"app\.tb_template\.{column} is {type_key}, a type semis does not draw",
    ):
        _generator().generate_rows(TEMPLATE, 3, fill=frozenset({column}))


def test_a_not_null_column_semis_cannot_draw_is_refused_before_a_row_is() -> None:
    ddl = APP.replace("address inet", "address inet NOT NULL")
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({TEMPLATE: 0x0C}))
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)
    with pytest.raises(ScenarioError, match=r"app\.tb_template\.address is inet") as refused:
        generator.generate_rows(TEMPLATE, 3)
    assert "Override it, or give it a provider" in str(refused.value.resolution_hint)


def test_an_overridden_column_semis_cannot_draw_is_written() -> None:
    ddl = APP.replace("address inet", "address inet NOT NULL")
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({TEMPLATE: 0x0C}))
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)
    rows = generator.generate_rows(TEMPLATE, 2, overrides={"address": "10.0.0.1"})
    assert {row["address"] for row in rows} == {"10.0.0.1"}


def test_walk_refuses_a_column_semis_cannot_draw_before_a_row_is() -> None:
    ddl = APP.replace("address inet", "address inet NOT NULL")
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({TEMPLATE: 0x0C}))
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)
    with pytest.raises(ScenarioError, match=r"app\.tb_template\.address is inet"):
        generator.walk({TEMPLATE: TablePlan(3)})


def _notices(tmp_path: Path, ddl: str, table: str, codes: dict[str, int]) -> tuple[str, ...]:
    path = tmp_path / "templates.yaml"
    path.write_text(
        "scenario_id: 0x5006\nname: templates\nmode: read-back\nseed: 42\ntables:\n"
        f"  - name: {table}\n    count: 3\n    fill: all\n"
    )
    manager = ScenarioManager(SchemaFacts.from_source(ddl, table_codes=TableCodes(codes)))
    return manager.check(manager.load(path), no_pin=True)[1:]


def test_the_run_names_the_columns_fill_all_leaves_null(tmp_path: Path) -> None:
    assert _notices(tmp_path, APP, TEMPLATE, {TEMPLATE: 0x0C}) == (
        "app.tb_template leaves moods, address NULL; semis draws no app.mood[], inet: "
        "an override or a provider writes them",
    )


def test_the_run_names_the_keys_fill_all_leaves_null_beside_them(tmp_path: Path) -> None:
    ddl = APP.replace(
        "CREATE TABLE app.tb_template (",
        "CREATE TABLE app.tb_folder (pk_folder bigint GENERATED ALWAYS AS IDENTITY PRIMARY "
        "KEY, id uuid NOT NULL UNIQUE);\nCREATE TABLE app.tb_template (\n"
        "  fk_folder bigint REFERENCES app.tb_folder (pk_folder),",
    )
    codes = {TEMPLATE: 0x0C, "app.tb_folder": 0x0D}
    assert _notices(tmp_path, ddl, TEMPLATE, codes) == (
        "app.tb_template leaves fk_folder, moods, address NULL; semis draws no app.mood[], "
        "inet: an override or a provider writes them; a parent under tables: or existing: "
        "points the keys",
    )


def test_a_unique_array_column_is_compared_by_its_values() -> None:
    column = ColumnFacts(
        name="owner_ids",
        type_key="uuid[]",
        raw_sql_type="uuid[]",
        not_null=True,
        default=None,
        unique=True,
    )
    provider = FakerProvider(seed=7)
    values = [provider.value_for(column, table=TEMPLATE) for _ in range(20)]
    assert len({tuple(value) for value in values}) == 20  # ty: ignore[invalid-argument-type]
