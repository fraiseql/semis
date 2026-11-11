"""Translation rows: one per entity row and declared locale, drawn in that locale."""

from fractions import Fraction
from typing import Any
from uuid import UUID

import pytest
from faker import Faker

from fraiseql_semis import faker_provider
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ResolutionError, RowContractError
from fraiseql_semis.faker_provider import CustomProviderRegistry
from fraiseql_semis.generator import FakeDataGenerator, Row, TablePlan
from fraiseql_semis.resolution import PrepSeedResolver
from fraiseql_semis.schema import SchemaFacts
from fraiseql_semis.translation import (
    Locale,
    Translation,
    TranslationTable,
    kept,
    pseudo,
    seed_for,
)
from fraiseql_semis.uuid_generator import SemanticUUIDGenerator, scenario_bounds
from tests.ddl import TRANSLATED, TRANSLATED_CODES

FR, GB = "00000000-0000-4000-8000-0000000000f1", "00000000-0000-4000-8000-0000000000f2"
LOCALES = (Locale("fr-FR", "fr_FR"), Locale("en-GB", "en_GB"))
CATEGORY, TL = "catalog.tb_category", "catalog.tl_category"
# A table drawn after the translation table, by name, among the entity's children.
AFTER = (
    TRANSLATED
    + """
CREATE TABLE catalog.tz_note (
    pk_note BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_category BIGINT NOT NULL REFERENCES catalog.tb_category (pk_category),
    body TEXT NOT NULL
);
"""
)
CODES = TRANSLATED_CODES | {"catalog.tz_note": 0x0F101112}


def _id(row: Row) -> UUID:
    """*row*'s ``id``, which the generator draws as a UUID."""
    value = row["id"]
    assert isinstance(value, UUID)
    return value


def _walk(
    ddl: str = TRANSLATED,
    *,
    locales: tuple[Locale, ...] | None = LOCALES,
    counts: dict[str, int] | None = None,
) -> dict[str, list[Row]]:
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES))
    resolver = PrepSeedResolver()
    resolver.locales("catalog.tb_locale", {"fr-FR": FR, "en-GB": GB})
    translation = (
        None if locales is None else Translation(TranslationTable.of(facts, CATEGORY, TL), locales)
    )
    tables = {
        table: TablePlan(count, translation=translation if table == CATEGORY else None)
        for table, count in (counts or {CATEGORY: 3}).items()
    }
    generator = FakeDataGenerator(facts, 0x5008, seed=42)
    run: dict[str, list[Row]] = {}
    for table, stream in generator.walk(tables, resolver=resolver):
        rows = list(stream)
        resolver.remember(table, rows)
        run[table.ref.display] = rows
    return run


def test_each_entity_row_gets_one_row_per_locale_after_the_entity() -> None:
    run = _walk()
    assert list(run) == [CATEGORY, TL]
    categories = [row["id"] for row in run[CATEGORY]]
    assert [(row["fk_category"], row["fk_locale"]) for row in run[TL]] == [
        (category, locale) for category in categories for locale in (FR, GB)
    ]


def test_each_locale_draws_its_labels_from_its_own_seeded_faker() -> None:
    labels = [row["label"] for row in _walk()[TL]]
    for offset, locale in enumerate(LOCALES):
        faker = Faker(locale.faker)
        faker.seed_instance(seed_for(42, TL, locale.code))
        assert labels[offset::2] == [faker.word() for _ in range(3)]


def test_two_runs_draw_the_same_translations() -> None:
    assert _walk()[TL] == _walk()[TL]


@pytest.mark.parametrize(
    "ddl",
    [
        AFTER,
        AFTER.replace(
            "    label VARCHAR(80) NOT NULL,",
            "    identifier TEXT NOT NULL UNIQUE,\n    note TEXT NOT NULL,\n    label VARCHAR(80) NOT NULL,",
        ),
    ],
    ids=["label", "identifier-and-not-null-text"],
)
def test_locales_leave_every_other_table_byte_identical(ddl: str) -> None:
    """The translations draw from their own Fakers: the scenario's draws the same values."""
    counts = {CATEGORY: 3, "catalog.tz_note": 3}
    translated = _walk(ddl, counts=counts)
    plain = _walk(ddl, locales=None, counts=counts)
    assert (translated[CATEGORY], translated["catalog.tz_note"]) == (
        plain[CATEGORY],
        plain["catalog.tz_note"],
    )


def test_a_unique_label_two_locales_draw_alike_is_redrawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """One store of used values spans the locales, so the second draw is redrawn rather
    than refused by the row contract."""
    monkeypatch.setattr("fraiseql_semis.generator.seed_for", lambda *_: 7)
    unique = TRANSLATED.replace("label VARCHAR(80) NOT NULL,", "label VARCHAR(80) NOT NULL UNIQUE,")
    same = (Locale("fr-FR", "fr_FR"), Locale("en-GB", "fr_FR"))
    labels = [row["label"] for row in _walk(unique, locales=same)[TL]]
    assert len(set(labels)) == len(labels) == 6


def test_a_translation_rows_uuid_is_its_entitys_sequence_times_256_plus_its_slot() -> None:
    with_id = TRANSLATED.replace(
        "CREATE TABLE catalog.tl_category (\n",
        "CREATE TABLE catalog.tl_category (\n    id UUID NOT NULL UNIQUE,\n",
    )
    rows = _walk(with_id, counts={CATEGORY: 7})[TL]
    seventh_in_english = rows[6 * 2 + 1]
    fields = SemanticUUIDGenerator.decode(_id(seventh_in_english))
    assert fields.sequence == 7 * 256 + 2
    low, high = scenario_bounds(CODES[TL], 0x5008)
    assert all(low <= _id(row) <= high for row in rows)


def test_a_run_makes_one_faker_per_declared_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not one per translation table and locale: a Faker takes tens of milliseconds."""
    entities = [f"catalog.tb_e{n}" for n in range(3)]
    ddl = "CREATE SCHEMA catalog;\nCREATE TABLE catalog.tb_locale (pk_locale BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, id UUID NOT NULL UNIQUE, code TEXT NOT NULL UNIQUE);\n"
    for entity in entities:
        name = entity.split(".")[1][3:]
        ddl += (
            f"CREATE TABLE {entity} (pk_{name} BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            f"id UUID NOT NULL UNIQUE);\n"
            f"CREATE TABLE catalog.tl_{name} (fk_{name} BIGINT NOT NULL REFERENCES {entity}, "
            "fk_locale BIGINT NOT NULL REFERENCES catalog.tb_locale, label TEXT NOT NULL);\n"
        )
    codes = {
        table: 0x100 + n
        for n, table in enumerate(entities + [t.replace("tb_", "tl_") for t in entities])
    }
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes(codes))
    made: list[Any] = []
    real = faker_provider.Faker

    def counted(*args: Any, **kwargs: Any) -> Any:
        made.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(faker_provider, "Faker", counted)
    resolver = PrepSeedResolver()
    resolver.locales("catalog.tb_locale", {"fr-FR": FR, "en-GB": GB})
    generator = FakeDataGenerator(facts, 0x5008, seed=42)
    tables = {
        entity: TablePlan(
            2,
            translation=Translation(
                TranslationTable.of(facts, entity, entity.replace("tb_", "tl_")), LOCALES
            ),
        )
        for entity in entities
    }
    for table, stream in generator.walk(tables, resolver=resolver):
        resolver.remember(table, list(stream))
    assert len(made) == 3


def test_a_locale_the_resolver_was_not_given_is_refused() -> None:
    resolver = PrepSeedResolver()
    resolver.locales("catalog.tb_locale", {"fr-FR": FR})
    with pytest.raises(ResolutionError, match=r"^catalog\.tb_locale holds no locale en-GB"):
        resolver.locale_key("catalog.tb_locale", "en-GB")


def test_an_unseeded_run_seeds_no_locale() -> None:
    """Without seed:, a run is not reproducible, and neither are its translations."""
    assert seed_for(None, TL, "fr-FR") is None
    assert seed_for(42, TL, "fr-FR") != seed_for(42, TL, "en-GB")


def test_appending_a_locale_changes_no_existing_translations_uuid() -> None:
    with_id = TRANSLATED.replace(
        "CREATE TABLE catalog.tl_category (\n",
        "CREATE TABLE catalog.tl_category (\n    id UUID NOT NULL UNIQUE,\n",
    )
    french = [row["id"] for row in _walk(with_id, locales=LOCALES[:1])[TL]]
    both = [row["id"] for row in _walk(with_id)[TL]]
    assert both[::2] == french


QPS = "00000000-0000-4000-8000-0000000000f9"


def _pseudo_walk(
    ddl: str = TRANSLATED,
    *,
    pseudo: str | None = "qps-ploc",
    providers: CustomProviderRegistry | None = None,
) -> dict[str, list[Row]]:
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES))
    resolver = PrepSeedResolver()
    resolver.locales("catalog.tb_locale", {"fr-FR": FR, "en-GB": GB, "qps-ploc": QPS})
    translation = Translation(TranslationTable.of(facts, CATEGORY, TL), LOCALES, pseudo)
    run: dict[str, list[Row]] = {}
    for table, stream in FakeDataGenerator(facts, 0x5008, seed=42, providers=providers).walk(
        {CATEGORY: TablePlan(3, translation=translation)}, resolver=resolver
    ):
        run[table.ref.display] = rows = list(stream)
        resolver.remember(table, rows)
    return run


WITH_ID = TRANSLATED.replace(
    "CREATE TABLE catalog.tl_category (\n",
    "CREATE TABLE catalog.tl_category (\n    id UUID NOT NULL UNIQUE,\n"
    "    identifier TEXT NOT NULL UNIQUE,\n    note TEXT NOT NULL,\n",
)


def test_each_entity_gets_a_pseudo_row_last_its_label_the_default_ones_transformed() -> None:
    rows = _pseudo_walk(WITH_ID)[TL]
    assert [row["fk_locale"] for row in rows] == [FR, GB, QPS] * 3
    for default, pseudo_row in zip(rows[0::3], rows[2::3], strict=True):
        assert pseudo_row["label"] == pseudo(str(default["label"]), length=80)
        assert pseudo_row["note"] == default["note"]
        assert pseudo_row["fk_category"] == default["fk_category"]


def test_a_pseudo_row_is_at_slot_255_and_moves_no_other_uuid() -> None:
    with_pseudo, without = _pseudo_walk(WITH_ID)[TL], _pseudo_walk(WITH_ID, pseudo=None)[TL]
    sequences = [SemanticUUIDGenerator.decode(_id(row)).sequence for row in with_pseudo]
    assert sequences[2::3] == [sequence * 256 + 255 for sequence in (1, 2, 3)]
    assert [row["id"] for k, row in enumerate(with_pseudo) if k % 3 != 2] == [
        row["id"] for row in without
    ]
    assert [row for k, row in enumerate(with_pseudo) if k % 3 != 2] == without


def test_a_unique_column_other_than_the_label_is_drawn_afresh() -> None:
    unique = WITH_ID.replace("    note TEXT NOT NULL,\n", "    note TEXT NOT NULL UNIQUE,\n")
    rows = _pseudo_walk(unique)[TL]
    assert all(
        pseudo_row["note"] != default["note"]
        for default, pseudo_row in zip(rows[0::3], rows[2::3], strict=True)
    )


def test_two_labels_cut_to_one_pseudo_label_under_a_unique_key_are_refused() -> None:
    """A pseudo label has no randomness to redraw with: the run refuses, naming both."""
    labels = iter(["Saint-Eta", "Lyon", "Saint-Etb", "Nice", "Brest", "Metz"])
    registry = CustomProviderRegistry()
    registry.register_column(TL, "label", lambda _faker, _column: next(labels))
    short = TRANSLATED.replace("label VARCHAR(80) NOT NULL,", "label VARCHAR(10) NOT NULL UNIQUE,")
    with pytest.raises(RowContractError) as refused:
        _pseudo_walk(short, providers=registry)
    assert str(refused.value).splitlines()[0] == (
        f"{TL}.label: the pseudo labels of 'Saint-Eta' and 'Saint-Etb' are both "
        f"'{pseudo('Saint-Eta', length=10)}'"
    )


def _covered_walk(coverage: dict[str, Fraction], *, seed: int = 42) -> list[Row]:
    facts = SchemaFacts.from_source(WITH_ID, table_codes=TableCodes(CODES))
    resolver = PrepSeedResolver()
    resolver.locales("catalog.tb_locale", {"fr-FR": FR, "en-GB": GB, "qps-ploc": QPS})
    translation = Translation(
        TranslationTable.of(facts, CATEGORY, TL), LOCALES, "qps-ploc", coverage
    )
    rows: list[Row] = []
    for table, stream in FakeDataGenerator(facts, 0x5008, seed=seed).walk(
        {CATEGORY: TablePlan(20, translation=translation)}, resolver=resolver
    ):
        drawn = list(stream)
        resolver.remember(table, drawn)
        rows += drawn if table.ref.display == TL else []
    return rows


def test_coverage_leaves_out_the_rows_kept_does_not_keep() -> None:
    rows = _covered_walk({"en-GB": Fraction("0.8")})
    english = [row for row in rows if row["fk_locale"] == GB]
    sequences = {SemanticUUIDGenerator.decode(_id(row)).sequence // 256 for row in english}
    assert len(english) == 16
    assert sequences == kept(range(1, 21), Fraction("0.8"), scenario=0x5008, table=TL, code="en-GB")
    assert len([row for row in rows if row["fk_locale"] == FR]) == 20


def test_coverage_changes_no_surviving_rows_label_or_uuid() -> None:
    full = {row["id"]: row for row in _covered_walk({})}
    for row in _covered_walk({"en-GB": Fraction("0.5"), "fr-FR": Fraction("0.3")}):
        assert full[row["id"]] == row


def test_a_pseudo_row_keeps_its_source_when_the_default_row_is_left_out() -> None:
    rows = _covered_walk({"fr-FR": Fraction(0)})
    assert [row for row in rows if row["fk_locale"] == FR] == []
    assert len([row for row in rows if row["fk_locale"] == QPS]) == 20


def test_the_gaps_do_not_follow_the_seed() -> None:
    def gaps(seed: int) -> set[object]:
        return {
            SemanticUUIDGenerator.decode(_id(row)).sequence
            for row in _covered_walk({"en-GB": Fraction("0.6")}, seed=seed)
        }

    assert gaps(1) == gaps(2)


def test_the_pseudo_locale_takes_its_own_coverage() -> None:
    rows = _covered_walk({"qps-ploc": Fraction("0.25")})
    assert len([row for row in rows if row["fk_locale"] == QPS]) == 5
