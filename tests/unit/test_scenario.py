"""Scenarios: a run as a file, loaded into Scenario and TableSpec."""

from pathlib import Path

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import RowContractError, ScenarioError
from fraiseql_semis.generator import FakeDataGenerator
from fraiseql_semis.hierarchy import Hierarchy
from fraiseql_semis.providers import i18n
from fraiseql_semis.scenario import (
    ExistingTable,
    Scenario,
    ScenarioManager,
    TableSpec,
    catalogue,
    init_scenario,
    single_table,
)
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


def _refusal(tmp_path: Path, text: str) -> list[str]:
    with pytest.raises(ScenarioError) as refused:
        _load(tmp_path, text)
    return str(refused.value).splitlines()


def test_a_scenario_built_in_python_names_itself_refusing_a_provider() -> None:
    scenario = Scenario(
        id=0x5001,
        name="s",
        mode="prep-seed",
        tables=(TableSpec("catalog.tb_continent", 1, providers={"name": "nope"}),),
    )
    with pytest.raises(ScenarioError, match=r"^scenario s: catalog\.tb_continent\.name names"):
        ScenarioManager(FACTS).rehearse(scenario)


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
        ScenarioError, match=r"tb_continent\.name: the override lists 1 value for 2 rows"
    ):
        _load(tmp_path, text)


def test_a_list_override_for_one_row_counts_it_in_the_singular() -> None:
    with pytest.raises(ScenarioError, match="lists 2 values for 1 row\n"):
        TableSpec(PRODUCT, 1, overrides={"sku": ["a", "b"]})


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
        (
            MINIMAL.replace("name: catalog.tb_continent", "name: tb_continent"),
            r"^scenario minimal_seed: table tb_continent is not schema-qualified\n"
            r"Hint: Name it with its schema, e\.g\. catalog\.tb_city\.$",
        ),
        (MINIMAL + "    overrides: {name: {a: 1}}\n", "not a mapping"),
        (MINIMAL + "    overrides: [name]\n", "overrides maps a column"),
        (MINIMAL + "    providers: {name: 3}\n", "a provider is named, by a string"),
        (MINIMAL + "    trusts_trigger: name\n", "trusts_trigger lists column names"),
        (MINIMAL + "    fill: name\n", "fill lists column names, or is all"),
        (MINIMAL + "    fill: [3]\n", "fill lists column names, or is all"),
        (MINIMAL + "    hierarchy: [fk]\n", "hierarchy maps parent, roots and fan_out"),
        (MINIMAL + "    hierarchy: {parent: fk, roots: 1, fan_out: 2, depth: 3}\n", "key depth"),
        (
            MINIMAL + "    hierarchy: {parent: fk, roots: 1}\n",
            r"catalog\.tb_continent: hierarchy has no fan_out:, and 1 of its 2 rows has a parent",
        ),
        (MINIMAL + "    hierarchy: {roots: 1, fan_out: 2}\n", "hierarchy has no parent:"),
        (
            MINIMAL + "    hierarchy: {parent: fk, roots: 0, fan_out: 2}\n",
            r"^scenario minimal_seed: catalog\.tb_continent: hierarchy on fk: roots is 0",
        ),
        (
            MINIMAL + "    hierarchy: {parent: 3, roots: 1, fan_out: 2}\n",
            r"^scenario minimal_seed: catalog\.tb_continent: hierarchy on 3: parent and path "
            "names columns, by strings",
        ),
        (
            MINIMAL + "    hierarchy: {parent: fk, roots: 1, fan_out: 2, path: path}\n",
            r"prep-seed cannot set catalog\.tb_continent\.path",
        ),
    ],
)
def test_a_malformed_scenario_is_refused(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(ScenarioError, match=match) as refused:
        _load(tmp_path, text)
    assert refused.value.resolution_hint != ScenarioError.default_hint


def test_a_provider_is_a_registered_name_never_code(tmp_path: Path) -> None:
    text = MINIMAL + '    providers: {name: "lambda f: f.country()"}\n'
    assert _refusal(tmp_path, text) == [
        "scenario minimal_seed: catalog.tb_continent.name names provider "
        "'lambda f: f.country()', which is not registered",
        "Hint: Registered providers: none.",
    ]


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


def test_a_hierarchy_of_roots_alone_needs_no_fan_out(tmp_path: Path) -> None:
    path = tmp_path / "locations.yaml"
    path.write_text(
        TREE.replace("count: 20", "count: 5").replace("roots: 2\n      fan_out: 3\n", "roots: 5\n")
    )
    facts = SchemaFacts.from_source(HIERARCHY, table_codes=TableCodes(HIERARCHY_CODES))
    (spec,) = ScenarioManager(facts).load(path).tables
    assert spec.hierarchy == Hierarchy("fk_parent_location", roots=5, path="path")


@pytest.mark.parametrize(("given", "fill"), [("[name]", frozenset({"name"})), ("all", "all")])
def test_fill_loads(tmp_path: Path, given: str, fill: object) -> None:
    (spec,) = _load(tmp_path, MINIMAL + f"    fill: {given}\n").tables
    assert spec.fill == fill


COPYING = MINIMAL.replace("tb_continent\n    count: 2", "tb_country\n    count: 2")


def test_copies_loads_each_column_with_its_key_and_parent_column(tmp_path: Path) -> None:
    (spec,) = _load(tmp_path, COPYING + "    copies:\n      tenant_id: fk_org.id\n").tables
    assert spec.copies == {"tenant_id": ("fk_org", "id")}


@pytest.mark.parametrize(
    "copies",
    ["{tenant_id: fk_org}", "{tenant_id: 3}", "{3: fk_org.id}", "{tenant_id: fk_org.}", "[x]"],
    ids=["no-dot", "number", "number-key", "no-column", "list"],
)
def test_a_malformed_copies_is_refused_with_the_shape_to_write(tmp_path: Path, copies: str) -> None:
    with pytest.raises(
        ScenarioError,
        match=r"^scenario minimal_seed: catalog\.tb_country: copies maps a column to "
        r"<foreign key>\.<parent column>",
    ):
        _load(tmp_path, COPYING + f"    copies: {copies}\n")


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


EXISTING = """\
scenario_id: 0x5001
name: customers
mode: read-back
seed: 42
tables:
  - name: catalog.tb_country
    count: 2
existing:
  - name: catalog.tb_continent
"""


def test_existing_tables_load_with_their_filter(tmp_path: Path) -> None:
    text = EXISTING + "  - name: catalog.tb_other\n    where: {identifier: [fr, de]}\n"
    scenario = _load(tmp_path, text)
    assert scenario.existing == (
        ExistingTable("catalog.tb_continent"),
        ExistingTable("catalog.tb_other", identifiers=("fr", "de")),
    )


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (
            EXISTING.replace("existing:\n  - name: catalog.tb_continent\n", "existing: [x]\n"),
            "an existing entry has no name:",
        ),
        (EXISTING + "    count: 3\n", r"existing catalog\.tb_continent: unknown key count"),
        (
            EXISTING.replace("name: catalog.tb_continent", "name: tb_continent"),
            r"^scenario \w+: existing table tb_continent is not schema-qualified\n",
        ),
        (
            EXISTING + "    where: {name: [Europe]}\n",
            r"existing catalog\.tb_continent: where: names rows by identifier",
        ),
        (
            EXISTING + "    where: {identifier: fr}\n",
            r"existing catalog\.tb_continent: where: names rows by identifier",
        ),
        (
            EXISTING + "    where: {identifier: []}\n",
            r"existing catalog\.tb_continent: where: names rows by identifier",
        ),
        (
            EXISTING + "  - name: catalog.tb_continent\n",
            r"lists catalog\.tb_continent under existing: more than once",
        ),
        (
            EXISTING.replace("tb_continent\n", "tb_country\n"),
            r"lists catalog\.tb_country under both tables: and existing:",
        ),
        (
            EXISTING.replace("read-back", "prep-seed"),
            r"prep-seed cannot read catalog\.tb_continent's existing rows",
        ),
        (
            EXISTING.replace("existing:\n  - name: catalog.tb_continent\n", "existing: {a: b}\n"),
            "existing lists tables",
        ),
    ],
)
def test_a_malformed_existing_entry_is_refused(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(ScenarioError, match=match) as refused:
        _load(tmp_path, text)
    assert refused.value.resolution_hint != ScenarioError.default_hint


NAME_HINT = "Hint: Use letters, digits, ., - and _: the name becomes file names."


@pytest.mark.parametrize(
    ("given", "shown"), [("[a, b]", "['a', 'b']"), ('"a/b"', "'a/b'"), ('""', "''"), ("12", "12")]
)
def test_a_name_that_is_no_file_name_is_refused(tmp_path: Path, given: str, shown: str) -> None:
    path = tmp_path / "scenario.yaml"
    path.write_text(MINIMAL.replace("name: minimal_seed", f"name: {given}"))
    with pytest.raises(ScenarioError) as refused:
        ScenarioManager(FACTS).load(path)
    assert str(refused.value).splitlines() == [
        f"{path}: name is {shown}, which is not a scenario name",
        NAME_HINT,
    ]


def test_a_scenario_built_in_python_takes_a_file_name_too() -> None:
    with pytest.raises(ScenarioError) as refused:
        Scenario(id=0x5001, name="a/b", mode="prep-seed", tables=())
    assert str(refused.value).splitlines() == [
        "name is 'a/b', which is not a scenario name",
        NAME_HINT,
    ]


def test_a_name_may_hold_a_dot(tmp_path: Path) -> None:
    path, _ = init_scenario(tmp_path, "a.b", mode="prep-seed", tables=["catalog.tb_continent"])
    assert path.name == "a.b.yaml"
    assert ScenarioManager(FACTS).load(path).name == "a.b"


def test_catalogue_refuses_a_name_that_is_no_file_name(tmp_path: Path) -> None:
    path = tmp_path / "scenario.yaml"
    path.write_text(MINIMAL.replace("name: minimal_seed", "name: a/b"))
    with pytest.raises(ScenarioError, match=r"name is 'a/b', which is not a scenario name"):
        catalogue(tmp_path)


@pytest.mark.parametrize(
    ("text", "where"),
    [
        (MINIMAL + "1: x\n", "{path}"),
        (MINIMAL + "    1: x\n", "scenario minimal_seed, table catalog.tb_continent"),
        (
            MINIMAL + "    hierarchy: {parent: fk, roots: 1, 1: x}\n",
            "scenario minimal_seed: catalog.tb_continent: hierarchy",
        ),
        (EXISTING + "    1: x\n", "scenario customers, existing catalog.tb_continent"),
    ],
)
def test_a_key_that_is_not_a_string_is_refused_naming_it(
    tmp_path: Path, text: str, where: str
) -> None:
    lines = _refusal(tmp_path, text)
    assert lines[0] == where.format(path=tmp_path / "scenario.yaml") + ": unknown key 1"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "description: [x]",
            [
                "scenario minimal_seed: description is ['x'], not a string",
                "Hint: Write description: as text, quoted if it holds a colon.",
            ],
        ),
        (
            "seed: [1]",
            [
                "scenario minimal_seed: seed is [1], not a whole number",
                "Hint: Give seed: a whole number, e.g. seed: 42, or leave it out.",
            ],
        ),
        (
            "seed: true",
            [
                "scenario minimal_seed: seed is True, not a whole number",
                "Hint: Give seed: a whole number, e.g. seed: 42, or leave it out.",
            ],
        ),
        (
            "locale: xx_XX",
            [
                "scenario minimal_seed: locale is 'xx_XX', which is not one of Faker's locales",
                "Hint: Name one of Faker's locales, e.g. en_US or fr_FR.",
            ],
        ),
        (
            "locale: [en_US]",
            [
                "scenario minimal_seed: locale is ['en_US'], which is not one of Faker's locales",
                "Hint: Name one of Faker's locales, e.g. en_US or fr_FR.",
            ],
        ),
    ],
)
def test_a_header_value_of_the_wrong_type_is_refused(
    tmp_path: Path, line: str, expected: list[str]
) -> None:
    assert _refusal(tmp_path, MINIMAL.replace("seed: 42", line)) == expected


def test_a_quoted_scenario_id_is_refused_saying_how_to_write_it(tmp_path: Path) -> None:
    assert _refusal(tmp_path, MINIMAL.replace("0x5001", '"0x7009"')) == [
        "scenario minimal_seed: scenario_id is '0x7009', a string",
        "Hint: Write it unquoted, in hex: scenario_id: 0x7009",
    ]


def test_a_run_s_locale_is_checked_too() -> None:
    scenario = Scenario(id=0x5001, name="s", mode="prep-seed", tables=())
    with pytest.raises(ScenarioError, match="locale is 'xx_XX', which is not one of Faker's"):
        scenario.for_run(locale="xx_XX")


def test_catalogue_reads_scenarios_alone(tmp_path: Path) -> None:
    """A scenario's pin file beside it is not a scenario."""
    (tmp_path / "minimal_seed.yaml").write_text(MINIMAL)
    (tmp_path / "minimal_seed.pin.json").write_text("{}\n")
    assert [entry.name for entry in catalogue(tmp_path)] == ["minimal_seed"]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (
            '"0x7009"',
            [
                "{path}: scenario_id is '0x7009', a string",
                "Hint: Write it unquoted, in hex: scenario_id: 0x7009",
            ],
        ),
        (
            "0x10000",
            [
                "{path}: scenario_id 65536 does not fit in 16 bits",
                "Hint: Choose an id from 0x0 to 0xffff, written in hex.",
            ],
        ),
    ],
)
def test_catalogue_refuses_an_id_that_is_none_naming_the_file(
    tmp_path: Path, given: str, expected: list[str]
) -> None:
    (tmp_path / "a.yaml").write_text(MINIMAL)
    bad = tmp_path / "b.yaml"
    bad.write_text(MINIMAL.replace("0x5001", given).replace("minimal_seed", "b"))
    with pytest.raises(ScenarioError) as refused:
        catalogue(tmp_path)
    assert str(refused.value).splitlines() == [line.format(path=bad) for line in expected]


UNQUALIFIED = [
    "table tb_city is not schema-qualified",
    "Hint: Name it with its schema, e.g. catalog.tb_city.",
]


def test_a_table_spec_names_its_table_with_its_schema() -> None:
    with pytest.raises(ScenarioError) as refused:
        TableSpec("tb_city", 1)
    assert str(refused.value).splitlines() == UNQUALIFIED


def test_an_existing_table_names_its_table_with_its_schema() -> None:
    with pytest.raises(ScenarioError) as refused:
        ExistingTable("tb_city")
    assert str(refused.value).splitlines() == [
        "existing table tb_city is not schema-qualified",
        UNQUALIFIED[1],
    ]


def test_a_single_table_names_its_table_with_its_schema() -> None:
    with pytest.raises(ScenarioError) as refused:
        single_table("tb_city", 1, mode="prep-seed", scenario_id=0x5001)
    assert str(refused.value).splitlines() == UNQUALIFIED


@pytest.mark.parametrize("key", ["tables", "existing"])
def test_a_table_the_schema_lacks_is_refused_naming_the_scenario(key: str) -> None:
    written = (TableSpec("catalog.tb_nope", 1),) if key == "tables" else ()
    read = (ExistingTable("catalog.tb_nope"),) if key == "existing" else ()
    scenario = Scenario(
        id=0x5001,
        name="s",
        mode="read-back",
        tables=written or (TableSpec("catalog.tb_continent", 1),),
        existing=read,
    )
    manager = ScenarioManager(FACTS)
    with pytest.raises(ScenarioError) as refused:
        manager.check(scenario)
    assert str(refused.value).splitlines() == [
        "scenario s: table catalog.tb_nope is not in the schema",
        "Hint: Name a table the schema holds, schema-qualified.",
    ]
    with pytest.raises(ScenarioError, match="tb_nope is not in the schema"):
        manager.rehearse(scenario, connection=object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            MINIMAL.replace("    count: 2", "    count: -1"),
            [
                "scenario minimal_seed: catalog.tb_continent: count is -1, not a whole number",
                "Hint: Give count: a whole number of rows, 0 or more.",
            ],
        ),
        (
            MINIMAL[: MINIMAL.index("tables:")] + "tables: []\n",
            [
                "scenario minimal_seed lists no tables",
                "Hint: List at least one table under tables:, each with a name: and a count:.",
            ],
        ),
        (
            MINIMAL[: MINIMAL.index("tables:")],
            [
                "{path} has no tables:",
                "Hint: List at least one table under tables:, each with a name: and a count:.",
            ],
        ),
        (
            MINIMAL + "  - count: 1\n",
            [
                "scenario minimal_seed: a table entry has no name:",
                "Hint: Give each tables: entry a name:, its schema-qualified table, and a count:.",
            ],
        ),
        (
            "tables: [\n",
            [
                "{path} does not read as YAML: while parsing a flow node, expected the node "
                "content, but found '<stream end>', at line 2, column 1",
                "Hint: Fix the YAML at that line: a scenario file is a mapping of the keys the "
                "scenario file reference lists.",
            ],
        ),
    ],
)
def test_a_refusal_says_what_to_write(tmp_path: Path, text: str, expected: list[str]) -> None:
    path = tmp_path / "scenario.yaml"
    assert _refusal(tmp_path, text) == [line.format(path=path) for line in expected]


def test_a_character_yaml_does_not_take_is_refused_on_one_line(tmp_path: Path) -> None:
    assert _refusal(tmp_path, "\x07")[0] == (
        f"{tmp_path / 'scenario.yaml'} does not read as YAML: unacceptable character #x0007: "
        "special characters are not allowed"
    )


def test_catalogue_refuses_a_file_that_does_not_read_as_yaml(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("tables: [\n")
    with pytest.raises(ScenarioError, match=r"broken\.yaml does not read as YAML"):
        catalogue(tmp_path)
