"""Prep-seed level 3 reads a foreign key's parent from its ``REFERENCES``.

A key named after a role rather than its table (an owner, a parent, a generic model) is
judged against the table it references: a resolver joining ``tb_person`` on
``fk_owner_id`` resolves ``fk_owner``, and one that leaves it unmapped is reported
against ``tb_person``, not a ``tb_owner`` no schema declares. Before confiture#498
(1.25), both were reported against ``tb_owner``.
"""

from pathlib import Path

from confiture.platform import PrepSeedPattern, ViolationSeverity, validate_seeds

TABLES = """
CREATE SCHEMA catalog;
CREATE SCHEMA prep_seed;
CREATE TABLE catalog.tb_person (
    pk_person BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE catalog.tb_widget (
    pk_widget BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    fk_owner BIGINT REFERENCES catalog.tb_person (pk_person)
);
CREATE TABLE prep_seed.tb_person (id UUID NOT NULL, name TEXT NOT NULL);
CREATE TABLE prep_seed.tb_widget (id UUID NOT NULL, fk_owner_id UUID);
"""
RESOLVERS = """
CREATE FUNCTION catalog.fn_resolve_tb_person() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO catalog.tb_person (id, name) SELECT id, name FROM prep_seed.tb_person;
END $$;
CREATE FUNCTION catalog.fn_resolve_tb_widget() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO catalog.tb_widget (id, fk_owner)
    SELECT w.id, p.pk_person
    FROM prep_seed.tb_widget w LEFT JOIN catalog.tb_person p ON p.id = w.fk_owner_id;
END $$;
"""


UNRESOLVED = RESOLVERS.replace(
    "SELECT w.id, p.pk_person\n"
    "    FROM prep_seed.tb_widget w LEFT JOIN catalog.tb_person p"
    " ON p.id = w.fk_owner_id",
    "SELECT w.id, NULL\n    FROM prep_seed.tb_widget w",
)


def _level_3(
    tmp_path: Path, resolvers: str
) -> list[tuple[PrepSeedPattern, ViolationSeverity, str]]:
    schema_dir, seeds_dir = tmp_path / "schema", tmp_path / "seeds"
    schema_dir.mkdir()
    seeds_dir.mkdir()
    (schema_dir / "010_tables.sql").write_text(TABLES)
    (schema_dir / "020_resolvers.sql").write_text(resolvers)
    (seeds_dir / "001_widget.sql").write_text(
        "INSERT INTO prep_seed.tb_widget (id, fk_owner_id) VALUES (NULL, NULL);\n"
    )
    report = validate_seeds(seeds_dir, schema_dir=schema_dir, max_level=3)
    return [
        (violation.pattern, violation.severity, violation.message)
        for violation in report.violations
    ]


def test_a_key_named_for_its_role_is_resolved_by_joining_its_reference(
    tmp_path: Path,
) -> None:
    assert _level_3(tmp_path, RESOLVERS) == []


def test_an_unmapped_key_is_reported_against_the_table_it_references(
    tmp_path: Path,
) -> None:
    assert UNRESOLVED != RESOLVERS
    assert _level_3(tmp_path, UNRESOLVED) == [
        (
            PrepSeedPattern.MISSING_FK_TRANSFORMATION,
            ViolationSeverity.ERROR,
            "fn_resolve_tb_widget fills catalog.tb_widget but never joins tb_person on "
            "fk_owner_id: fk_owner is not resolved",
        )
    ]
