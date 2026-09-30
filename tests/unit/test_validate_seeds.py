"""seeds.validate: confiture's prep-seed levels over the seeds semis wrote."""

from pathlib import Path

from confiture.platform import PrepSeedPattern, ViolationSeverity

from fraiseql_semis import seeds
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, TRINITY

FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
TWIN = "prep_seed.tb_continent"
EUROPE = "02030405-5001-0001-0000-000000000001"


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    seeds_dir, schema_dir = tmp_path / "seeds", tmp_path / "schema"
    seeds_dir.mkdir()
    schema_dir.mkdir()
    (schema_dir / "catalog.sql").write_text(TRINITY)
    return seeds_dir, schema_dir


def test_levels_1_to_3_report_a_bad_uuid(tmp_path: Path) -> None:
    seeds_dir, schema_dir = _dirs(tmp_path)
    rows = [
        {"id": EUROPE, "identifier": "europe", "name": "Europe"},
        {"id": "not-a-uuid", "identifier": "asia", "name": "Asia"},
    ]
    seeds.write(seeds_dir / "001_continent.sql", TWIN, rows, facts=FACTS, mode="prep-seed")
    report = seeds.validate(seeds_dir, schema_dir=schema_dir, max_level=3)
    found = [
        (
            violation.pattern,
            violation.severity,
            Path(violation.file_path).name,
            violation.line_number,
        )
        for violation in report.violations
    ]
    assert found == [
        (PrepSeedPattern.INVALID_UUID_FORMAT, ViolationSeverity.ERROR, "001_continent.sql", 3),
        (PrepSeedPattern.MISSING_RESOLVER_FUNCTION, ViolationSeverity.WARNING, "schema", 1),
    ]
