"""Prep-seed level 1 judges a UUID column's values only, in every row (confiture#387).

A slug in a text column is not a UUID, and a bad UUID after the first row is found.
"""

from pathlib import Path

from confiture.platform import validate_seeds

VALID = "00000000-0000-0000-0000-000000000001"


def _level_1(tmp_path: Path, rows: str) -> list[str]:
    (tmp_path / "010_x.sql").write_text(f"INSERT INTO prep_seed.tb_x (id, v) VALUES\n{rows};\n")
    report = validate_seeds(tmp_path, schema_dir=tmp_path, max_level=1)
    return [violation.message for violation in report.violations]


def test_a_hyphenated_slug_is_not_a_uuid(tmp_path: Path) -> None:
    assert _level_1(tmp_path, f"    ('{VALID}', 'europe-5001-3')") == []


def test_a_bad_uuid_after_the_first_row_is_found(tmp_path: Path) -> None:
    assert len(_level_1(tmp_path, f"    ('{VALID}', 'x'),\n    ('not-a-uuid', 'y')")) == 1
