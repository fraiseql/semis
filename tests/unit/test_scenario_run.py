"""ScenarioManager.execute: a scenario run in its mode, its schema pinned beside its seeds."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import (
    CodeRegistryError,
    PinError,
    ResolutionError,
    RowContractError,
    ScenarioError,
)
from fraiseql_semis.pin import digest, facts_file
from fraiseql_semis.scenario import Run, ScenarioManager
from fraiseql_semis.schema import CONFITURE_VERSION, ColumnFacts, SchemaFacts
from tests.ddl import CODES, CONTRACT, CONTRACT_CODES, HIERARCHY, HIERARCHY_CODES, TRINITY

FACTS = "minimal_seed.facts.json"
MOVED = TRINITY.replace("name VARCHAR(50) NOT NULL", "name VARCHAR(50)")
SCENARIO = """\
scenario_id: 0x5001
name: minimal_seed
mode: prep-seed
seed: 42
tables:
  - name: catalog.tb_country
    count: 4
  - name: catalog.tb_continent
    count: 2
"""


def _manager(ddl: str = TRINITY) -> ScenarioManager:
    return ScenarioManager(SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES)))


def _run(tmp_path: Path, text: str = SCENARIO, *, ddl: str = TRINITY, no_pin: bool = False) -> Run:
    tmp_path.mkdir(exist_ok=True)
    scenario = tmp_path / "minimal_seed.yaml"
    scenario.write_text(text)
    manager = _manager(ddl)
    return manager.execute(manager.load(scenario), tmp_path / "out", no_pin=no_pin)


def _pinned(tmp_path: Path) -> str:
    """The scenario with the pin its first run wrote, copied in beside it as documented."""
    first = _run(tmp_path / "first")
    shutil.copy(first.pin_path.parent / FACTS, tmp_path)
    return SCENARIO + first.pin_path.read_text()


def test_prep_seed_writes_the_seeds_in_order_and_the_pin_beside_them(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert [seed.path.name for seed in run.seeds] == [
        "001_prep_seed.tb_continent.sql",
        "002_prep_seed.tb_country.sql",
    ]
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == [
        "001_prep_seed.tb_continent.sql",
        "002_prep_seed.tb_country.sql",
        FACTS,
        "schema_pin.yaml",
    ]
    assert run.notices == ("scenario minimal_seed is unpinned: its schema is not checked",)


def test_the_written_pin_is_the_block_a_scenario_records(tmp_path: Path) -> None:
    run = _run(tmp_path)
    block = yaml.safe_load(run.pin_path.read_text())["schema_pin"]
    assert block == {
        "source": "ddl",
        "digest": run.pin.digest,
        "confiture": CONFITURE_VERSION,
        "taken": run.pin.taken,
        "facts": FACTS,
    }
    kept = json.loads((tmp_path / "out" / FACTS).read_text())
    assert digest(kept) == run.pin.digest


def test_a_pinned_scenario_replays_against_its_schema(tmp_path: Path) -> None:
    run = _run(tmp_path, _pinned(tmp_path))
    assert run.notices[0].startswith("scenario minimal_seed matches its schema pin (ddl sha256:")


def test_a_pinned_scenario_is_refused_against_a_moved_schema(tmp_path: Path) -> None:
    text = _pinned(tmp_path)
    with pytest.raises(PinError, match=r"catalog\.tb_continent\.name: not_null true → false"):
        _run(tmp_path, text, ddl=MOVED)
    written = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert written == [FACTS, "schema_pin.yaml"]


def test_no_pin_runs_against_a_moved_schema_and_says_so(tmp_path: Path) -> None:
    run = _run(tmp_path, _pinned(tmp_path), ddl=MOVED, no_pin=True)
    assert len(run.seeds) == 2
    assert run.notices == (
        "scenario minimal_seed: the schema pin is not checked for this run (no_pin)",
        # The move made name nullable, so nobody naming it leaves it NULL.
        "catalog.tb_continent leaves name NULL; fill: draws them",
    )


def test_prep_seed_is_byte_reproducible(tmp_path: Path) -> None:
    first = [seed.path.read_bytes() for seed in _run(tmp_path / "a").seeds]
    second = [seed.path.read_bytes() for seed in _run(tmp_path / "b").seeds]
    assert first == second


def test_prep_seed_takes_no_connection(tmp_path: Path) -> None:
    scenario = tmp_path / "s.yaml"
    scenario.write_text(SCENARIO)
    manager = _manager()
    with pytest.raises(
        ScenarioError,
        match=r"^scenario minimal_seed runs in prep-seed mode, which reaches no database\n"
        r"Hint: Apply the written seeds separately, or declare read-back\.$",
    ):
        manager.execute(manager.load(scenario), tmp_path, connection=object())  # ty: ignore[invalid-argument-type]


def test_read_back_needs_a_connection(tmp_path: Path) -> None:
    with pytest.raises(
        ScenarioError,
        match=r"^scenario minimal_seed runs in read-back mode, which needs a connection\n"
        r"Hint: Pass the connection whose transaction the run belongs to\.$",
    ):
        _run(tmp_path, SCENARIO.replace("prep-seed", "read-back"))


def test_validation_refuses_a_read_back_scenario(tmp_path: Path) -> None:
    scenario = tmp_path / "s.yaml"
    scenario.write_text(SCENARIO.replace("prep-seed", "read-back"))
    manager = _manager()
    with pytest.raises(
        ScenarioError,
        match=r"^scenario minimal_seed runs in read-back mode: confiture's five levels judge "
        r"prep-seed seeds\nHint: A read-back run is checked as it applies: semis apply "
        r"--dry-run\.$",
    ):
        manager.validate(manager.load(scenario), schema_dir=tmp_path)


def _product_run(tmp_path: Path, table: str) -> Run:
    scenario = tmp_path / "s.yaml"
    scenario.write_text(
        "scenario_id: 0x5001\nname: products\nmode: prep-seed\nseed: 42\ntables:\n" + table
    )
    facts = SchemaFacts.from_source(CONTRACT, table_codes=TableCodes(CONTRACT_CODES))
    manager = ScenarioManager(facts, providers={"house_note": _house_note})
    return manager.execute(manager.load(scenario), tmp_path / "out")


def _house_note(_: object, column: ColumnFacts) -> str:
    return f"house {column.name}"


def test_trusts_trigger_and_providers_reach_the_rows(tmp_path: Path) -> None:
    run = _product_run(
        tmp_path,
        "  - name: catalog.tb_product\n"
        "    count: 2\n"
        "    trusts_trigger: [created_by]\n"
        "    providers: {note: house_note}\n",
    )
    (seed,) = run.seeds
    text = seed.path.read_text()
    assert "created_by" not in text
    assert text.count("'house note'") == 2


def test_trusting_a_column_the_table_does_not_write_is_refused(tmp_path: Path) -> None:
    with pytest.raises(
        ScenarioError, match="trusts created_at to a trigger, which is not a column"
    ):
        _product_run(
            tmp_path,
            "  - name: catalog.tb_product\n    count: 1\n    trusts_trigger: [created_at]\n",
        )


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ("schema_pin: [ddl]\n", "schema_pin is a mapping"),
        ("schema_pin: {source: ddl}\n", "schema_pin has no confiture, digest, facts, taken"),
        (
            "schema_pin: {source: ddl, digest: d, confiture: c, taken: 2026-09-23, by: me}\n",
            "schema_pin has unknown key by",
        ),
        (
            "schema_pin: {source: git, digest: d, confiture: c, taken: 2026-09-23, facts: f}\n",
            "schema_pin is malformed",
        ),
        (
            "schema_pin: {source: ddl, digest: d, confiture: c, taken: 2026-09-23,"
            " facts: minimal_seed.facts.json}\n",
            "names facts .*minimal_seed.facts.json, which does not exist",
        ),
    ],
)
def test_a_malformed_pin_block_is_refused(tmp_path: Path, block: str, match: str) -> None:
    scenario = tmp_path / "s.yaml"
    scenario.write_text(SCENARIO + block)
    with pytest.raises(ScenarioError, match=match):
        _manager().load(scenario)


@pytest.mark.parametrize(
    "block",
    [
        "{source: ddl, digest: sha256:0, confiture: 1.27.0, taken: 2026-09-30,"
        " snapshot: schema_pin.ddl}",
        "{source: live, digest: sha256:0, confiture: 1.27.0, taken: 2026-09-30}",
    ],
    ids=["ddl", "live"],
)
def test_a_pin_written_by_0_1_0_is_refused_with_a_hint_to_re_pin(
    tmp_path: Path, block: str
) -> None:
    scenario = tmp_path / "s.yaml"
    scenario.write_text(SCENARIO + f"schema_pin: {block}\n")
    with pytest.raises(ScenarioError) as refused:
        _manager().load(scenario)
    assert str(refused.value).splitlines() == [
        "scenario minimal_seed: schema_pin was written by semis 0.1.0, and keeps no facts",
        "Hint: Re-pin the scenario: delete its schema_pin: block, run it with -o <dir>, then "
        "put the schema_pin.yaml written beside its seeds in the block's place and the "
        "minimal_seed.facts.json beside the scenario file.",
    ]


@pytest.mark.parametrize("kept", ["[]", "not json", '[{"table": "catalog.tb_other"}]'])
def test_facts_that_are_not_the_ones_digested_are_refused(tmp_path: Path, kept: str) -> None:
    """A stale or swapped facts file would name the wrong changes: it is refused at load."""
    text = _pinned(tmp_path)
    (tmp_path / FACTS).write_text(kept)
    scenario = tmp_path / "s.yaml"
    scenario.write_text(text)
    with pytest.raises(ScenarioError, match=rf"{FACTS} does not hold the facts its schema_pin"):
        _manager().load(scenario)


@pytest.mark.parametrize(
    "named", ["other.facts.json", "../minimal_seed.facts.json", "/etc/hostname"]
)
def test_a_pin_naming_any_file_but_its_scenarios_facts_is_refused(
    tmp_path: Path, named: str
) -> None:
    """A pin is read beside its scenario, from the one file a run of it writes: never from
    a path the pin names, which could be anywhere."""
    text = _pinned(tmp_path).replace(f"facts: {FACTS}", f"facts: {named}")
    (tmp_path / "other.facts.json").write_text((tmp_path / FACTS).read_text())
    scenario = tmp_path / "s.yaml"
    scenario.write_text(text)
    with pytest.raises(ScenarioError) as refused:
        _manager().load(scenario)
    assert str(refused.value).splitlines() == [
        f"scenario minimal_seed: schema_pin names facts {named}, where a run of it keeps "
        f"them in {FACTS}",
        "Hint: Paste the block of the schema_pin.yaml one run wrote into the scenario, and "
        "copy that run's facts file beside it, both unchanged.",
    ]


def test_facts_nested_past_what_json_reads_are_refused(tmp_path: Path) -> None:
    text = _pinned(tmp_path)
    (tmp_path / FACTS).write_text("[" * 100_000 + "]" * 100_000)
    scenario = tmp_path / "s.yaml"
    scenario.write_text(text)
    with pytest.raises(ScenarioError, match=rf"{FACTS} does not hold the facts its schema_pin"):
        _manager().load(scenario)


def test_the_facts_file_is_named_after_the_scenario() -> None:
    assert facts_file("catalog.tb_continent") == "catalog.tb_continent.facts.json"
    assert facts_file("a.b") == "a.b.facts.json"


def test_a_prep_seed_hierarchy_is_one_file_its_children_carrying_parent_uuids(
    tmp_path: Path,
) -> None:
    scenario = tmp_path / "locations.yaml"
    scenario.write_text(
        "scenario_id: 0x5001\nname: locations\nmode: prep-seed\nseed: 42\ntables:\n"
        "  - name: catalog.tb_location\n    count: 5\n"
        "    hierarchy: {parent: fk_parent_location, roots: 1, fan_out: 4}\n"
    )
    facts = SchemaFacts.from_source(HIERARCHY, table_codes=TableCodes(HIERARCHY_CODES))
    manager = ScenarioManager(facts)
    run = manager.execute(manager.load(scenario), tmp_path / "out")
    (seed,) = run.seeds
    root = "05060708-5001-8001-8000-000000000001"
    assert seed.path.read_text().count(root) == 1 + 4


def test_no_file_a_run_writes_is_read_as_a_seed_but_its_seeds(tmp_path: Path) -> None:
    """confiture's directory readers take every ``*.sql``: only the seeds may be one."""
    run = _run(tmp_path)
    assert sorted((tmp_path / "out").glob("*.sql")) == sorted(seed.path for seed in run.seeds)


def test_a_parent_the_run_lacks_is_refused_before_a_row_is_drawn(tmp_path: Path) -> None:
    """``check`` refuses what the draw would: ``validate`` and ``seeds`` agree."""
    scenario = tmp_path / "orphans.yaml"
    scenario.write_text(SCENARIO.replace("  - name: catalog.tb_continent\n    count: 2\n", ""))
    manager = _manager()
    with pytest.raises(
        ResolutionError,
        match=r"^scenario minimal_seed: .*references catalog\.tb_continent, which has no rows",
    ):
        manager.check(manager.load(scenario))


def test_a_row_the_contract_refuses_names_its_scenario(tmp_path: Path) -> None:
    """ARCHITECTURE §9: the refusal names the table, the column and the scenario."""
    scenario = tmp_path / "products.yaml"
    scenario.write_text(
        "scenario_id: 0x5002\nname: products\nmode: prep-seed\nseed: 42\ntables:\n"
        "  - name: catalog.tb_product\n    count: 2\n    overrides:\n      sku: same\n"
    )
    manager = ScenarioManager(
        SchemaFacts.from_source(CONTRACT, table_codes=TableCodes(CONTRACT_CODES))
    )
    with pytest.raises(RowContractError, match=r"^scenario products: .*tb_product\.sku is unique"):
        manager.execute(manager.load(scenario), tmp_path / "out")


OPTIONAL_CONTINENT = TRINITY.replace(
    "fk_continent BIGINT NOT NULL REFERENCES", "fk_continent BIGINT REFERENCES"
)
COUNTRIES_ALONE = """\
scenario_id: 0x5001
name: minimal_seed
mode: prep-seed
seed: 42
tables:
  - name: catalog.tb_country
    count: 2
    overrides:
      fk_continent: null
"""


def test_a_nullable_foreign_key_overridden_null_needs_no_parent(tmp_path: Path) -> None:
    run = _run(tmp_path, COUNTRIES_ALONE, ddl=OPTIONAL_CONTINENT)
    (seed,) = run.seeds
    assert seed.path.read_text().count("NULL") == 2


def test_a_not_null_key_left_null_is_refused_before_a_row_is_drawn(tmp_path: Path) -> None:
    scenario = tmp_path / "orphans.yaml"
    scenario.write_text(COUNTRIES_ALONE)
    manager = _manager(TRINITY)
    with pytest.raises(
        ScenarioError,
        match=r"catalog\.tb_country\.fk_continent is NOT NULL, so it cannot be left null",
    ):
        manager.check(manager.load(scenario))


def test_a_nullable_self_fk_overridden_null_is_a_flat_set(tmp_path: Path) -> None:
    """One line, no hierarchy: every row of a self-referencing table is a root."""
    flat = TRINITY.replace(
        "fk_continent BIGINT NOT NULL REFERENCES catalog.tb_continent (pk_continent)",
        "fk_continent BIGINT REFERENCES catalog.tb_country (pk_country)",
    )
    run = _run(tmp_path, COUNTRIES_ALONE, ddl=flat)
    (seed,) = run.seeds
    assert seed.path.read_text().count("NULL") == 2


def test_an_existing_parent_needs_no_rows_in_the_run(tmp_path: Path) -> None:
    scenario = tmp_path / "countries.yaml"
    scenario.write_text(
        SCENARIO.replace("prep-seed", "read-back").replace(
            "  - name: catalog.tb_continent\n    count: 2\n",
            "existing:\n  - name: catalog.tb_continent\n",
        )
    )
    manager = _manager()
    assert manager.check(manager.load(scenario)) == (
        "scenario minimal_seed is unpinned: its schema is not checked",
    )


@pytest.mark.parametrize(
    ("ddl", "where", "match"),
    [
        (
            TRINITY.replace(
                "pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,", "note TEXT,"
            ),
            "",
            r"existing catalog\.tb_continent shows no surrogate key",
        ),
        (
            TRINITY.replace(
                "    identifier TEXT NOT NULL UNIQUE,\n    name VARCHAR(50)",
                "    name VARCHAR(50)",
                1,
            ),
            "    where: {identifier: [eu]}\n",
            r"existing catalog\.tb_continent has no identifier column for where:",
        ),
    ],
    ids=["no-surrogate-key", "no-identifier"],
)
def test_an_existing_table_read_back_cannot_point_at_is_refused(
    tmp_path: Path, ddl: str, where: str, match: str
) -> None:
    scenario = tmp_path / "countries.yaml"
    scenario.write_text(
        SCENARIO.replace("prep-seed", "read-back").replace(
            "  - name: catalog.tb_continent\n    count: 2\n",
            "existing:\n  - name: catalog.tb_continent\n" + where,
        )
    )
    manager = _manager(ddl)
    with pytest.raises(ScenarioError, match=rf"scenario minimal_seed: {match}"):
        manager.check(manager.load(scenario))


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        ("    fill: name\n", r"catalog\.tb_continent: fill lists column names"),
        ("    count: -1\n", r"catalog\.tb_continent: count is -1"),
        ("    fill: [name]\n", r"catalog\.tb_continent fills name, which is NOT NULL"),
        (
            "    overrides: {pk_continent: 1}\n",
            r"catalog\.tb_continent\.pk_continent is not a column",
        ),
    ],
    ids=["parsed", "spec", "fill", "override"],
)
def test_a_tables_refusal_names_its_scenario(tmp_path: Path, entry: str, match: str) -> None:
    """Whether the file is read or the run is checked, the refusal opens with the scenario."""
    text = SCENARIO.replace("    count: 2\n", "    count: 2\n" + entry)
    if "count: -1" in entry:
        text = SCENARIO.replace("    count: 2\n", entry)
    scenario = tmp_path / "s.yaml"
    scenario.write_text(text)
    manager = _manager()
    with pytest.raises(ScenarioError, match=rf"^scenario minimal_seed: {match}"):
        manager.check(manager.load(scenario))


def test_a_table_with_no_code_is_refused_naming_the_scenario(tmp_path: Path) -> None:
    codes = {table: code for table, code in CODES.items() if table != "catalog.tb_country"}
    manager = ScenarioManager(SchemaFacts.from_source(TRINITY, table_codes=TableCodes(codes)))
    scenario = tmp_path / "s.yaml"
    scenario.write_text(SCENARIO)
    with pytest.raises(
        CodeRegistryError, match=r"^scenario minimal_seed: catalog\.tb_country has no table code"
    ):
        manager.check(manager.load(scenario))


def test_fill_naming_the_hierarchys_path_is_refused(tmp_path: Path) -> None:
    """Read-back fills the path from the keys PostgreSQL gives: there is nothing to draw."""
    manager = ScenarioManager(
        SchemaFacts.from_source(HIERARCHY, table_codes=TableCodes(HIERARCHY_CODES))
    )
    scenario = tmp_path / "locations.yaml"
    scenario.write_text(
        "scenario_id: 0x5001\nname: locations\nmode: read-back\nseed: 42\ntables:\n"
        "  - name: catalog.tb_location\n    count: 3\n    fill: [path]\n"
        "    hierarchy: {parent: fk_parent_location, roots: 1, fan_out: 2, path: path}\n"
    )
    with pytest.raises(
        ScenarioError,
        match=r"^scenario locations: catalog\.tb_location fills path, which is the hierarchy's "
        r"path, which read-back fills from the keys",
    ):
        manager.check(manager.load(scenario))


def test_a_hierarchy_parent_trusted_to_a_trigger_names_the_scenario(tmp_path: Path) -> None:
    manager = ScenarioManager(
        SchemaFacts.from_source(HIERARCHY, table_codes=TableCodes(HIERARCHY_CODES))
    )
    scenario = tmp_path / "locations.yaml"
    scenario.write_text(
        "scenario_id: 0x5001\nname: locations\nmode: read-back\nseed: 42\ntables:\n"
        "  - name: catalog.tb_location\n    count: 3\n    trusts_trigger: [fk_parent_location]\n"
        "    hierarchy: {parent: fk_parent_location, roots: 1, fan_out: 2, path: path}\n"
    )
    with pytest.raises(
        ScenarioError,
        match=r"^scenario locations: catalog\.tb_location\.fk_parent_location is the parent of "
        r"the table's hierarchy, which semis draws: it cannot be trusted to a trigger",
    ):
        manager.check(manager.load(scenario))
