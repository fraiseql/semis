"""What promotion does with a prep-seed child's pinned parent UUID (ARCHITECTURE D41).

A fresh pin promotes every child onto the parent read-back would have chosen. A stale
one — a reference row whose UUID changed after it was pinned — is measured, not
guessed: what the project's resolver writes, and what confiture's level 5 reports,
depends on its join. An inner join drops the child, and level 5 reports nothing; a left
join writes ``NULL``, which level 5 reports, or fails on a NOT NULL key.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import LiteralString

import psycopg
import pytest
from confiture.platform import validate_seeds

from fraiseql_semis import ScenarioManager, SchemaFacts, TableCodes, seeds
from fraiseql_semis.staging import Staging

pytestmark = pytest.mark.integration

CATALOG, PREP = "semis_promote_catalog", "semis_promote_prep"
CATEGORY, CUSTOMER = f"{CATALOG}.tb_category", f"{CATALOG}.tb_customer"
BOOKS, MUSIC = "00000000-0000-4000-8000-0000000000b0", "00000000-0000-4000-8000-0000000000c0"


def _tables(key: LiteralString) -> LiteralString:
    """The catalog and its staging twin, the customer's key to its category *key*."""
    return f"""
CREATE SCHEMA {CATALOG};
CREATE SCHEMA {PREP};
CREATE TABLE {CATEGORY} (
    pk_category BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE
);
CREATE TABLE {CUSTOMER} (
    pk_customer BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_category BIGINT {key} REFERENCES {CATEGORY} (pk_category)
);
CREATE TABLE {PREP}.tb_customer (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    fk_category_id UUID
);
"""


def _resolver(join: LiteralString) -> LiteralString:
    """A project's resolver, promoting each staged customer by its category's UUID."""
    return f"""
CREATE FUNCTION {CATALOG}.fn_resolve_tb_customer() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO {CUSTOMER} (id, identifier, fk_category)
    SELECT s.id, s.identifier, k.pk_category FROM {PREP}.tb_customer AS s
    {join} JOIN {CATEGORY} AS k ON k.id = s.fk_category_id;
END $$;
"""


ROWS = f"INSERT INTO {CATEGORY} (id, identifier) VALUES ('{BOOKS}', 'books'), ('{MUSIC}', 'music');"
SCENARIO = f"""\
scenario_id: 0x5007
name: customers
mode: prep-seed
seed: 42
tables:
  - name: {CUSTOMER}
    count: 4
existing:
  - name: {CATEGORY}
"""


class _Project:
    """The schema, a scenario of four customers over the existing categories, and the
    seeds its pin gives."""

    def __init__(self, url: str, tmp_path: Path, key: LiteralString, join: LiteralString) -> None:
        self.url, self.tmp_path = url, tmp_path
        self.ddl = _tables(key) + ROWS
        self.resolver = _resolver(join)
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA IF EXISTS {CATALOG}, {PREP} CASCADE")
            admin.execute(self.ddl + self.resolver)
        self.manager = ScenarioManager(
            SchemaFacts.from_source(self.ddl, table_codes=TableCodes({CUSTOMER: 0x0C0D0E0F})),
            staging=Staging(PREP),
        )
        self.path = tmp_path / "customers.yaml"
        self.path.write_text(SCENARIO)
        with psycopg.connect(url) as connection:
            self.manager.pin(
                self.manager.load(self.path, read_pin=False), self.path, connection=connection
            )
            connection.rollback()
        run = self.manager.execute(self.manager.load(self.path), tmp_path / "seeds")
        self.seeds = [seed.path for seed in run.seeds]

    def promoted(self) -> list[tuple[str, str | None]]:
        """Each customer the resolver promotes, by identifier, with its category's, in a
        transaction rolled back."""
        with psycopg.connect(self.url) as connection:
            seeds.apply(connection, self.seeds)
            connection.execute(f"SELECT {CATALOG}.fn_resolve_tb_customer()")
            rows = connection.execute(
                f"SELECT c.identifier, k.identifier FROM {CUSTOMER} AS c "
                f"LEFT JOIN {CATEGORY} AS k ON k.pk_category = c.fk_category ORDER BY c.id"
            ).fetchall()
            connection.rollback()
        return rows

    def level_5(self) -> list[tuple[str, str]]:
        """What confiture's five levels report on the seeds, by severity and pattern."""
        schema_dir = self.tmp_path / "schema"
        schema_dir.mkdir(exist_ok=True)
        (schema_dir / "010_tables.sql").write_text(self.ddl)
        (schema_dir / "020_fn_resolve_tb_customer.sql").write_text(self.resolver)
        report = validate_seeds(
            self.tmp_path / "seeds",
            schema_dir=schema_dir,
            max_level=5,
            database=self.url,
            prep_seed_schema=PREP,
            catalog_schema=CATALOG,
        )
        return sorted(
            (violation.severity.name, violation.pattern.name) for violation in report.violations
        )

    def rebuild_with_a_new_uuid_for_books(self) -> None:
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(
                f"UPDATE {CATEGORY} SET id = gen_random_uuid() WHERE identifier = 'books'"
            )


@pytest.fixture
def project(
    database_url: str, tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[_Project]:
    key, join = request.param
    yield _Project(database_url, tmp_path, key, join)
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {CATALOG}, {PREP} CASCADE")


@pytest.mark.parametrize("project", [("NOT NULL", "INNER")], indirect=True)
def test_a_fresh_pin_promotes_each_child_onto_read_backs_parent(project: _Project) -> None:
    """Round-robin over the categories in pk_* order, as read-back would point them."""
    assert [category for _, category in project.promoted()] == ["books", "music"] * 2
    assert project.level_5() == []


@pytest.mark.parametrize(
    ("project", "promoted", "reported"),
    [
        (("NOT NULL", "INNER"), ["music", "music"], []),
        (("", "LEFT"), [None, "music", None, "music"], [("CRITICAL", "NULL_FK_AFTER_RESOLUTION")]),
        (("NOT NULL", "LEFT"), None, [("ERROR", "MISSING_FK_TRANSFORMATION")]),
    ],
    ids=["inner-join", "left-join-nullable", "left-join-not-null"],
    indirect=["project"],
)
def test_a_stale_uuid_is_dropped_nulled_or_refused_by_the_resolvers_join(
    project: _Project, promoted: list[str | None] | None, reported: list[tuple[str, str]]
) -> None:
    """After the pin, books takes a new UUID: the seeds still carry the old one."""
    project.rebuild_with_a_new_uuid_for_books()
    if promoted is None:
        with pytest.raises(psycopg.errors.NotNullViolation):
            project.promoted()
    else:
        assert [category for _, category in project.promoted()] == promoted
    assert project.level_5() == reported
