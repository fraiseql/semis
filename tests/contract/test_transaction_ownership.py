"""Who owns the transaction apply_seeds runs in: a URL commits, a connection does not.

Read-back mode's one-transaction guarantee rests on the second half (ARCHITECTURE §5).
"""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from confiture.platform import SeedError, apply_seeds

pytestmark = pytest.mark.integration

SCHEMA = "semis_tx"
TABLE = f"{SCHEMA}.tb_note"


@pytest.fixture
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        connection.execute(f"CREATE SCHEMA {SCHEMA}; CREATE TABLE {TABLE} (n INT CHECK (n > 0))")
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


def _seed(tmp_path: Path, name: str, value: int) -> Path:
    path = tmp_path / name
    path.write_text(f"INSERT INTO {TABLE} VALUES ({value});\n")
    return path


def _count(database: str) -> int:
    with psycopg.connect(database) as connection:
        (count,) = connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone() or (None,)
    assert isinstance(count, int)
    return count


def test_a_url_commits(database: str, tmp_path: Path) -> None:
    apply_seeds(database, [_seed(tmp_path, "one.sql", 1)])
    assert _count(database) == 1


def test_a_connection_does_not_commit(database: str, tmp_path: Path) -> None:
    with psycopg.connect(database) as connection:
        apply_seeds(connection, [_seed(tmp_path, "one.sql", 1)])
        (inside,) = connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone() or (None,)
        outside = _count(database)
        connection.rollback()
    assert (inside, outside, _count(database)) == (1, 0, 0)


def test_a_failed_file_leaves_the_callers_transaction_open(database: str, tmp_path: Path) -> None:
    """The failed file's savepoint is undone; the rest waits for the caller's rollback."""
    with psycopg.connect(database) as connection:
        apply_seeds(connection, [_seed(tmp_path, "one.sql", 1)])
        with pytest.raises(SeedError, match="check constraint"):
            apply_seeds(connection, [_seed(tmp_path, "bad.sql", -1)])
        (inside,) = connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone() or (None,)
        connection.rollback()
    assert (inside, _count(database)) == (1, 0)
