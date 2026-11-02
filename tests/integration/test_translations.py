"""Translations applied: each category's label per declared locale, its locale key the
locale's row, read by its code."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import LiteralString

import psycopg
import pytest
from confiture.platform import validate_seeds

from fraiseql_semis import ScenarioManager, SchemaFacts, TableCodes, seeds
from fraiseql_semis.errors import AlreadyAppliedError, ResetBlockedError, ScenarioError
from fraiseql_semis.staging import Staging
from fraiseql_semis.translation import TranslationRule
from tests.ddl import TRANSLATED, TRANSLATED_CODES

SCHEMA = "semis_tl"
DDL = (
    TRANSLATED.replace("CREATE SCHEMA catalog;", f"CREATE SCHEMA {SCHEMA};")
    .replace("catalog.", f"{SCHEMA}.")
    .split("CREATE SCHEMA prep_seed;")[0]
)
LOCALE, CATEGORY, TL = f"{SCHEMA}.tb_locale", f"{SCHEMA}.tb_category", f"{SCHEMA}.tl_category"
# A table whose key points at the locale table as at any existing parent.
SHOP = f"""
CREATE TABLE {SCHEMA}.tb_shop (
    pk_shop BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_locale BIGINT NOT NULL REFERENCES {LOCALE} (pk_locale)
);
"""
LOCALES: LiteralString = f"""
INSERT INTO {LOCALE} (id, identifier, code) VALUES
    (gen_random_uuid(), 'fr', 'fr-FR'), (gen_random_uuid(), 'gb', 'en-GB'),
    (gen_random_uuid(), 'de', 'de-DE')
"""
CODES = {
    name.replace("catalog.", f"{SCHEMA}."): code for name, code in TRANSLATED_CODES.items()
} | {f"{SCHEMA}.tb_shop": 0x10111213}
SCENARIO = f"""\
scenario_id: 0x5008
name: shop
mode: read-back
seed: 42
locales: [fr-FR, en-GB]
tables:
  - name: {CATEGORY}
    count: 20
    translations: {TL}
"""


def _facts(ddl: str) -> SchemaFacts:
    return SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES))


@pytest.fixture
def connection(database_url: str) -> Iterator[psycopg.Connection]:
    """The schema, built inside a transaction the test's end rolls back."""
    with psycopg.connect(database_url) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        connection.execute(DDL + SHOP)
        connection.execute(LOCALES)
        yield connection
        connection.rollback()


def _apply(
    connection: psycopg.Connection, tmp_path: Path, text: str = SCENARIO, ddl: str = DDL + SHOP
) -> None:
    path = tmp_path / "shop.yaml"
    path.write_text(text)
    manager = ScenarioManager(_facts(ddl))
    manager.apply(manager.load(path), connection=connection)


def _one(connection: psycopg.Connection, query: LiteralString) -> object:
    (value,) = connection.execute(query).fetchone() or (None,)
    return value


def test_each_category_gets_one_label_per_declared_locale(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    _apply(connection, tmp_path)
    assert connection.execute(
        f"SELECT count(*), count(DISTINCT (fk_category, fk_locale)) FROM {TL}"
    ).fetchone() == (40, 40)
    assert (
        _one(
            connection,
            f"SELECT string_agg(DISTINCT l.code, ',' ORDER BY l.code) FROM {TL} AS t "
            f"JOIN {LOCALE} AS l ON l.pk_locale = t.fk_locale",
        )
        == "en-GB,fr-FR"
    )


def test_a_declared_locale_the_table_lacks_is_refused_before_anything_is_written(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    with pytest.raises(ScenarioError) as refused:
        _apply(connection, tmp_path, SCENARIO.replace("en-GB]", "en-GB, it-IT]"))
    assert str(refused.value).splitlines() == [
        f"scenario shop: locale it-IT has no row in {LOCALE} (code)",
        "Hint: Insert its row, or drop it from locales:.",
    ]
    assert _one(connection, f"SELECT count(*) FROM {CATEGORY}") == 0


def test_a_second_apply_of_translations_with_an_id_is_refused(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    with_id = DDL.replace(
        f"CREATE TABLE {TL} (\n", f"CREATE TABLE {TL} (\n    id UUID NOT NULL UNIQUE,\n"
    )
    connection.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
    connection.execute(with_id + SHOP)
    connection.execute(LOCALES)
    _apply(connection, tmp_path, ddl=with_id + SHOP)
    path = tmp_path / "shop.yaml"
    manager = ScenarioManager(_facts(with_id + SHOP))
    with pytest.raises(AlreadyAppliedError, match=r"^scenario shop is already applied"):
        manager.apply(manager.load(path), connection=connection)


def test_a_locale_table_under_existing_keeps_its_existing_children_apart(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    """Shops point at the where: row; translations at the declared codes."""
    text = SCENARIO + (
        f"  - name: {SCHEMA}.tb_shop\n    count: 3\n"
        f"existing:\n  - name: {LOCALE}\n    where: {{identifier: [de]}}\n"
    )
    _apply(connection, tmp_path, text)
    assert (
        _one(
            connection,
            f"SELECT string_agg(DISTINCT l.code, ',') FROM {SCHEMA}.tb_shop AS s "
            f"JOIN {LOCALE} AS l ON l.pk_locale = s.fk_locale",
        )
        == "de-DE"
    )
    assert _one(connection, f"SELECT count(*) FROM {TL}") == 40


def test_an_empty_locale_table_is_refused_naming_the_first_code(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    connection.execute(f"DELETE FROM {LOCALE}")
    with pytest.raises(
        ScenarioError, match=rf"^scenario shop: locale fr-FR has no row in {LOCALE} \(code\)"
    ):
        _apply(connection, tmp_path)


def test_a_code_two_locale_rows_share_is_refused(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    connection.execute(f"ALTER TABLE {LOCALE} DROP CONSTRAINT tb_locale_code_key")
    connection.execute(
        f"INSERT INTO {LOCALE} (id, identifier, code) VALUES (gen_random_uuid(), 'fr2', 'fr-FR')"
    )
    with pytest.raises(
        ScenarioError, match=rf"^scenario shop: locale fr-FR has 2 rows in {LOCALE} \(code\)"
    ):
        _apply(connection, tmp_path)


PREP = f"{SCHEMA}_prep"
PREP_DDL = TRANSLATED.split("CREATE SCHEMA prep_seed;")[1].replace("prep_seed.", f"{PREP}.")
RESOLVER: LiteralString = f"""
CREATE FUNCTION {SCHEMA}.fn_resolve_tl_category() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO {TL} (fk_category, fk_locale, label)
    SELECT c.pk_category, l.pk_locale, s.label FROM {PREP}.tl_category AS s
    JOIN {CATEGORY} AS c ON c.id = s.fk_category_id
    JOIN {LOCALE} AS l ON l.id = s.fk_locale_id;
END $$;
"""
PREP_SCENARIO = SCENARIO.replace("read-back", "prep-seed").replace("count: 20", "count: 3")


@pytest.fixture
def staged(database_url: str) -> Iterator[str]:
    """The schema and its staging twins, committed, for a pin and a level 5 to read."""
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {PREP} CASCADE")
        admin.execute(DDL + f"CREATE SCHEMA {PREP};" + PREP_DDL + RESOLVER)
        admin.execute(LOCALES)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}, {PREP} CASCADE")


def _prep_manager() -> ScenarioManager:
    return ScenarioManager(_facts(DDL + f"CREATE SCHEMA {PREP};" + PREP_DDL), staging=Staging(PREP))


def _pinned(database: str, tmp_path: Path) -> tuple[ScenarioManager, Path]:
    path = tmp_path / "shop.yaml"
    path.write_text(PREP_SCENARIO)
    manager = _prep_manager()
    with psycopg.connect(database) as connection:
        manager.pin(manager.load(path, read_pin=False), path, connection=connection)
        connection.rollback()
    return manager, path


def test_the_pin_records_each_declared_locales_uuid(staged: str, tmp_path: Path) -> None:
    _, path = _pinned(staged, tmp_path)
    (read,) = json.loads((tmp_path / "shop.pin.json").read_text())["rows"]["reads"]
    with psycopg.connect(staged) as connection:
        ids = dict(connection.execute(f"SELECT code, id::text FROM {LOCALE}").fetchall())
    assert read == {
        "table": LOCALE,
        "by": "code",
        "where": ["fr-FR", "en-GB"],
        "rows": [["fr-FR", ids["fr-FR"]], ["en-GB", ids["en-GB"]]],
    }
    assert path.exists()


def test_pinning_an_empty_locale_table_is_refused(staged: str, tmp_path: Path) -> None:
    with psycopg.connect(staged, autocommit=True) as admin:
        admin.execute(f"DELETE FROM {LOCALE}")
    with pytest.raises(
        ScenarioError, match=rf"^scenario shop: locale fr-FR has no row in {LOCALE} \(code\)"
    ):
        _pinned(staged, tmp_path)


def test_promoted_translations_point_at_their_locales_rows(staged: str, tmp_path: Path) -> None:
    """The staged rows carry the pinned UUIDs; the project's resolver turns each into its
    locale's pk_locale, and confiture's static levels find nothing to report."""
    manager, path = _pinned(staged, tmp_path)
    run = manager.execute(manager.load(path), tmp_path / "seeds")
    with psycopg.connect(staged) as connection:
        connection.execute(f"SET search_path TO {SCHEMA}, public")
        seeds.apply(connection, [seed.path for seed in run.seeds])
        connection.execute(
            f"INSERT INTO {CATEGORY} (id, identifier, name) "
            f"SELECT id, identifier, name FROM {PREP}.tb_category"
        )
        connection.execute(f"SELECT {SCHEMA}.fn_resolve_tl_category()")
        pointed = connection.execute(
            f"SELECT l.code, count(*) FROM {TL} AS t JOIN {LOCALE} AS l "
            "ON l.pk_locale = t.fk_locale GROUP BY l.code ORDER BY l.code"
        ).fetchall()
        connection.rollback()
    assert pointed == [("en-GB", 3), ("fr-FR", 3)]
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    (schema_dir / "010_tables.sql").write_text(DDL + f"CREATE SCHEMA {PREP};" + PREP_DDL)
    (schema_dir / "020_fn_resolve_tl_category.sql").write_text(RESOLVER)
    report = validate_seeds(
        tmp_path / "seeds",
        schema_dir=schema_dir,
        max_level=3,
        prep_seed_schema=PREP,
        catalog_schema=SCHEMA,
    )
    assert [
        v.severity.name for v in report.violations if v.severity.name in {"CRITICAL", "ERROR"}
    ] == []


def test_reset_deletes_the_scenarios_translations_and_keeps_another_scenarios(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    """A translation table without a uuid id is the scenario's through its entity key: in
    the final table, its rows whose category is one of the scenario's."""
    _apply(connection, tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _apply(
        connection, other, SCENARIO.replace("0x5008", "0x5009").replace("name: shop", "name: other")
    )
    manager = ScenarioManager(_facts(DDL + SHOP))
    path = tmp_path / "shop.yaml"
    deleted = manager.reset(manager.load(path), connection=connection)
    assert [(table.table, table.rows) for table in deleted][:2] == [(TL, 40), (CATEGORY, 20)]
    assert connection.execute(f"SELECT count(*) FROM {TL}").fetchone() == (40,)
    assert connection.execute(f"SELECT count(*) FROM {CATEGORY}").fetchone() == (20,)


def test_a_prep_seed_reset_clears_the_translation_twin_and_the_promoted_rows(
    staged: str, tmp_path: Path
) -> None:
    manager, path = _pinned(staged, tmp_path)
    scenario = manager.load(path)
    with psycopg.connect(staged) as connection:
        manager.apply(scenario, connection=connection)
        connection.execute(
            f"INSERT INTO {CATEGORY} (id, identifier, name) "
            f"SELECT id, identifier, name FROM {PREP}.tb_category"
        )
        connection.execute(f"SELECT {SCHEMA}.fn_resolve_tl_category()")
        deleted = {
            table.table: table.rows for table in manager.reset(scenario, connection=connection)
        }
        left = connection.execute(
            f"SELECT (SELECT count(*) FROM {TL}), (SELECT count(*) FROM {PREP}.tl_category)"
        ).fetchone()
        connection.rollback()
    assert (deleted[TL], deleted[f"{PREP}.tl_category"], left) == (6, 6, (0, 0))


def test_a_row_outside_the_run_pointing_at_translations_blocks_the_reset(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    """Translation rows are found through their entity, and still guarded: a key into
    them from outside the run refuses the reset, whatever its action."""
    _apply(connection, tmp_path)
    connection.execute(
        f"CREATE TABLE {SCHEMA}.tb_quote (fk_category BIGINT, fk_locale BIGINT, "
        f"CONSTRAINT quote_label FOREIGN KEY (fk_category, fk_locale) "
        f"REFERENCES {TL} (fk_category, fk_locale) ON DELETE CASCADE); "
        f"INSERT INTO {SCHEMA}.tb_quote SELECT fk_category, fk_locale FROM {TL} LIMIT 1"
    )
    manager = ScenarioManager(_facts(DDL + SHOP))
    with pytest.raises(
        ResetBlockedError,
        match=rf"^scenario shop: 1 row of {SCHEMA}\.tb_quote points at its rows of {TL}, by "
        "quote_label",
    ):
        manager.reset(manager.load(tmp_path / "shop.yaml"), connection=connection)
    assert connection.execute(f"SELECT count(*) FROM {TL}").fetchone() == (40,)


def test_translations_pointing_at_another_run_table_reset_as_the_scenarios(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    """A translation row's keys into the run's tables are the scenario's own, through its
    entity: they never block the reset of their own rows."""
    with_shop = (
        DDL + SHOP + f"ALTER TABLE {TL} ADD COLUMN fk_shop BIGINT REFERENCES {SCHEMA}.tb_shop;\n"
    )
    connection.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
    connection.execute(with_shop)
    connection.execute(LOCALES)
    text = (
        SCENARIO
        + f"  - name: {SCHEMA}.tb_shop\n    count: 2\n"
        + (f"existing:\n  - name: {LOCALE}\n")
    )
    _apply(connection, tmp_path, text, ddl=with_shop)
    assert connection.execute(f"SELECT count(fk_shop) FROM {TL}").fetchone() == (40,)
    manager = ScenarioManager(_facts(with_shop))
    deleted = manager.reset(manager.load(tmp_path / "shop.yaml"), connection=connection)
    assert {table.table: table.rows for table in deleted}[TL] == 40


LANGUAGES = (
    DDL.replace("tb_locale", "tb_language")
    .replace("pk_locale", "pk_language")
    .replace("    code TEXT NOT NULL UNIQUE\n", "    iso TEXT NOT NULL UNIQUE\n")
    .replace("fk_locale BIGINT", "fk_language BIGINT")
    .replace("UNIQUE (fk_category, fk_locale)", "UNIQUE (fk_category, fk_language)")
    .replace("    label VARCHAR(80) NOT NULL,", "    name VARCHAR(80) NOT NULL,")
)
RULE = TranslationRule(
    label_column="name", locale_fk="fk_language", locale_table=(f"{SCHEMA}.tb_language", "iso")
)


def test_a_project_rule_names_another_translation_tables_columns(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    connection.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
    connection.execute(LANGUAGES)
    connection.execute(
        f"INSERT INTO {SCHEMA}.tb_language (id, identifier, iso) VALUES "
        "(gen_random_uuid(), 'fr', 'fr-FR'), (gen_random_uuid(), 'gb', 'en-GB')"
    )
    path = tmp_path / "shop.yaml"
    path.write_text(SCENARIO.replace(f"translations: {TL}", "translations: true"))
    manager = ScenarioManager(_facts(LANGUAGES), translations=RULE)
    manager.apply(manager.load(path), connection=connection)
    assert connection.execute(
        f"SELECT count(*), count(DISTINCT (fk_category, fk_language)), count(name) FROM {TL}"
    ).fetchone() == (40, 40, 40)


def test_a_locale_table_the_locale_key_does_not_point_at_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "shop.yaml"
    path.write_text(SCENARIO.replace(f"translations: {TL}", "translations: true"))
    rule = TranslationRule(
        label_column="name", locale_fk="fk_language", locale_table=("public.tb_locale", "code")
    )
    manager = ScenarioManager(_facts(LANGUAGES), translations=rule)
    with pytest.raises(
        ScenarioError,
        match=rf"^scenario shop: {TL}\.fk_language points at {SCHEMA}\.tb_language, not "
        r"semis\.yaml's locale_table public\.tb_locale",
    ):
        manager.check(manager.load(path), no_pin=True)


def test_a_table_named_under_translations_takes_the_rules_columns(tmp_path: Path) -> None:
    """The name overrides the template; the columns are still the rule's."""
    path = tmp_path / "shop.yaml"
    path.write_text(SCENARIO)
    renamed = TranslationRule(
        table="{schema}.{entity}_labels",
        label_column="name",
        locale_fk="fk_language",
        locale_table=(f"{SCHEMA}.tb_language", "iso"),
    )
    manager = ScenarioManager(_facts(LANGUAGES), translations=renamed)
    assert manager.check(manager.load(path), no_pin=True)[0].startswith("scenario shop")


def test_a_pseudo_locale_writes_one_more_label_per_category(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    connection.execute(
        f"INSERT INTO {LOCALE} (id, identifier, code) VALUES (gen_random_uuid(), 'qps', 'qps-ploc')"
    )
    _apply(connection, tmp_path, SCENARIO.replace("en-GB]\n", "en-GB]\npseudo_locale: qps-ploc\n"))
    labels = connection.execute(
        f"SELECT t.label FROM {TL} AS t JOIN {LOCALE} AS l ON l.pk_locale = t.fk_locale "
        "WHERE l.code = 'qps-ploc'"
    ).fetchall()
    assert len(labels) == 20
    assert all(label.startswith("[") and label.endswith("]") for (label,) in labels)


def test_a_pseudo_locale_the_locale_table_lacks_is_refused(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    with pytest.raises(
        ScenarioError, match=rf"^scenario shop: locale qps-ploc has no row in {LOCALE} \(code\)"
    ):
        _apply(
            connection, tmp_path, SCENARIO.replace("en-GB]\n", "en-GB]\npseudo_locale: qps-ploc\n")
        )


def test_coverage_writes_its_share_of_a_locales_labels(
    connection: psycopg.Connection, tmp_path: Path
) -> None:
    connection.execute(
        f"INSERT INTO {LOCALE} (id, identifier, code) VALUES (gen_random_uuid(), 'qps', 'qps-ploc')"
    )
    _apply(
        connection,
        tmp_path,
        SCENARIO.replace(
            "locales: [fr-FR, en-GB]\n",
            "locales: [fr-FR, en-GB, de-DE]\npseudo_locale: qps-ploc\n"
            "coverage: {en-GB: 0.8, de-DE: 0}\n",
        ),
    )
    counts = connection.execute(
        f"SELECT l.code, count(t.label) FROM {LOCALE} AS l "
        f"LEFT JOIN {TL} AS t ON t.fk_locale = l.pk_locale GROUP BY l.code ORDER BY l.code"
    ).fetchall()
    assert counts == [("de-DE", 0), ("en-GB", 16), ("fr-FR", 20), ("qps-ploc", 20)]
