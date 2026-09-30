"""ScenarioManager.execute: a scenario run in its mode, its schema pinned beside its seeds."""

from pathlib import Path

import pytest
import yaml

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import PinError, ResolutionError, ScenarioError
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
    """The scenario with the pin its first run wrote, copied in beside it as documented."""
    first = _run(tmp_path / "first")
    (tmp_path / "schema_pin.ddl").write_text((first.pin_path.parent / "schema_pin.ddl").read_text())
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
        "schema_pin.ddl",
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
        "snapshot": "schema_pin.ddl",
    }
    assert (tmp_path / "out" / "schema_pin.ddl").read_text() == TRINITY


def test_a_pinned_scenario_replays_against_its_schema(tmp_path: Path) -> None:
    run = _run(tmp_path, _pinned(tmp_path))
    assert run.notices[0].startswith("scenario minimal_seed matches its schema pin (ddl sha256:")


def test_a_pinned_scenario_is_refused_against_a_moved_schema(tmp_path: Path) -> None:
    text = _pinned(tmp_path)
    with pytest.raises(PinError, match=r"CHANGE COLUMN NULLABLE catalog\.tb_continent\.name"):
        _run(tmp_path, text, ddl=MOVED)
    written = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert written == ["schema_pin.ddl", "schema_pin.yaml"]


def test_no_pin_runs_against_a_moved_schema_and_says_so(tmp_path: Path) -> None:
    run = _run(tmp_path, _pinned(tmp_path), ddl=MOVED, no_pin=True)
    assert len(run.seeds) == 2
    assert run.notices == (
        "scenario minimal_seed: the schema pin is not checked for this run (no_pin)",
    )


def test_prep_seed_is_byte_reproducible(tmp_path: Path) -> None:
    first = [seed.path.read_bytes() for seed in _run(tmp_path / "a").seeds]
    second = [seed.path.read_bytes() for seed in _run(tmp_path / "b").seeds]
    assert first == second


def test_prep_seed_takes_no_connection(tmp_path: Path) -> None:
    scenario = tmp_path / "s.yaml"
    scenario.write_text(SCENARIO)
    manager = _manager()
    with pytest.raises(ScenarioError, match="prep-seed mode, which reaches no database"):
        manager.execute(manager.load(scenario), tmp_path, connection=object())  # ty: ignore[invalid-argument-type]


def test_read_back_needs_a_connection(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError, match="read-back mode, which needs a connection"):
        _run(tmp_path, SCENARIO.replace("prep-seed", "read-back"))


def test_validation_refuses_a_read_back_scenario(tmp_path: Path) -> None:
    scenario = tmp_path / "s.yaml"
    scenario.write_text(SCENARIO.replace("prep-seed", "read-back"))
    manager = _manager()
    with pytest.raises(ScenarioError, match="confiture's five levels judge prep-seed seeds"):
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
        ("schema_pin: {source: ddl}\n", "schema_pin has no confiture, digest, taken"),
        (
            "schema_pin: {source: ddl, digest: d, confiture: c, taken: 2026-09-23, by: me}\n",
            "schema_pin has unknown key by",
        ),
        (
            "schema_pin: {source: git, digest: d, confiture: c, taken: 2026-09-23}\n",
            "schema_pin is malformed",
        ),
        (
            "schema_pin: {source: ddl, digest: d, confiture: c, taken: 2026-09-23,"
            " snapshot: gone.sql}\n",
            "names snapshot .*gone.sql, which does not exist",
        ),
    ],
)
def test_a_malformed_pin_block_is_refused(tmp_path: Path, block: str, match: str) -> None:
    scenario = tmp_path / "s.yaml"
    scenario.write_text(SCENARIO + block)
    with pytest.raises(ScenarioError, match=match):
        _manager().load(scenario)


def test_a_pin_without_a_snapshot_is_refused_with_its_digests_alone(tmp_path: Path) -> None:
    text = (
        SCENARIO + "schema_pin: {source: ddl, digest: sha256:0, confiture: c, taken: 2026-09-23}\n"
    )
    with pytest.raises(PinError, match="kept no DDL snapshot") as refused:
        _run(tmp_path, text)
    assert refused.value.recorded == "sha256:0"


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
    root = "05060708-5001-0001-0000-000000000001"
    assert seed.path.read_text().count(root) == 1 + 4


def test_a_pin_naming_a_sql_snapshot_still_replays(tmp_path: Path) -> None:
    """Scenarios pinned before the snapshot was named ``.ddl`` keep the name they recorded."""
    first = _run(tmp_path / "first")
    (tmp_path / "schema_pin.sql").write_text((first.pin_path.parent / "schema_pin.ddl").read_text())
    pinned = SCENARIO + first.pin_path.read_text().replace("schema_pin.ddl", "schema_pin.sql")
    run = _run(tmp_path, pinned)
    assert run.notices[0].startswith("scenario minimal_seed matches its schema pin (ddl sha256:")


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
        ResolutionError, match=r"references catalog\.tb_continent, which has no rows in this run"
    ):
        manager.check(manager.load(scenario))


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


@pytest.mark.parametrize(
    ("ddl", "match"),
    [
        (TRINITY, r"catalog\.tb_country\.fk_continent is NOT NULL, so it cannot be left null"),
        (
            TRINITY.replace(
                "fk_continent BIGINT NOT NULL REFERENCES catalog.tb_continent (pk_continent)",
                "fk_continent BIGINT REFERENCES catalog.tb_country (pk_country)",
            ),
            r"catalog\.tb_country\.fk_continent references its own table, so it cannot be "
            r"left null",
        ),
    ],
)
def test_a_key_that_cannot_be_left_null_is_refused_before_a_row_is_drawn(
    tmp_path: Path, ddl: str, match: str
) -> None:
    scenario = tmp_path / "orphans.yaml"
    scenario.write_text(COUNTRIES_ALONE)
    manager = _manager(ddl)
    with pytest.raises(ScenarioError, match=match):
        manager.check(manager.load(scenario))
