"""The staging twin: where a prep-seed run writes, and what the project's resolvers read."""

from dataclasses import replace
from pathlib import Path

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.hierarchy import Hierarchy
from fraiseql_semis.pin import SchemaPin, projection
from fraiseql_semis.scenario import Scenario, ScenarioManager, TableSpec
from fraiseql_semis.schema import SchemaFacts
from fraiseql_semis.staging import Staging
from tests.ddl import CODES, HIERARCHY, HIERARCHY_CODES, TRINITY

CONTINENT, COUNTRY = "catalog.tb_continent", "catalog.tb_country"
FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))


def _prep_seed(*tables: TableSpec) -> Scenario:
    return Scenario(id=0x5001, name="staged", mode="prep-seed", tables=tables, seed=42)


def test_prep_seed_writes_the_staging_twin(tmp_path: Path) -> None:
    scenario = _prep_seed(TableSpec(CONTINENT, 2), TableSpec(COUNTRY, 4))
    continents, countries = ScenarioManager(FACTS).execute(scenario, tmp_path).seeds
    text = countries.path.read_text()
    assert (continents.table.display, countries.table.display) == (
        "prep_seed.tb_continent",
        "prep_seed.tb_country",
    )
    assert countries.columns == ("id", "identifier", "fk_continent_id", "iso_code")
    assert "INSERT INTO prep_seed.tb_country" in text
    assert all(str(uuid) in continents.path.read_text() for uuid in _uuids(text))


def _uuids(text: str) -> set[str]:
    """The fk_continent_id values of a country file: the third value of each row."""
    rows = [line for line in text.splitlines() if line.lstrip().startswith("(")]
    return {row.split(", ")[2].strip("'") for row in rows}


def test_a_table_without_its_twin_is_refused_before_a_row_is_drawn() -> None:
    catalog_only = SchemaFacts.from_source(
        TRINITY.split("CREATE SCHEMA prep_seed;")[0], table_codes=TableCodes(CODES)
    )
    with pytest.raises(ScenarioError, match=r"catalog\.tb_continent has no staging twin"):
        ScenarioManager(catalog_only).check(_prep_seed(TableSpec(CONTINENT, 2)))


def test_a_read_back_scenario_needs_no_twin() -> None:
    catalog_only = SchemaFacts.from_source(
        TRINITY.split("CREATE SCHEMA prep_seed;")[0], table_codes=TableCodes(CODES)
    )
    scenario = replace(_prep_seed(TableSpec(CONTINENT, 2)), mode="read-back")
    assert ScenarioManager(catalog_only).check(scenario)


def test_a_twin_missing_a_column_the_rows_carry_is_refused(tmp_path: Path) -> None:
    narrow = SchemaFacts.from_source(
        TRINITY.replace("    fk_continent_id UUID,\n", ""), table_codes=TableCodes(CODES)
    )
    scenario = _prep_seed(TableSpec(CONTINENT, 1), TableSpec(COUNTRY, 1))
    with pytest.raises(ScenarioError, match=r"prep_seed\.tb_country has no fk_continent_id"):
        ScenarioManager(narrow).execute(scenario, tmp_path)


def test_the_twin_is_in_the_staging_schema_the_project_names(tmp_path: Path) -> None:
    staged = SchemaFacts.from_source(
        TRINITY.replace("prep_seed", "staging"), table_codes=TableCodes(CODES)
    )
    manager = ScenarioManager(staged, staging=Staging("staging"))
    (seed,) = manager.execute(_prep_seed(TableSpec(CONTINENT, 1)), tmp_path).seeds
    assert (seed.table.display, seed.path.name) == (
        "staging.tb_continent",
        "001_staging.tb_continent.sql",
    )


def test_a_hierarchys_parent_is_written_under_its_twin_name(tmp_path: Path) -> None:
    facts = SchemaFacts.from_source(HIERARCHY, table_codes=TableCodes(HIERARCHY_CODES))
    tree = Hierarchy(parent="fk_parent_location", roots=1, fan_out=2)
    scenario = _prep_seed(TableSpec("catalog.tb_location", 3, hierarchy=tree))
    (seed,) = ScenarioManager(facts).execute(scenario, tmp_path).seeds
    assert "fk_parent_location_id" in seed.columns
    assert "fk_parent_location" not in seed.columns


def test_a_change_to_a_twin_moves_the_pin() -> None:
    tables = [CONTINENT]
    twins = ["prep_seed.tb_continent"]
    widened = SchemaFacts.from_source(
        TRINITY.replace("    name VARCHAR(50)\n);", "    name VARCHAR(80)\n);"),
        table_codes=TableCodes(CODES),
    )
    assert SchemaPin.of(FACTS, tables).digest == SchemaPin.of(widened, tables).digest
    assert (
        SchemaPin.of(FACTS, tables, twins=twins).digest
        != SchemaPin.of(widened, tables, twins=twins).digest
    )


def test_a_missing_twin_is_pinned_as_missing() -> None:
    catalog_only = SchemaFacts.from_source(
        TRINITY.split("CREATE SCHEMA prep_seed;")[0], table_codes=TableCodes(CODES)
    )
    assert projection(catalog_only, [], ["prep_seed.tb_continent"]) == [
        {"twin": "prep_seed.tb_continent", "columns": None}
    ]
