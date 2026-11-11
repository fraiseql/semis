"""locales: and translations: — a scenario's declared locales, and the tables translated."""

import json
import re
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import cast

import psycopg
import pytest
from faker.config import AVAILABLE_LOCALES

from fraiseql_semis import pin_file
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.pin import PinnedRead, SchemaPin
from fraiseql_semis.scenario import Scenario, ScenarioManager, TableSpec
from fraiseql_semis.schema import SchemaFacts
from fraiseql_semis.translation import Locale, TranslationRule, faker_locale
from tests.ddl import CODES, TRANSLATED, TRANSLATED_CODES, TRINITY

FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
HEADER = "scenario_id: 0x5008\nname: shop\nmode: read-back\nseed: 42\n"
TRANSLATED_TABLE = """\
tables:
  - name: catalog.tb_continent
    count: 2
    translations: catalog.tl_continent
"""


def _load(tmp_path: Path, text: str) -> Scenario:
    path = tmp_path / "shop.yaml"
    path.write_text(text)
    return ScenarioManager(FACTS).load(path)


def test_locales_and_translations_load(tmp_path: Path) -> None:
    scenario = _load(
        tmp_path,
        HEADER + "locales: [fr-FR, en-gb, {code: fr, faker: fr_CA}, en]\n" + TRANSLATED_TABLE,
    )
    assert scenario.locales == (
        Locale("fr-FR", "fr_FR"),
        Locale("en-gb", "en_GB"),
        Locale("fr", "fr_CA"),
        Locale("en", "en"),
    )
    (spec,) = scenario.tables
    assert spec.translations == "catalog.tl_continent"


@pytest.mark.parametrize(
    ("code", "faker"),
    [("fr_FR", "fr_FR"), ("fr-FR", "fr_FR"), ("fr-fr", "fr_FR"), ("de", "de"), ("de-AT", "de_AT")],
)
def test_a_code_names_its_faker_locale(code: str, faker: str) -> None:
    assert faker_locale(code) == faker


@pytest.mark.parametrize("code", ["fr", "it", "xx-YY", "klingon"])
def test_a_code_faker_has_no_locale_for_is_none(code: str) -> None:
    assert faker_locale(code) is None


# Faker's French locales, as the installed release has them (fr_DZ came in a later one).
FRENCH = ", ".join(sorted(locale for locale in AVAILABLE_LOCALES if locale.startswith("fr_")))
LOCALES_HINT = "Hint: List the locale table's codes, e.g. locales: [fr-FR, en-GB]."


@pytest.mark.parametrize(
    ("locales", "lines"),
    [
        (
            "locales: [fr]\n",
            [
                "scenario shop: locales: fr has no Faker locale",
                f"Hint: Faker has {FRENCH}: write {{code: fr, faker: fr_FR}}.",
            ],
        ),
        (
            "locales: [{code: fr, faker: fr_XX}]\n",
            [
                "scenario shop: locales: fr maps to faker fr_XX, which is not one of Faker's "
                "locales",
                "Hint: Name one of Faker's locales, e.g. fr_FR.",
            ],
        ),
        (
            "locales: [fr-FR, en-GB, fr-FR]\n",
            ["scenario shop: locales: lists fr-FR twice", "Hint: List each locale code once."],
        ),
        (
            "locales: fr-FR\n",
            [
                "scenario shop: locales: is a list of locale codes, e.g. [fr-FR, en-GB]",
                LOCALES_HINT,
            ],
        ),
        (
            "locales: []\n",
            [
                "scenario shop: locales: is a list of locale codes, e.g. [fr-FR, en-GB]",
                LOCALES_HINT,
            ],
        ),
        (
            "locales: [3]\n",
            [
                "scenario shop: locales: is a list of locale codes, e.g. [fr-FR, en-GB]",
                LOCALES_HINT,
            ],
        ),
        (
            "locales: [{code: 3, faker: fr_FR}]\n",
            [
                "scenario shop: locales: is a list of locale codes, e.g. [fr-FR, en-GB]",
                LOCALES_HINT,
            ],
        ),
        (
            "locales: [{code: fr}]\n",
            [
                "scenario shop: locales: is a list of locale codes, e.g. [fr-FR, en-GB]",
                LOCALES_HINT,
            ],
        ),
        (
            "locales: [" + ", ".join(f"en-x{n}" for n in range(300)) + "]\n",
            [
                "scenario shop: locales: holds 300 locales; a scenario writes at most 254",
                "Hint: Translate into at most 254 locales in one scenario.",
            ],
        ),
    ],
    ids=[
        "no-faker",
        "bad-faker",
        "twice",
        "string",
        "empty",
        "number",
        "code-number",
        "no-faker-key",
        "300",
    ],
)
def test_malformed_locales_are_refused(tmp_path: Path, locales: str, lines: list[str]) -> None:
    with pytest.raises(ScenarioError) as refused:
        _load(tmp_path, HEADER + locales + TRANSLATED_TABLE)
    assert str(refused.value).splitlines() == lines


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (
            HEADER + TRANSLATED_TABLE,
            r"^scenario shop: catalog\.tb_continent has translations:, and the scenario "
            r"declares no locales:",
        ),
        (
            HEADER + "locales: [fr-FR]\ntables:\n  - name: catalog.tb_continent\n    count: 2\n",
            r"^scenario shop declares locales:, and no table has translations:",
        ),
        (
            HEADER
            + "locales: [fr-FR]\n"
            + TRANSLATED_TABLE
            + "  - name: catalog.tl_continent\n    count: 2\n",
            r"^scenario shop: catalog\.tl_continent is written by catalog\.tb_continent's "
            r"translations: and listed under tables:",
        ),
        (
            HEADER
            + "locales: [fr-FR]\n"
            + TRANSLATED_TABLE
            + "existing:\n  - name: catalog.tl_continent\n",
            r"^scenario shop: catalog\.tl_continent is written by catalog\.tb_continent's "
            r"translations: and listed under existing:",
        ),
        (
            HEADER
            + "locales: [fr-FR]\n"
            + TRANSLATED_TABLE.replace("catalog.tl_continent", "tl_continent"),
            r"^scenario shop: catalog\.tb_continent's translations: names tl_continent, which "
            r"is not schema-qualified",
        ),
    ],
    ids=["no-locales", "no-translations", "under-tables", "under-existing", "unqualified"],
)
def test_translations_the_scenario_contradicts_are_refused(
    tmp_path: Path, text: str, match: str
) -> None:
    with pytest.raises(ScenarioError, match=match):
        _load(tmp_path, text)


TRANSLATED_SCENARIO = """\
scenario_id: 0x5008
name: shop
mode: read-back
seed: 42
locales: [fr-FR, en-GB]
tables:
  - name: catalog.tb_category
    count: 3
    translations: catalog.tl_category
"""


def _check(tmp_path: Path, ddl: str, text: str = TRANSLATED_SCENARIO) -> tuple[str, ...]:
    path = tmp_path / "shop.yaml"
    path.write_text(text)
    manager = ScenarioManager(
        SchemaFacts.from_source(ddl, table_codes=TableCodes(TRANSLATED_CODES))
    )
    return manager.check(manager.load(path), no_pin=True)


def test_a_translated_scenario_checks(tmp_path: Path) -> None:
    assert _check(tmp_path, TRANSLATED)[0].startswith("scenario shop")


@pytest.mark.parametrize(
    ("ddl", "text", "match"),
    [
        (
            TRANSLATED,
            TRANSLATED_SCENARIO.replace("catalog.tl_category", "catalog.tl_gone"),
            r"catalog\.tb_category's translations: names catalog\.tl_gone, which is not in the "
            r"schema",
        ),
        (
            TRANSLATED.replace(
                "    fk_locale BIGINT NOT NULL REFERENCES catalog.tb_locale (pk_locale),\n", ""
            ),
            TRANSLATED_SCENARIO,
            r"catalog\.tl_category has no fk_locale column, which a translation table needs",
        ),
        (
            TRANSLATED.replace("    label VARCHAR(80) NOT NULL,\n", ""),
            TRANSLATED_SCENARIO,
            r"catalog\.tl_category has no label column, which a translation table needs",
        ),
        (
            TRANSLATED.replace(
                "fk_locale BIGINT NOT NULL REFERENCES catalog.tb_locale (pk_locale)",
                "fk_locale BIGINT NOT NULL",
            ),
            TRANSLATED_SCENARIO,
            r"catalog\.tl_category\.fk_locale declares no foreign key, so semis cannot point it "
            r"at a locale row",
        ),
        (
            TRANSLATED.replace(
                "fk_category BIGINT NOT NULL REFERENCES catalog.tb_category (pk_category),",
                "fk_category BIGINT NOT NULL,",
            ),
            TRANSLATED_SCENARIO,
            r"catalog\.tl_category has no foreign key to catalog\.tb_category",
        ),
        (
            TRANSLATED.replace(
                "    label VARCHAR(80) NOT NULL,",
                "    fk_parent BIGINT REFERENCES catalog.tb_category (pk_category),\n"
                "    label VARCHAR(80) NOT NULL,",
            ),
            TRANSLATED_SCENARIO,
            r"catalog\.tl_category has two foreign keys to catalog\.tb_category \(fk_category, "
            r"fk_parent\): one is the entity's",
        ),
        (
            TRANSLATED.replace("    code TEXT NOT NULL UNIQUE\n", "    label TEXT\n"),
            TRANSLATED_SCENARIO,
            r"catalog\.tb_locale has no code column to find a locale by",
        ),
        (
            TRANSLATED.replace(
                "pk_locale BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,", "note TEXT,"
            ).replace(
                "REFERENCES catalog.tb_locale (pk_locale)", "REFERENCES catalog.tb_locale (id)"
            ),
            TRANSLATED_SCENARIO,
            r"catalog\.tb_locale shows no surrogate key to point a locale key at",
        ),
        (
            TRANSLATED.replace(
                "    id UUID NOT NULL UNIQUE,\n    identifier TEXT NOT NULL UNIQUE,\n    code",
                "    id TEXT NOT NULL UNIQUE,\n    identifier TEXT NOT NULL UNIQUE,\n    code",
            ),
            TRANSLATED_SCENARIO.replace("read-back", "prep-seed"),
            r"locale table catalog\.tb_locale has no uuid id for a prep-seed child to carry",
        ),
    ],
    ids=[
        "not-in-schema",
        "no-fk-locale",
        "no-label",
        "fk-locale-not-a-key",
        "no-entity-key",
        "two-entity-keys",
        "no-code",
        "read-back-no-surrogate-key",
        "prep-seed-no-uuid",
    ],
)
def test_a_translation_table_semis_cannot_write_is_refused(
    tmp_path: Path, ddl: str, text: str, match: str
) -> None:
    with pytest.raises(ScenarioError, match=rf"^scenario shop: {match}"):
        _check(tmp_path, ddl, text)


@pytest.mark.parametrize("command", ["pin", "reset"])
def test_pin_and_reset_name_the_scenario_in_a_translation_refusal(
    tmp_path: Path, command: str
) -> None:
    """They reach the translation tables before any database, as check does."""
    path = tmp_path / "shop.yaml"
    path.write_text(TRANSLATED_SCENARIO.replace("catalog.tl_category", "catalog.tl_gone"))
    manager = ScenarioManager(
        SchemaFacts.from_source(TRANSLATED, table_codes=TableCodes(TRANSLATED_CODES))
    )
    scenario = manager.load(path)
    with pytest.raises(ScenarioError, match=r"^scenario shop: catalog\.tb_category's translations"):
        if command == "pin":
            manager.pin(scenario, path, check=True)
        else:
            manager.reset(scenario, connection=cast("psycopg.Connection", None))


FR_UUID, GB_UUID = "00000000-0000-4000-8000-0000000000f1", "00000000-0000-4000-8000-0000000000f2"
LOCALE_READ = PinnedRead(
    "catalog.tb_locale", "code", ("fr-FR", "en-GB"), (("fr-FR", FR_UUID), ("en-GB", GB_UUID))
)


def _prep_seed(tmp_path: Path, rows: object) -> tuple[ScenarioManager, Path]:
    path = tmp_path / "shop.yaml"
    path.write_text(TRANSLATED_SCENARIO.replace("read-back", "prep-seed"))
    facts = SchemaFacts.from_source(TRANSLATED, table_codes=TableCodes(TRANSLATED_CODES))
    manager = ScenarioManager(facts)
    taken = SchemaPin.of(
        facts,
        ["catalog.tb_category", "catalog.tl_category"],
        twins=["prep_seed.tb_category", "prep_seed.tl_category"],
        existing=["catalog.tb_locale"],
        coded={"catalog.tb_locale": "code"},
    )
    pin_file.write(pin_file.path_for(path, "shop"), replace(taken, rows=rows))
    return manager, path


def test_prep_seed_translations_carry_their_locales_pinned_uuids(tmp_path: Path) -> None:
    manager, path = _prep_seed(tmp_path, (LOCALE_READ,))
    run = manager.execute(manager.load(path), tmp_path / "out")
    staged = next(
        seed for seed in run.seeds if seed.path.name.endswith("prep_seed.tl_category.sql")
    )
    lines = [
        line for line in staged.path.read_text().splitlines() if line.lstrip().startswith("('")
    ]
    assert [FR_UUID in line for line in lines] == [True, False] * 3
    assert [GB_UUID in line for line in lines] == [False, True] * 3


def test_a_prep_seed_locale_its_pin_lacks_is_refused(tmp_path: Path) -> None:
    lacking = replace(LOCALE_READ, where=("fr-FR",), rows=(("fr-FR", FR_UUID),))
    manager, path = _prep_seed(tmp_path, (lacking,))
    with pytest.raises(ScenarioError) as refused:
        manager.check(manager.load(path))
    assert str(refused.value).splitlines()[0] == (
        "scenario shop: prep-seed points at catalog.tb_locale's rows by UUID, and its pin "
        "records no row whose code is 'en-GB'"
    )


FIXTURE = Path(__file__).parent.parent / "fixtures" / "countries-0.3.0.pin.json"


def test_a_pin_without_translations_projects_as_0_3_0_did() -> None:
    """countries-0.3.0.pin.json was written by semis 0.3.0, for a read-back scenario of
    countries over existing continents: this release reads the same facts and digest."""
    kept = SchemaPin.from_document(json.loads(FIXTURE.read_text()), scenario="countries")
    now = SchemaPin.of(FACTS, ["catalog.tb_country"], existing=["catalog.tb_continent"])
    assert (now.digest, now.recorded) == (kept.digest, kept.recorded)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("    code TEXT NOT NULL UNIQUE\n", "    locale_code TEXT NOT NULL UNIQUE\n"),
        ("    label VARCHAR(80) NOT NULL,\n", "    title VARCHAR(80) NOT NULL,\n"),
    ],
    ids=["code", "label"],
)
def test_renaming_the_locale_code_or_the_label_moves_the_pin(old: str, new: str) -> None:
    def pin(ddl: str) -> str:
        facts = SchemaFacts.from_source(ddl, table_codes=TableCodes(TRANSLATED_CODES))
        return SchemaPin.of(
            facts,
            ["catalog.tb_category", "catalog.tl_category"],
            coded={"catalog.tb_locale": "code"},
        ).digest

    assert pin(TRANSLATED.replace(old, new)) != pin(TRANSLATED)


def test_a_prep_seed_run_with_translations_is_byte_identical(tmp_path: Path) -> None:
    manager, path = _prep_seed(tmp_path, (LOCALE_READ,))
    first = manager.execute(manager.load(path), tmp_path / "a")
    second = manager.execute(manager.load(path), tmp_path / "b")
    assert [seed.path.read_bytes() for seed in first.seeds] == [
        seed.path.read_bytes() for seed in second.seeds
    ]


@pytest.mark.parametrize(
    ("template", "entity", "table"),
    [
        ("{schema}.tl_{entity}", "catalog.tb_category", "catalog.tl_category"),
        ("tl_{entity}", "catalog.tb_category", "catalog.tl_category"),
        ("i18n.{entity}_labels", "catalog.tb_category", "i18n.category_labels"),
        ("{schema}.tl_{entity}", "catalog.category", "catalog.tl_category"),
    ],
    ids=["default", "no-schema", "other-schema", "no-tb-prefix"],
)
def test_the_rule_names_an_entitys_translation_table(
    template: str, entity: str, table: str
) -> None:
    assert TranslationRule(table=template).table_for(entity) == table


RULED = TRANSLATED_SCENARIO.replace("translations: catalog.tl_category", "translations: true")


def _ruled(
    tmp_path: Path, text: str = RULED, rule: TranslationRule | None = None
) -> tuple[str, ...]:
    path = tmp_path / "shop.yaml"
    path.write_text(text)
    facts = SchemaFacts.from_source(TRANSLATED, table_codes=TableCodes(TRANSLATED_CODES))
    manager = ScenarioManager(facts, translations=rule)
    return manager.check(manager.load(path), no_pin=True)


def test_translations_true_takes_the_rules_table(tmp_path: Path) -> None:
    assert _ruled(tmp_path)[0].startswith("scenario shop")
    assert _ruled(tmp_path, rule=TranslationRule(table="tl_{entity}"))[0].startswith(
        "scenario shop"
    )


def test_a_rule_naming_a_table_the_schema_lacks_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError) as refused:
        _ruled(tmp_path, rule=TranslationRule(table="{schema}.{entity}_i18n"))
    assert str(refused.value).splitlines() == [
        "scenario shop: catalog.tb_category's translations, by semis.yaml's rule "
        "'{schema}.{entity}_i18n', are catalog.category_i18n, which the schema does not hold",
        "Hint: Name the table under translations:, or change the rule in semis.yaml.",
    ]


@pytest.mark.parametrize("value", ["false", "3", "[catalog.tl_category]"])
def test_translations_neither_true_nor_a_table_is_refused(tmp_path: Path, value: str) -> None:
    with pytest.raises(
        ScenarioError,
        match=r"^scenario shop: catalog\.tb_category: translations: is true or a "
        r"schema-qualified table",
    ):
        _ruled(tmp_path, RULED.replace("translations: true", f"translations: {value}"))


def test_the_rules_table_listed_under_tables_too_is_refused(tmp_path: Path) -> None:
    with pytest.raises(
        ScenarioError,
        match=r"^scenario shop: catalog\.tl_category is written by catalog\.tb_category's "
        r"translations: and listed under tables:",
    ):
        _ruled(tmp_path, RULED + "  - name: catalog.tl_category\n    count: 1\n")


def test_a_pseudo_locale_loads(tmp_path: Path) -> None:
    scenario = _load(
        tmp_path, HEADER + "locales: [fr-FR]\npseudo_locale: qps-ploc\n" + TRANSLATED_TABLE
    )
    assert scenario.pseudo_locale == "qps-ploc"


@pytest.mark.parametrize(
    ("keys", "lines"),
    [
        (
            "pseudo_locale: qps-ploc\n",
            [
                "scenario shop: pseudo_locale: transforms the first locale's labels, and the "
                "scenario declares no locales:",
                "Hint: Declare the locales first; the pseudo-locale transforms the first one's labels.",
            ],
        ),
        (
            "locales: [fr-FR, qps-ploc]\npseudo_locale: qps-ploc\n",
            [
                "scenario shop: qps-ploc is under both locales: and pseudo_locale:",
                "Hint: List the pseudo-locale under pseudo_locale: alone.",
            ],
        ),
        (
            "locales: [fr-FR]\npseudo_locale: ''\n",
            [
                "scenario shop: pseudo_locale: is one locale code, e.g. qps-ploc",
                "Hint: Name the locale table's code for the pseudo-locale.",
            ],
        ),
        (
            "locales: [fr-FR]\npseudo_locale: [qps-ploc]\n",
            [
                "scenario shop: pseudo_locale: is one locale code, e.g. qps-ploc",
                "Hint: Name the locale table's code for the pseudo-locale.",
            ],
        ),
    ],
    ids=["no-locales", "both", "empty", "list"],
)
def test_a_pseudo_locale_the_scenario_contradicts_is_refused(
    tmp_path: Path, keys: str, lines: list[str]
) -> None:
    with pytest.raises(ScenarioError) as refused:
        _load(tmp_path, HEADER + keys + TRANSLATED_TABLE)
    assert str(refused.value).splitlines() == lines


PSEUDO = TRANSLATED_SCENARIO.replace(
    "locales: [fr-FR, en-GB]\n", "locales: [fr-FR, en-GB]\npseudo_locale: qps-ploc\n"
)


def test_a_label_too_short_for_the_brackets_is_refused(tmp_path: Path) -> None:
    short = TRANSLATED.replace("label VARCHAR(80) NOT NULL", "label VARCHAR(2) NOT NULL")
    with pytest.raises(
        ScenarioError,
        match=r"^scenario shop: catalog\.tl_category\.label is varchar\(2\), too short for a "
        r"bracketed pseudo label",
    ):
        _check(tmp_path, short, PSEUDO)


def test_a_short_column_that_is_not_the_label_is_not_judged(tmp_path: Path) -> None:
    short = TRANSLATED.replace(
        "    label VARCHAR(80) NOT NULL,", "    label VARCHAR(80) NOT NULL,\n    flag CHAR(1),"
    )
    assert _check(tmp_path, short, PSEUDO)[0].startswith("scenario shop")


def test_a_scenario_built_in_python_with_its_pseudo_locale_among_its_locales_is_refused() -> None:
    with pytest.raises(
        ScenarioError, match=r"^scenario shop: qps-ploc is under both locales: and pseudo_locale:"
    ):
        Scenario(
            id=0x5008,
            name="shop",
            mode="read-back",
            tables=(TableSpec("catalog.tb_category", 1, translations="catalog.tl_category"),),
            locales=(Locale("fr-FR", "fr_FR"), Locale("qps-ploc", "en_US")),
            pseudo_locale="qps-ploc",
        )


COVERED = HEADER + "locales: [fr-FR, en-GB]\npseudo_locale: qps-ploc\n"


def test_coverage_loads_as_exact_fractions(tmp_path: Path) -> None:
    scenario = _load(
        tmp_path, COVERED + "coverage: {en-GB: 0.8, fr-FR: 0, qps-ploc: 1}\n" + TRANSLATED_TABLE
    )
    assert scenario.coverage == {
        "en-GB": Fraction(4, 5),
        "fr-FR": Fraction(0),
        "qps-ploc": Fraction(1),
    }


@pytest.mark.parametrize(
    ("coverage", "said"),
    [
        ("[en-GB]", "coverage: maps a locale to a fraction, e.g. {en-GB: 0.8}"),
        ("{it-IT: 0.5}", "coverage: names it-IT, which the scenario does not declare"),
        ("{en-GB: 1.2}", "coverage: en-GB is 1.2, not a fraction from 0 to 1"),
        ("{en-GB: -0.1}", "coverage: en-GB is -0.1, not a fraction from 0 to 1"),
        ("{en-GB: .nan}", "coverage: en-GB is nan, not a fraction from 0 to 1"),
        ("{en-GB: .inf}", "coverage: en-GB is inf, not a fraction from 0 to 1"),
        ("{en-GB: true}", "coverage: en-GB is True, not a fraction from 0 to 1"),
        ("{en-GB: most}", "coverage: en-GB is 'most', not a fraction from 0 to 1"),
    ],
    ids=["list", "undeclared", "above", "below", "nan", "inf", "bool", "string"],
)
def test_malformed_coverage_is_refused(tmp_path: Path, coverage: str, said: str) -> None:
    with pytest.raises(ScenarioError, match=rf"^scenario shop: {re.escape(said)}"):
        _load(tmp_path, COVERED + f"coverage: {coverage}\n" + TRANSLATED_TABLE)


def test_coverage_without_locales_is_refused(tmp_path: Path) -> None:
    with pytest.raises(
        ScenarioError,
        match=r"^scenario shop: coverage: applies to locales:, and the scenario declares none",
    ):
        _load(
            tmp_path,
            HEADER
            + "coverage: {en-GB: 0.5}\ntables:\n  - name: catalog.tb_continent\n    count: 2\n",
        )


def test_session_variables_under_a_scenarios_coverage_point_at_semis_yaml(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError) as refused:
        _load(
            tmp_path,
            COVERED
            + "coverage:\n  session_variables:\n    app.agent_role: manager\n"
            + TRANSLATED_TABLE,
        )
    assert refused.value.message == (
        "scenario shop: coverage: maps a locale to a fraction; session_variables, what "
        "semis coverage reads with, goes under semis.yaml's coverage:"
    )
    assert refused.value.resolution_hint == (
        "Move it to semis.yaml: coverage: {session_variables: {app.agent_role: manager}}."
    )
