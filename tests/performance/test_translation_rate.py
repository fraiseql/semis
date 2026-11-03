"""Translations at scale: 20 translated tables in 10 locales, at the PRD's rate.

A run makes one Faker per declared locale, not one per (table, locale), so the tables'
count costs rows, not Faker construction. Timed untraced, as the generation rate is.
"""

import time
from typing import Any

import pytest
from faker import Faker

from fraiseql_semis import faker_provider
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.generator import FakeDataGenerator, TablePlan
from fraiseql_semis.resolution import PrepSeedResolver
from fraiseql_semis.schema import SchemaFacts
from fraiseql_semis.translation import Locale, Translation, TranslationTable, faker_locale
from tests.performance.test_generation_rate import _floor, untraced

TABLES = 20
CODES = ("fr-FR", "en-GB", "de-DE", "es-ES", "it-IT", "nl-NL", "pt-BR", "pl-PL", "sv-SE", "ja-JP")
ROWS = 100  # per table: enough rows that the ten Fakers' construction is not the measure
ENTITIES = [f"catalog.tb_thing{n}" for n in range(TABLES)]


def _facts() -> SchemaFacts:
    ddl = (
        "CREATE SCHEMA catalog;\nCREATE TABLE catalog.tb_locale (pk_locale BIGINT GENERATED "
        "ALWAYS AS IDENTITY PRIMARY KEY, id UUID NOT NULL UNIQUE, code TEXT NOT NULL UNIQUE);\n"
    )
    for entity in ENTITIES:
        name = entity.split(".")[1][3:]
        ddl += (
            f"CREATE TABLE {entity} (pk_{name} BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY "
            f"KEY, id UUID NOT NULL UNIQUE, name TEXT NOT NULL);\n"
            f"CREATE TABLE catalog.tl_{name} (fk_{name} BIGINT NOT NULL REFERENCES {entity}, "
            "fk_locale BIGINT NOT NULL REFERENCES catalog.tb_locale, label TEXT NOT NULL, "
            "description TEXT NOT NULL);\n"
        )
    tables = ENTITIES + [entity.replace("tb_", "tl_") for entity in ENTITIES]
    return SchemaFacts.from_source(
        ddl, table_codes=TableCodes({table: 0x200 + n for n, table in enumerate(tables)})
    )


@untraced
def test_twenty_translated_tables_in_ten_locales_draw_at_the_prds_rate() -> None:
    facts = _facts()
    locales = tuple(Locale(code, faker_locale(code) or "en_US") for code in CODES)
    resolver = PrepSeedResolver()
    resolver.locales("catalog.tb_locale", {code: f"key-{code}" for code in CODES})
    generator = FakeDataGenerator(facts, 0x5009, seed=42)
    plans = {
        entity: TablePlan(
            ROWS,
            translation=Translation(
                TranslationTable.of(facts, entity, entity.replace("tb_", "tl_")), locales
            ),
        )
        for entity in ENTITIES
    }
    drawn = 0
    started = time.perf_counter()
    for table, stream in generator.walk(plans, resolver=resolver):
        rows = list(stream)
        resolver.remember(table, rows)
        drawn += len(rows)
    rate = drawn / (time.perf_counter() - started)
    assert drawn == TABLES * ROWS * (1 + len(CODES))
    assert rate >= _floor(), f"{rate:,.0f} rows/s drawn, below the floor of {_floor():,}"


def test_a_run_makes_one_faker_per_declared_locale_and_one_for_its_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[object] = []

    def counted(*args: Any, **kwargs: Any) -> Faker:
        made.append(args)
        return Faker(*args, **kwargs)

    monkeypatch.setattr(faker_provider, "Faker", counted)
    facts = _facts()
    locales = tuple(Locale(code, faker_locale(code) or "en_US") for code in CODES)
    resolver = PrepSeedResolver()
    resolver.locales("catalog.tb_locale", {code: f"key-{code}" for code in CODES})
    generator = FakeDataGenerator(facts, 0x5009, seed=42)
    plans = {
        entity: TablePlan(
            2,
            translation=Translation(
                TranslationTable.of(facts, entity, entity.replace("tb_", "tl_")), locales
            ),
        )
        for entity in ENTITIES
    }
    for table, stream in generator.walk(plans, resolver=resolver):
        resolver.remember(table, list(stream))
    assert len(made) == len(CODES) + 1
