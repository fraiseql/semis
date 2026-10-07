"""Prep-seed level 1 judges a COPY seed as it judges an INSERT one (confiture#366).

So `--format copy` in prep-seed mode is validated, not waved through.
"""

from pathlib import Path

import pytest
from confiture.platform import PrepSeedPattern, ViolationSeverity, validate_seeds

from fraiseql_semis import seeds
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, TRINITY

FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
EUROPE = "02030405-5001-8001-8000-000000000001"
ROWS = [
    {"id": EUROPE, "identifier": "europe", "name": "Europe"},
    {"id": "not-a-uuid", "identifier": "asia", "name": "Asia"},
]


def _level_1(
    tmp_path: Path, table: str, fmt: seeds.Format
) -> list[tuple[PrepSeedPattern, ViolationSeverity]]:
    seeds_dir = tmp_path / fmt
    seeds_dir.mkdir()
    seeds.write(seeds_dir / "001.sql", table, ROWS, facts=FACTS, mode="prep-seed", format=fmt)
    report = validate_seeds(seeds_dir, schema_dir=seeds_dir, max_level=1)
    return [(violation.pattern, violation.severity) for violation in report.violations]


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("prep_seed.tb_continent", {PrepSeedPattern.INVALID_UUID_FORMAT}),
        (
            "catalog.tb_continent",
            {PrepSeedPattern.INVALID_UUID_FORMAT, PrepSeedPattern.PREP_SEED_TARGET_MISMATCH},
        ),
    ],
)
def test_copy_and_insert_draw_the_same_findings(
    tmp_path: Path, table: str, expected: set[PrepSeedPattern]
) -> None:
    copy, insert = _level_1(tmp_path, table, "copy"), _level_1(tmp_path, table, "insert")
    assert copy == insert
    assert {pattern for pattern, _ in copy} == expected
