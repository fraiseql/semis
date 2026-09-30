"""The scenario the repository ships is the one the README shows, and it runs."""

from pathlib import Path

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.scenario import ScenarioManager
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, WORKED

ROOT = Path(__file__).resolve().parents[2]
MINIMAL_SEED = ROOT / "scenarios" / "minimal_seed.yaml"


def test_the_readme_shows_the_shipped_scenario() -> None:
    readme = (ROOT / "README.md").read_text()
    assert f"```yaml\n{MINIMAL_SEED.read_text()}```" in readme


def test_minimal_seed_runs_against_its_schema(tmp_path: Path) -> None:
    manager = ScenarioManager(SchemaFacts.from_source(WORKED, table_codes=TableCodes(CODES)))
    run = manager.execute(manager.load(MINIMAL_SEED), tmp_path)
    continents, countries = (seed.path.read_text() for seed in run.seeds)
    assert all(name in continents for name in ("'Africa'", "'South America'"))
    assert "created_by" not in countries
    assert countries.count("'03040506-5001-0001-") == 50
    assert run.notices == ("scenario minimal_seed is unpinned: its schema is not checked",)
