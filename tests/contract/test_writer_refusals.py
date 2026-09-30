"""What confiture's seed writers refuse — the backstops seeds.write relies on."""

from pathlib import Path

import pytest
from confiture.platform import SeedError, parse_schema, write_copy_seed, write_insert_seed

from tests.ddl import TRINITY

MODEL = parse_schema(TRINITY)
TABLE = "catalog.tb_continent"
WRITERS = pytest.mark.parametrize("writer", [write_copy_seed, write_insert_seed])


@WRITERS
def test_identity_column_is_refused_and_nothing_is_written(writer, tmp_path: Path) -> None:
    path = tmp_path / "seed.sql"
    with pytest.raises(SeedError, match=r"PostgreSQL fills catalog\.tb_continent\.pk_continent"):
        writer(path, TABLE, ["pk_continent", "id"], [{"pk_continent": 1, "id": "x"}], model=MODEL)
    assert not path.exists()


@WRITERS
def test_row_missing_a_named_column_is_refused(writer, tmp_path: Path) -> None:
    with pytest.raises(SeedError, match="missing 'name'"):
        writer(tmp_path / "seed.sql", TABLE, ["id", "name"], [{"id": "x"}], model=MODEL)


@WRITERS
def test_row_with_a_column_not_named_is_refused(writer, tmp_path: Path) -> None:
    with pytest.raises(SeedError, match="'name', which is not among the columns written"):
        writer(tmp_path / "seed.sql", TABLE, ["id"], [{"id": "x", "name": "y"}], model=MODEL)


def test_copy_with_no_columns_writes_an_empty_column_list(tmp_path: Path) -> None:
    """Why seeds.write names the writable columns when there are no rows."""
    path = tmp_path / "seed.sql"
    write_copy_seed(path, TABLE, [], [], model=MODEL)
    assert path.read_text().startswith("COPY catalog.tb_continent () FROM stdin;")
