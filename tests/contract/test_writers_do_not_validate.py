"""Why the row contract exists: confiture's writers check a row's shape, not its values.

Each test pins one measured acceptance (ARCHITECTURE §6). When one starts failing,
confiture has begun validating that fact, and rows.py can say so.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest
from confiture.platform import parse_schema, write_copy_seed, write_insert_seed

from tests.ddl import TRINITY

MODEL = parse_schema(TRINITY)
CONTINENT = "catalog.tb_continent"
WRITERS = pytest.mark.parametrize("writer", [write_copy_seed, write_insert_seed])


def _written(writer, path: Path, table: str, row: Mapping[str, object]) -> str:
    writer(path, table, list(row), [row], model=MODEL)
    return path.read_text()


@WRITERS
def test_not_a_uuid_is_accepted_into_a_uuid_column(writer, tmp_path: Path) -> None:
    row = {"id": "not-a-uuid", "identifier": "a", "name": "b"}
    assert "not-a-uuid" in _written(writer, tmp_path / "s.sql", CONTINENT, row)


@WRITERS
def test_a_value_longer_than_the_declared_length_is_accepted(writer, tmp_path: Path) -> None:
    row = {"id": "x", "identifier": "a", "name": "n" * 80}
    assert "n" * 80 in _written(writer, tmp_path / "s.sql", CONTINENT, row)


@WRITERS
def test_none_is_accepted_into_a_not_null_column(writer, tmp_path: Path) -> None:
    row = {"id": "x", "identifier": None, "name": "b"}
    assert _written(writer, tmp_path / "s.sql", CONTINENT, row)


@WRITERS
def test_a_not_null_column_left_out_is_accepted(writer, tmp_path: Path) -> None:
    row = {"id": "x"}
    assert _written(writer, tmp_path / "s.sql", "catalog.tb_country", row)
