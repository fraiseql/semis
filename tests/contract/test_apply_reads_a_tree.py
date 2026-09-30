"""apply_seeds reads a directory as validate_seeds does: the whole tree (confiture#374).

semis still passes explicit file lists (D8); this pins that a directory no longer
validates what it never applies.
"""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from confiture.platform import apply_seeds

pytestmark = pytest.mark.integration

SCHEMA = "semis_tree"
TABLE = f"{SCHEMA}.tb_note"


@pytest.fixture
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        connection.execute(f"CREATE SCHEMA {SCHEMA}; CREATE TABLE {TABLE} (n INT)")
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


def test_a_nested_seed_is_applied(database: str, tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "one.sql").write_text(f"INSERT INTO {TABLE} VALUES (1);\n")
    result = apply_seeds(database, tmp_path)
    assert (result.total, result.succeeded) == (1, 1)
