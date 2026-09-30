"""Prep-seed level 3 judges a key with no ``REFERENCES`` by what its resolver joins.

A key into a partitioned table cannot declare ``REFERENCES``: the parent's unique keys
include its partition key, which the child does not carry. Named for its role
(``fk_origin`` into ``tb_sample``), such a key is resolved when its resolver matches some
table's ``id`` to ``fk_origin_id`` (confiture#530), and is still an ERROR when it does not.
"""

from pathlib import Path

from confiture.platform import PrepSeedPattern, ViolationSeverity, validate_seeds

TABLES = """
CREATE SCHEMA catalog;
CREATE SCHEMA prep_seed;
CREATE TABLE catalog.tb_sample (
    pk_sample BIGINT GENERATED ALWAYS AS IDENTITY,
    id UUID NOT NULL,
    taken_on DATE NOT NULL,
    PRIMARY KEY (pk_sample, taken_on)
) PARTITION BY RANGE (taken_on);
CREATE TABLE catalog.tb_event (
    pk_event BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    fk_origin BIGINT
);
CREATE TABLE prep_seed.tb_sample (id UUID NOT NULL, taken_on DATE NOT NULL);
CREATE TABLE prep_seed.tb_event (id UUID NOT NULL, fk_origin_id UUID);
"""
MAPPING = """
CREATE FUNCTION catalog.fn_resolve_tb_event() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO catalog.tb_event (id, fk_origin)
    SELECT s.id, (SELECT pk_sample FROM catalog.tb_sample WHERE id = s.fk_origin_id)
    FROM prep_seed.tb_event s;
END $$;
"""
DROPPING = """
CREATE FUNCTION catalog.fn_resolve_tb_event() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO catalog.tb_event (id, fk_origin) SELECT s.id, NULL FROM prep_seed.tb_event s;
END $$;
"""


def _violations(tmp_path: Path, resolver: str) -> list[tuple[object, ...]]:
    schema_dir, seeds_dir = tmp_path / "schema", tmp_path / "seeds"
    schema_dir.mkdir()
    seeds_dir.mkdir()
    (schema_dir / "010_tables.sql").write_text(TABLES)
    (schema_dir / "020_resolvers.sql").write_text(resolver)
    (seeds_dir / "001_event.sql").write_text(
        "INSERT INTO prep_seed.tb_event (id, fk_origin_id) VALUES (NULL, NULL);\n"
    )
    report = validate_seeds(seeds_dir, schema_dir=schema_dir, max_level=3)
    return [
        (violation.pattern, violation.severity, violation.message)
        for violation in report.violations
    ]


def test_an_unreferenced_key_its_resolver_maps_is_resolved(tmp_path: Path) -> None:
    assert _violations(tmp_path, MAPPING) == []


def test_an_unreferenced_key_its_resolver_drops_is_an_error(tmp_path: Path) -> None:
    assert _violations(tmp_path, DROPPING) == [
        (
            PrepSeedPattern.MISSING_FK_TRANSFORMATION,
            ViolationSeverity.ERROR,
            "fn_resolve_tb_event fills catalog.tb_event but never joins tb_origin on "
            "fk_origin_id: fk_origin is not resolved",
        )
    ]
