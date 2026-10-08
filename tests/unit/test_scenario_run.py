"""ScenarioManager.execute: a scenario run in its mode, its schema pinned beside its seeds."""

from pathlib import Path

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import (
    CodeRegistryError,
    PinError,
    ResolutionError,
    RowContractError,
    ScenarioError,
)
from fraiseql_semis.pin import digest
from fraiseql_semis.scenario import Run, ScenarioManager
from fraiseql_semis.schema import CONFITURE_VERSION, ColumnFacts, SchemaFacts
from tests.ddl import CODES, CONTRACT, CONTRACT_CODES, HIERARCHY, HIERARCHY_CODES, TRINITY

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
    """The scenario, its pin taken beside it as semis pin takes it."""
    tmp_path.mkdir(exist_ok=True)
    path = tmp_path / "minimal_seed.yaml"
    path.write_text(SCENARIO)
    manager = _manager()
    manager.pin(manager.load(path), path)
    return SCENARIO


def test_prep_seed_writes_the_seeds_in_order_and_nothing_else(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert [seed.path.name for seed in run.seeds] == [
        "001_prep_seed.tb_continent.sql",
        "002_prep_seed.tb_country.sql",
    ]
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == [
        "001_prep_seed.tb_continent.sql",
        "002_prep_seed.tb_country.sql",
    ]
    assert run.notices == ("scenario minimal_seed is unpinned: its schema is not checked",)


def test_a_run_carries_the_pin_of_the_schema_it_read(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert (run.pin.source, run.pin.confiture) == ("ddl", CONFITURE_VERSION)
    assert digest(run.pin.recorded or []) == run.pin.digest


def test_a_pinned_scenario_replays_against_its_schema(tmp_path: Path) -> None:
    run = _run(tmp_path, _pinned(tmp_path))
    assert run.notices[0].startswith("scenario minimal_seed matches its schema pin (ddl sha256:")


def test_a_pinned_scenario_is_refused_against_a_moved_schema(tmp_path: Path) -> None:
    text = _pinned(tmp_path)
    with pytest.raises(PinError, match=r"catalog\.tb_continent\.name: not_null true → false"):
        _run(tmp_path, text, ddl=MOVED)
    assert list((tmp_path / "out").iterdir()) == []


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


def test_a_nullable_foreign_key_without_a_parent_is_written_null(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        COUNTRIES_ALONE.replace("    overrides:\n      fk_continent: null\n", ""),
        ddl=OPTIONAL_CONTINENT,
    )
    (seed,) = run.seeds
    assert seed.path.read_text().count("NULL") == 2
    assert run.notices[1:] == (
        "catalog.tb_country leaves fk_continent NULL; a parent under tables: points them",
    )


def test_a_nullable_foreign_key_to_a_parent_drawing_no_rows_is_written_null(
    tmp_path: Path,
) -> None:
    """A parent in the run with count: 0 counts as no parent."""
    text = SCENARIO.replace("count: 2\n", "count: 0\n")
    run = _run(tmp_path, text, ddl=OPTIONAL_CONTINENT)
    countries = run.seeds[-1].path.read_text()
    assert countries.count("NULL") == 4


def test_a_not_null_key_whose_parent_the_run_lacks_is_still_refused(tmp_path: Path) -> None:
    scenario = tmp_path / "orphans.yaml"
    scenario.write_text(COUNTRIES_ALONE.replace("    overrides:\n      fk_continent: null\n", ""))
    manager = _manager(TRINITY)
    with pytest.raises(
        ResolutionError,
        match=r"^scenario minimal_seed: catalog\.tb_country\.fk_continent references "
        r"catalog\.tb_continent, which has no rows in this run",
    ):
        manager.check(manager.load(scenario))


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
