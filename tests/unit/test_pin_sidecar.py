"""A scenario's pin is the file beside it: read when the scenario loads, and only there."""

import json
from pathlib import Path

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import PinError, ScenarioError
from fraiseql_semis.scenario import ScenarioManager
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, TRINITY

SCENARIO = """\
scenario_id: 0x5001
name: minimal_seed
mode: prep-seed
seed: 42
tables:
  - name: catalog.tb_continent
    count: 2
"""
MOVED = TRINITY.replace("name VARCHAR(50) NOT NULL", "name VARCHAR(50)")


def _manager(ddl: str = TRINITY) -> ScenarioManager:
    return ScenarioManager(SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES)))


def _pinned(tmp_path: Path) -> Path:
    path = tmp_path / "minimal_seed.yaml"
    path.write_text(SCENARIO)
    manager = _manager()
    manager.pin(manager.load(path), path)
    return path


def test_a_scenario_with_its_pin_file_beside_it_is_pinned(tmp_path: Path) -> None:
    manager = _manager()
    notices = manager.check(manager.load(_pinned(tmp_path)))
    assert notices[0].startswith("scenario minimal_seed matches its schema pin (ddl sha256:")


def test_a_pinned_scenario_is_refused_on_a_moved_schema_naming_semis_pin(tmp_path: Path) -> None:
    manager = _manager(MOVED)
    with pytest.raises(PinError) as refused:
        manager.check(manager.load(_pinned(tmp_path)))
    assert str(refused.value).splitlines()[1:] == [
        "What moved:",
        "  catalog.tb_continent.name: not_null true → false",
        "Hint: Review the changes, then accept them with semis pin on the scenario, or pass "
        "--no-pin for one run.",
    ]


def test_a_scenario_keeping_a_schema_pin_block_is_refused_naming_semis_pin(
    tmp_path: Path,
) -> None:
    path = tmp_path / "minimal_seed.yaml"
    path.write_text(SCENARIO + "schema_pin: {source: ddl, digest: sha256:0}\n")
    with pytest.raises(ScenarioError) as refused:
        _manager().load(path)
    assert str(refused.value).splitlines() == [
        "scenario minimal_seed keeps a schema_pin: block, where semis 0.2.0 kept its pin; the "
        "pin is now minimal_seed.pin.json, beside the scenario",
        "Hint: Delete the schema_pin: block and minimal_seed.facts.json, then run semis pin "
        f"{path}.",
    ]


def test_a_scenario_with_a_facts_file_beside_it_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "minimal_seed.yaml"
    path.write_text(SCENARIO)
    (tmp_path / "minimal_seed.facts.json").write_text("[]\n")
    with pytest.raises(
        ScenarioError,
        match=r"^scenario minimal_seed has minimal_seed\.facts\.json beside it, where semis "
        r"0\.2\.0 kept its pin",
    ):
        _manager().load(path)


def test_a_pin_file_that_does_not_read_refuses_the_load(tmp_path: Path) -> None:
    path = _pinned(tmp_path)
    (tmp_path / "minimal_seed.pin.json").write_text(json.dumps({"digest": "sha256:0"}))
    with pytest.raises(ScenarioError, match=r"^scenario minimal_seed: its pin file is malformed"):
        _manager().load(path)


def test_read_pin_false_loads_unpinned_whatever_the_file_holds(tmp_path: Path) -> None:
    path = _pinned(tmp_path)
    (tmp_path / "minimal_seed.pin.json").write_text("{not json")
    assert _manager().load(path, read_pin=False).pin is None
