"""seeds.write / seeds.apply: semis' rows through confiture's writers and applier."""

import inspect
from collections.abc import Sequence
from pathlib import Path

import pytest
from confiture.platform import SeedError, SeedFile

from fraiseql_semis import seeds
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.generator import FakeDataGenerator
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, TRINITY

FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
TABLE = "catalog.tb_continent"


def _rows(count: int = 3) -> list[dict[str, object]]:
    return list(
        FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42).generate_rows(TABLE, count=count)
    )


def test_write_returns_a_seed_file(tmp_path: Path) -> None:
    seed = seeds.write(tmp_path / "continent.sql", TABLE, _rows(), facts=FACTS, mode="read-back")
    assert isinstance(seed, SeedFile)
    assert (seed.table.display, seed.columns, seed.rows) == (
        TABLE,
        ("id", "identifier", "name"),
        3,
    )


def test_supplying_the_identity_pk_is_refused(tmp_path: Path) -> None:
    rows = [{"pk_continent": 1, **row} for row in _rows()]
    with pytest.raises(SeedError, match=r"PostgreSQL fills catalog\.tb_continent\.pk_continent"):
        seeds.write(tmp_path / "continent.sql", TABLE, rows, facts=FACTS, mode="read-back")


@pytest.mark.parametrize(
    ("mode", "fmt", "expected"),
    [
        ("prep-seed", None, "insert"),
        ("read-back", None, "copy"),
        ("prep-seed", "copy", "copy"),
        ("read-back", "insert", "insert"),
    ],
)
def test_format_follows_the_mode_unless_given(
    tmp_path: Path, mode: seeds.Mode, fmt: seeds.Format | None, expected: str
) -> None:
    seed = seeds.write(tmp_path / "c.sql", TABLE, _rows(), facts=FACTS, mode=mode, format=fmt)
    assert seed.format == expected


def test_no_rows_still_names_the_writable_columns(tmp_path: Path) -> None:
    seed = seeds.write(tmp_path / "c.sql", TABLE, [], facts=FACTS, mode="read-back")
    assert seed.columns == ("id", "identifier", "name")


def test_columns_are_stable_across_rows() -> None:
    assert len({tuple(row) for row in _rows(count=10)}) == 1


def test_apply_takes_an_explicit_file_list() -> None:
    annotation = inspect.signature(seeds.apply).parameters["paths"].annotation
    assert annotation == Sequence[Path | str]


@pytest.mark.parametrize("single", ["continent.sql", Path("continent.sql")])
def test_apply_refuses_a_single_path(single: str | Path) -> None:
    with pytest.raises(TypeError, match="list"):
        seeds.apply("postgresql:///unused", single)  # ty: ignore[invalid-argument-type]


def test_apply_refuses_a_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="the seed files themselves"):
        seeds.apply("postgresql:///unused", [tmp_path])


def test_rows_may_be_an_iterator(tmp_path: Path) -> None:
    listed = seeds.write(tmp_path / "listed.sql", TABLE, _rows(), facts=FACTS, mode="read-back")
    streamed = seeds.write(
        tmp_path / "streamed.sql", TABLE, iter(_rows()), facts=FACTS, mode="read-back"
    )
    assert (streamed.columns, streamed.rows) == (listed.columns, listed.rows)
    assert streamed.path.read_bytes() == listed.path.read_bytes()


def test_no_rows_from_an_iterator_still_names_the_writable_columns(tmp_path: Path) -> None:
    seed = seeds.write(tmp_path / "c.sql", TABLE, iter([]), facts=FACTS, mode="read-back")
    assert seed.columns == ("id", "identifier", "name")
