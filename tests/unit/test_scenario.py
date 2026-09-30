"""Scenarios: a run as a file, loaded into Scenario and TableSpec."""

from pathlib import Path

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import RowContractError, ScenarioError
from fraiseql_semis.generator import FakeDataGenerator
from fraiseql_semis.hierarchy import Hierarchy
from fraiseql_semis.providers import i18n
from fraiseql_semis.scenario import Scenario, ScenarioManager, TableSpec, catalogue
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, CONTRACT, CONTRACT_CODES, HIERARCHY, HIERARCHY_CODES, TRINITY

FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
PRODUCTS = SchemaFacts.from_source(CONTRACT, table_codes=TableCodes(CONTRACT_CODES))
PRODUCT = "catalog.tb_product"

MINIMAL = """\
scenario_id: 0x5001
name: minimal_seed
mode: prep-seed
seed: 42
tables:
  - name: catalog.tb_continent
    count: 2
"""


def _load(tmp_path: Path, text: str) -> Scenario:
    path = tmp_path / "scenario.yaml"
    path.write_text(text)
    return ScenarioManager(FACTS).load(path)


def test_scenario_without_a_mode_is_refused(tmp_path: Path) -> None:
    text = MINIMAL.replace("mode: prep-seed\n", "")
    with pytest.raises(ScenarioError, match=r"prep-seed.*read-back"):
        _load(tmp_path, text)


def test_an_unknown_mode_is_refused_naming_both(tmp_path: Path) -> None:
    text = MINIMAL.replace("mode: prep-seed", "mode: auto")
    with pytest.raises(ScenarioError, match=r"'auto' is not one of prep-seed or read-back"):
        _load(tmp_path, text)


def test_a_scenario_loads_into_scenario_and_table_spec(tmp_path: Path) -> None:
    text = MINIMAL + (
        "  - name: catalog.tb_country\n"
        "    count: 3\n"
        "    trusts_trigger: [fk_continent]\n"
        "    providers: {iso_code: slug}\n"
        "    overrides: {identifier: [a, b, c]}\n"
    )
    path = tmp_path / "scenario.yaml"
    path.write_text(text)
    scenario = ScenarioManager(FACTS, providers={"slug": _slug}).load(path)
    continent, country = scenario.tables
    assert (scenario.id, scenario.name, scenario.mode, scenario.seed, scenario.locale) == (
        0x5001,
        "minimal_seed",
        "prep-seed",
        42,
        "en_US",
    )
    assert continent == TableSpec("catalog.tb_continent", 2)
    assert (country.count, country.trusts_trigger, country.providers, country.overrides) == (
        3,
        frozenset({"fk_continent"}),
        {"iso_code": "slug"},
        {"identifier": ["a", "b", "c"]},
    )


def _slug(*_: object) -> str:
    return "slug"


def _product_rows(overrides: dict[str, object], count: int = 3) -> list[dict[str, object]]:
    return list(
        FakeDataGenerator(PRODUCTS, scenario_id=0x5001, seed=42).generate_rows(
            PRODUCT, count, overrides=overrides
        )
    )


def test_overrides_accept_scalar_list_and_callable() -> None:
    spec = TableSpec(
        PRODUCT,
        3,
        overrides={
            "note": "seeded",
            "sku": ["a-1", "b-2", "c-3"],
            "name": lambda index: f"Product {index}",
        },
    )
    rows = _product_rows(dict(spec.overrides))
    assert [(row["note"], row["sku"], row["name"]) for row in rows] == [
        ("seeded", "a-1", "Product 0"),
        ("seeded", "b-2", "Product 1"),
        ("seeded", "c-3", "Product 2"),
    ]


def test_an_override_writes_a_column_that_has_a_default() -> None:
    assert {row["status"] for row in _product_rows({"status": "live"})} == {"live"}


def test_list_override_shorter_than_count_is_refused(tmp_path: Path) -> None:
    text = MINIMAL + "    overrides: {name: [Africa]}\n"
    with pytest.raises(
        ScenarioError, match=r"tb_continent\.name: the override lists 1 values for 2"
    ):
        _load(tmp_path, text)


def test_list_override_longer_than_count_is_refused() -> None:
    with pytest.raises(ScenarioError, match="lists 3 values for 2 rows"):
        TableSpec(PRODUCT, 2, overrides={"sku": ["a", "b", "c"]})


@pytest.mark.parametrize(
    ("column", "reason"),
    [
        ("id", "is the natural id"),
        ("pk_product", "is not a column semis writes"),
        ("colour", "is not a column semis writes"),
    ],
)
def test_an_override_semis_cannot_honour_is_refused(column: str, reason: str) -> None:
    with pytest.raises(ScenarioError, match=rf"tb_product\.{column} {reason}"):
        _product_rows({column: "x"})


def test_a_foreign_key_cannot_be_overridden() -> None:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    with pytest.raises(ScenarioError, match=r"tb_country\.fk_continent is a foreign key"):
        generator.generate_rows("catalog.tb_country", 1, overrides={"fk_continent": 1})


def test_a_scalar_override_on_a_unique_column_meets_the_contract() -> None:
    with pytest.raises(RowContractError, match=r"tb_product\.sku is unique"):
        _product_rows({"sku": "same"})


def test_an_overridden_column_cannot_also_be_trusted() -> None:
    with pytest.raises(ScenarioError, match="both overridden and trusted"):
        TableSpec(PRODUCT, 1, overrides={"note": "x"}, trusts_trigger=frozenset({"note"}))


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (MINIMAL + "    cuont: 3\n", "unknown key cuont"),
        (MINIMAL + "scenario_idd: 1\n", "unknown key scenario_idd"),
        (MINIMAL.replace("    count: 2\n", ""), "tb_continent has no count"),
        (MINIMAL.replace("    count: 2", "    count: -1"), "count is -1"),
        (MINIMAL.replace("0x5001", "0x10000"), "does not fit in 16 bits"),
        (MINIMAL + MINIMAL[MINIMAL.index("  - name") :], "lists catalog.tb_continent more"),
        ("- just a list\n", "does not hold a scenario mapping"),
        (MINIMAL.replace("name: minimal_seed\n", ""), "has no name:"),
        (MINIMAL[: MINIMAL.index("tables:")] + "tables: []\n", "lists no tables"),
        (MINIMAL + "  - count: 1\n", "a table entry has no name"),
        (MINIMAL + "    overrides: {name: {a: 1}}\n", "not a mapping"),
        (MINIMAL + "    overrides: [name]\n", "overrides maps a column"),
        (MINIMAL + "    providers: {name: 3}\n", "a provider is named, by a string"),
        (MINIMAL + "    trusts_trigger: name\n", "trusts_trigger lists column names"),
        (MINIMAL + "    hierarchy: [fk]\n", "hierarchy maps parent, roots and fan_out"),
        (MINIMAL + "    hierarchy: {parent: fk, roots: 1, fan_out: 2, depth: 3}\n", "key depth"),
        (MINIMAL + "    hierarchy: {parent: fk, roots: 1}\n", "hierarchy has no fan_out"),
        (MINIMAL + "    hierarchy: {parent: fk, roots: 0, fan_out: 2}\n", "roots is 0"),
        (
            MINIMAL + "    hierarchy: {parent: 3, roots: 1, fan_out: 2}\n",
            "names columns, by strings",
        ),
        (
            MINIMAL + "    hierarchy: {parent: fk, roots: 1, fan_out: 2, path: path}\n",
            r"prep-seed cannot set catalog\.tb_continent\.path",
        ),
    ],
)
def test_a_malformed_scenario_is_refused(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(ScenarioError, match=match):
        _load(tmp_path, text)


def test_a_provider_is_a_registered_name_never_code(tmp_path: Path) -> None:
    text = MINIMAL + '    providers: {name: "lambda f: f.country()"}\n'
    with pytest.raises(
        ScenarioError, match=r"names provider 'lambda f: f.country\(\)', which is not"
    ):
        _load(tmp_path, text)


TREE = """\
scenario_id: 0x5001
name: locations
mode: read-back
tables:
  - name: catalog.tb_location
    count: 20
    hierarchy:
      parent: fk_parent_location
      roots: 2
      fan_out: 3
      path: path
"""


def test_hierarchy_block_loads(tmp_path: Path) -> None:
    path = tmp_path / "locations.yaml"
    path.write_text(TREE)
    facts = SchemaFacts.from_source(HIERARCHY, table_codes=TableCodes(HIERARCHY_CODES))
    (spec,) = ScenarioManager(facts).load(path).tables
    assert spec.hierarchy == Hierarchy("fk_parent_location", roots=2, fan_out=3, path="path")


def test_catalogue_refuses_a_file_that_is_no_scenario(tmp_path: Path) -> None:
    (tmp_path / "codes.yaml").write_text("catalog.tb_continent: 0x02030405\n")
    with pytest.raises(ScenarioError, match=r"codes\.yaml is not a scenario"):
        catalogue(tmp_path)


def test_catalogue_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert catalogue(tmp_path / "absent") == ()


def test_a_run_seeding_an_unseeded_scenario_says_it_had_none() -> None:
    scenario = Scenario(id=0x5001, name="s", mode="prep-seed", tables=())
    _, notices = scenario.for_run(seed=7)
    assert notices == ("scenario s sets no seed; this run uses seed 7",)


def test_a_provider_a_library_also_names_is_refused() -> None:
    facts = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
    with pytest.raises(ValueError, match=r"i18n\.country_code is both a provider and a library's"):
        ScenarioManager(
            facts, providers={"i18n.country_code": lambda f, _c: f.word()}, libraries=[i18n.LIBRARY]
        )
