"""Prep-seed level 5 runs what level 1 reads (confiture#385, #386).

A resolver is found by the routine's name, so a numbered file —
``019201004_fn_resolve_…`` — is run; a seed in a subdirectory is loaded.
"""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from confiture.platform import PrepSeedPattern, validate_seeds

pytestmark = pytest.mark.integration

CATALOG, PREP = "semis_l5_catalog", "semis_l5_prep"
TABLES = f"""
CREATE SCHEMA {CATALOG};
CREATE SCHEMA {PREP};
CREATE TABLE {CATALOG}.tb_widget (
    pk_widget BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE {PREP}.tb_widget (id UUID NOT NULL, name TEXT NOT NULL);
"""
# Fills a table that does not exist, so running it is a finding.
RESOLVER = f"""
CREATE FUNCTION {CATALOG}.fn_resolve_tb_widget() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO {CATALOG}.tb_gone (id, name) SELECT id, name FROM {PREP}.tb_widget;
END $$;
"""
WIDGET = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {CATALOG}, {PREP} CASCADE")
        connection.execute(TABLES + RESOLVER)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"DROP SCHEMA {CATALOG}, {PREP} CASCADE")


def _level_5(database: str, tmp_path: Path, seed: Path) -> list[tuple[PrepSeedPattern, str]]:
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    (schema_dir / "010_tables.sql").write_text(TABLES)
    (schema_dir / "019201001_fn_resolve_tb_widget.sql").write_text(RESOLVER)
    report = validate_seeds(
        tmp_path / "seeds",
        schema_dir=schema_dir,
        max_level=5,
        database=database,
        prep_seed_schema=PREP,
        catalog_schema=CATALOG,
    )
    assert [Path(path) for path in report.scanned_files] == [seed]
    return [(violation.pattern, Path(violation.file_path).name) for violation in report.violations]


def _seed(tmp_path: Path, relative: str, name: str) -> Path:
    path = tmp_path / "seeds" / relative
    path.parent.mkdir(parents=True)
    path.write_text(f"INSERT INTO {PREP}.tb_widget (id, name) VALUES ('{WIDGET}', {name});\n")
    return path


def test_a_numbered_resolver_file_is_run(database: str, tmp_path: Path) -> None:
    seed = _seed(tmp_path, "001_widget.sql", "'w'")
    assert (
        PrepSeedPattern.MISSING_FK_TRANSFORMATION,
        "019201001_fn_resolve_tb_widget.sql",
    ) in _level_5(database, tmp_path, seed)


def test_a_nested_seed_is_loaded(database: str, tmp_path: Path) -> None:
    seed = _seed(tmp_path, "sub/001_widget.sql", "NULL")
    assert any(name == "001_widget.sql" for _, name in _level_5(database, tmp_path, seed))
