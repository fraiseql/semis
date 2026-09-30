"""Prep-seed levels 2 to 5 find each staging table's final table from its resolver (#458).

A tree whose resolvers fill tables in several schemas — shared reference data in one,
per-tenant tables in another — is judged table by table, so semis passes no
``catalog_schema`` unless a project names one.
"""

from pathlib import Path

from confiture.platform import validate_seeds

TREE = """
CREATE SCHEMA catalog;
CREATE SCHEMA tenant;
CREATE SCHEMA prep_seed;
CREATE TABLE catalog.tb_color (
    pk_color BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE tenant.tb_widget (
    pk_widget BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    fk_color BIGINT REFERENCES catalog.tb_color (pk_color)
);
CREATE TABLE prep_seed.tb_color (id UUID NOT NULL, name TEXT NOT NULL);
CREATE TABLE prep_seed.tb_widget (id UUID NOT NULL, fk_color_id UUID);

CREATE FUNCTION catalog.fn_resolve_tb_color() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO catalog.tb_color (id, name) SELECT id, name FROM prep_seed.tb_color;
END $$;
CREATE FUNCTION tenant.fn_resolve_tb_widget() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO tenant.tb_widget (id, fk_color)
    SELECT w.id, c.pk_color
    FROM prep_seed.tb_widget w LEFT JOIN catalog.tb_color c ON c.id = w.fk_color_id;
END $$;
"""


def test_a_table_resolved_outside_catalog_is_judged_where_it_lands(tmp_path: Path) -> None:
    schema_dir, seeds_dir = tmp_path / "schema", tmp_path / "seeds"
    schema_dir.mkdir()
    seeds_dir.mkdir()
    (schema_dir / "010_tree.sql").write_text(TREE)
    (seeds_dir / "001_widget.sql").write_text(
        "INSERT INTO prep_seed.tb_widget (id, fk_color_id) VALUES "
        "('00000000-0000-0000-0000-000000000001', NULL);\n"
    )
    report = validate_seeds(seeds_dir, schema_dir=schema_dir, max_level=3)
    assert [violation.message for violation in report.violations] == []
