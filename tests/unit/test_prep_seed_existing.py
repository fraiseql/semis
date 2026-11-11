"""A prep-seed run points at existing rows by the UUIDs its pin records, and reaches no
database."""

from dataclasses import replace
from pathlib import Path

import pytest

from fraiseql_semis import pin_file
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.pin import PinnedRead, SchemaPin
from fraiseql_semis.scenario import ScenarioManager
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, TRINITY

EUROPE, ASIA = "00000000-0000-4000-8000-0000000000e0", "00000000-0000-4000-8000-0000000000a5"
CONTINENTS = PinnedRead("catalog.tb_continent", "identifier", None, (("eu", EUROPE), ("as", ASIA)))
SCENARIO = """\
scenario_id: 0x5001
name: countries
mode: prep-seed
seed: 42
tables:
  - name: catalog.tb_country
    count: 4
existing:
  - name: catalog.tb_continent
"""
FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))


def _scenario(tmp_path: Path, text: str = SCENARIO, rows: object = (CONTINENTS,)) -> Path:
    """The scenario, with a pin of this schema recording *rows*; none for ``None``,
    no pin file for ``"no pin"``."""
    path = tmp_path / "countries.yaml"
    path.write_text(text)
    if rows != "no pin":
        manager = ScenarioManager(FACTS)
        scenario = manager.load(path, read_pin=False)
        pin = SchemaPin.of(
            FACTS,
            [spec.name for spec in scenario.tables],
            twins=["prep_seed.tb_country"],
            existing=[table.name for table in scenario.existing],
        )
        pin_file.write(pin_file.path_for(path, "countries"), replace(pin, rows=rows))
    return path


def test_a_prep_seed_child_points_at_the_pinned_rows_round_robin(tmp_path: Path) -> None:
    """As read-back would by position: the first country the first continent, and on."""
    manager = ScenarioManager(FACTS)
    run = manager.execute(manager.load(_scenario(tmp_path)), tmp_path / "out")
    (seed,) = run.seeds
    shown = seed.path.read_text()
    assert [shown.index(uuid) > 0 for uuid in (EUROPE, ASIA)] == [True, True]
    rows = [line for line in shown.splitlines() if line.lstrip().startswith("('")]
    assert [EUROPE in row for row in rows] == [True, False, True, False]
    assert [ASIA in row for row in rows] == [False, True, False, True]


def test_two_prep_seed_runs_are_byte_identical(tmp_path: Path) -> None:
    manager = ScenarioManager(FACTS)
    path = _scenario(tmp_path)
    first = manager.execute(manager.load(path), tmp_path / "a")
    second = manager.execute(manager.load(path), tmp_path / "b")
    assert first.seeds[0].path.read_bytes() == second.seeds[0].path.read_bytes()


HINT = (
    "Hint: Record them with semis pin on the scenario, -d naming the database they are read from."
)


@pytest.mark.parametrize(
    ("text", "rows", "said"),
    [
        (SCENARIO, "no pin", "its pin records none"),
        (SCENARIO, None, "its pin records none"),
        (SCENARIO, (), "its pin records none"),
        (
            SCENARIO + "    where: {identifier: [eu, oc]}\n",
            (replace(CONTINENTS, where=("eu",), rows=(("eu", EUROPE),)),),
            "its pin records no row whose identifier is 'oc'",
        ),
    ],
    ids=["no-pin-file", "no-rows", "no-reads", "where-value"],
)
def test_a_prep_seed_scenario_its_pin_does_not_cover_is_refused_at_check(
    tmp_path: Path, text: str, rows: object, said: str
) -> None:
    manager = ScenarioManager(FACTS)
    scenario = manager.load(_scenario(tmp_path, text, rows))
    for no_pin in (False, True):
        with pytest.raises(ScenarioError) as refused:
            manager.check(scenario, no_pin=no_pin)
        assert str(refused.value).splitlines() == [
            f"scenario countries: prep-seed points at catalog.tb_continent's rows by UUID, "
            f"and {said}",
            HINT,
        ]


def test_an_existing_table_with_no_uuid_id_is_refused_in_prep_seed(tmp_path: Path) -> None:
    ddl = TRINITY.replace(
        "PRIMARY KEY,\n    id UUID NOT NULL UNIQUE,\n    identifier TEXT NOT NULL UNIQUE,\n"
        "    name VARCHAR(50) NOT NULL",
        "PRIMARY KEY,\n    id TEXT NOT NULL UNIQUE,\n    identifier TEXT NOT NULL UNIQUE,\n"
        "    name VARCHAR(50) NOT NULL",
        1,
    )
    manager = ScenarioManager(SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES)))
    with pytest.raises(
        ScenarioError,
        match=r"^scenario countries: existing catalog\.tb_continent has no uuid id for a "
        r"prep-seed child to carry",
    ):
        manager.check(manager.load(_scenario(tmp_path, rows="no pin")), no_pin=True)
