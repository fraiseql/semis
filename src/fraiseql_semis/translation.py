"""Translations: the locales a scenario declares, and the rows written per locale.

A locale is a code as the project's locale table holds it (``fr-FR``, ``fr``,
``pt_BR``), and the Faker locale its labels are drawn in. Pure: names in, values out.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from math import ceil
from string import Formatter
from types import MappingProxyType
from typing import NamedTuple

from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.faker_provider import LOCALES
from fraiseql_semis.schema import SchemaFacts, qualified

MAX_LOCALES = 254
"""How many locales a scenario writes: a translation row's sequence slot is 1 to 254,
255 kept for the pseudo-locale."""
STRIDE = 256
"""The sequences an entity row's translations take: ``entity sequence * 256 + slot``."""
PSEUDO_SLOT = 255
"""The pseudo-locale's slot: after every declared locale's, so declaring it moves none."""


@dataclass(frozen=True)
class TranslationRule:
    """How a project's translation tables are named and which columns they use, as
    ``semis.yaml``'s ``translations:`` says; every key defaults to the trinity's names.

    *table* names an entity's translation table, ``{entity}`` the entity's name without
    its ``tb_`` prefix and ``{schema}`` its schema. *locale_table* is the locale table
    and its code column, when named; otherwise the table ``locale_fk``'s foreign key
    names, found by ``code``.
    """

    table: str = "{schema}.tl_{entity}"
    label_column: str = "label"
    locale_fk: str = "fk_locale"
    locale_table: tuple[str, str] | None = None

    @property
    def code(self) -> str:
        """The locale table's column a locale is found by."""
        return "code" if self.locale_table is None else self.locale_table[1]

    def table_for(self, entity: str) -> str:
        """The translation table of the schema-qualified *entity*: its name without its
        ``tb_`` prefix as ``{entity}``, its schema as ``{schema}``, and a template with no
        schema in the entity's."""
        schema, _, name = entity.partition(".")
        template = self.table if "." in self.table else "{schema}." + self.table
        return template.format(schema=schema, entity=name.removeprefix("tb_"))


def template_is_valid(template: str) -> bool:
    """Whether *template* names a translation table: it holds ``{entity}``, no other
    placeholder but ``{schema}``, and ``{schema}`` only before its dot."""
    try:
        names = [name for _, name, _, _ in Formatter().parse(template) if name is not None]
    except ValueError:
        return False
    schema, dot, _ = template.partition(".")
    return (
        "entity" in names
        and set(names) <= {"schema", "entity"}
        and template.count("{schema}") == (schema.count("{schema}") if dot else 0)
    )


@dataclass(frozen=True)
class Locale:
    """A locale a scenario writes translations in: its *code* in the locale table, and
    the *faker* locale its values are drawn in."""

    code: str
    faker: str


def faker_locale(code: str) -> str | None:
    """The Faker locale of the locale *code*: the code itself, the code with ``-`` read
    as ``_`` and its region upper-cased (``fr-fr`` → ``fr_FR``), or its bare language
    when Faker has one (``de``); ``None`` when Faker has none of them."""
    language, _, region = code.replace("-", "_").partition("_")
    candidates = (
        code,
        f"{language.lower()}_{region.upper()}" if region else None,
        language.lower(),
    )
    return next((name for name in candidates if name in LOCALES), None)


def languages_of(code: str) -> list[str]:
    """Faker's locales of *code*'s language, for a code that names none of them."""
    language = code.replace("-", "_").partition("_")[0].lower()
    return sorted(name for name in LOCALES if name.partition("_")[0] == language)


def example_of(code: str) -> str:
    """A Faker locale to suggest for *code*: its own, its language's main one
    (``fr`` → ``fr_FR``), or ``en_US``."""
    language = code.replace("-", "_").partition("_")[0].lower()
    found = faker_locale(code) or f"{language}_{language.upper()}"
    named = languages_of(code)
    return found if found in LOCALES else named[0] if named else "en_US"


@dataclass(frozen=True)
class TranslationTable:
    """Where *entity*'s translations are written: *table*, its *entity_key* to the entity,
    its *locale_key* to *locale_table*, whose *code* column finds a locale, and the
    *label* drawn per locale."""

    table: str
    entity: str
    entity_key: str
    locale_key: str
    label: str
    locale_table: str
    code: str

    @classmethod
    def of(
        cls,
        facts: SchemaFacts,
        entity: str,
        table: str,
        rule: TranslationRule | None = None,
    ) -> TranslationTable:
        """*entity*'s translation *table*, its columns found in *facts* by the project's
        *rule*; refused when it is not one semis can write."""
        rule = rule or TranslationRule()
        columns = {column.name: column for column in facts.columns(table) or ()}
        if facts.ref(table) is None:
            raise ScenarioError(
                f"{entity}'s translations: names {table}, which is not in the schema",
                resolution_hint="Name the translation table, schema-qualified, as the schema holds it.",
            )
        for needed in (rule.locale_fk, rule.label_column):
            if needed not in columns:
                raise ScenarioError(
                    f"{table} has no {needed} column, which a translation table needs",
                    resolution_hint=(
                        f"translations: writes a table with an entity key, {rule.locale_fk} "
                        f"and {rule.label_column}, as semis.yaml's translations: names them."
                    ),
                )
        reference = columns[rule.locale_fk].foreign_key
        if reference is None:
            raise ScenarioError(
                f"{table}.{rule.locale_fk} declares no foreign key, so semis cannot point it "
                "at a locale row",
                resolution_hint="translations: writes a table whose locale key is a declared foreign key.",
            )
        locale_table = qualified(reference.table)
        if rule.locale_table is not None and rule.locale_table[0] != locale_table:
            raise ScenarioError(
                f"{table}.{rule.locale_fk} points at {locale_table}, not semis.yaml's "
                f"locale_table {rule.locale_table[0]}",
                resolution_hint=(
                    "Name, as locale_table:, the table the locale key's foreign key names: "
                    "a run points the key at no other."
                ),
            )
        keys = sorted(
            name
            for name, column in columns.items()
            if column.foreign_key is not None and qualified(column.foreign_key.table) == entity
        )
        if not keys:
            raise ScenarioError(
                f"{table} has no foreign key to {entity}",
                resolution_hint="A translation table points at its entity by a declared foreign key.",
            )
        if len(keys) > 1:
            raise ScenarioError(
                f"{table} has two foreign keys to {entity} ({', '.join(keys)}): one is the "
                "entity's",
                resolution_hint="A translation table points at its entity by one foreign key.",
            )
        if rule.code not in {column.name for column in facts.columns(locale_table) or ()}:
            raise ScenarioError(
                f"{locale_table} has no {rule.code} column to find a locale by",
                resolution_hint=f"The locale table holds each locale's {rule.code}, as locales: lists them.",
            )
        return cls(
            table, entity, keys[0], rule.locale_fk, rule.label_column, locale_table, rule.code
        )


class Translation(NamedTuple):
    """What a run writes for one translated table: its translation *table*, one row per
    entity row and locale of *locales*, in that order, then, with a *pseudo* locale's
    code, one more whose label is the first locale's transformed. A locale *coverage*
    names keeps its label for that share of the entity rows, the ones `kept` picks."""

    table: TranslationTable
    locales: tuple[Locale, ...]
    pseudo: str | None = None
    coverage: Mapping[str, Fraction] = MappingProxyType({})


def sequence(entity: int, slot: int) -> int:
    """The sequence of the translation row in the locale of *slot* (1 for the first of
    ``locales:``) of the entity row of sequence *entity*: appending a locale moves none."""
    return entity * STRIDE + slot


def seed_for(seed: int | None, table: str, code: str) -> int | None:
    """The seed of *code*'s values in the translation *table*, from the run's *seed*: the
    first 8 bytes of a sha256 over ``[seed, table, code]``, so each locale's stream of
    each table is its own. ``None`` for an unseeded run."""
    if seed is None:
        return None
    encoded = json.dumps([seed, table, code], separators=(",", ":")).encode()
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")


def locale_keys(
    found: Iterable[tuple[object, object]], codes: Iterable[str], *, table: str, column: str
) -> dict[str, object]:
    """Each of *codes*' key in the locale *table*, from the rows *found* there as
    ``(code, key)``; refused when a code has no row, or two."""
    rows: dict[object, list[object]] = {}
    for code, key in found:
        rows.setdefault(code, []).append(key)
    keys = {}
    for code in codes:
        matched = rows.get(code, [])
        if not matched:
            raise ScenarioError(
                f"locale {code} has no row in {table} ({column})",
                resolution_hint="Insert its row, or drop it from locales:.",
            )
        if len(matched) > 1:
            raise ScenarioError(
                f"locale {code} has {len(matched)} rows in {table} ({column})",
                resolution_hint=(
                    "Give each locale one row: which of two a translation points at would be "
                    "arbitrary."
                ),
            )
        keys[code] = matched[0]
    return keys


def locale_reads(translations: Mapping[str, Translation]) -> dict[str, tuple[str, list[str]]]:
    """Each locale table *translations* point at, with the column a locale is found by
    and the codes declared for it."""
    reads: dict[str, tuple[str, list[str]]] = {}
    for translation in translations.values():
        _, codes = reads.setdefault(translation.table.locale_table, (translation.table.code, []))
        codes.extend(locale.code for locale in translation.locales if locale.code not in codes)
        if translation.pseudo is not None and translation.pseudo not in codes:
            codes.append(translation.pseudo)
    return reads


PSEUDO_LETTERS = dict(
    zip(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "ḁḃḉḋḕḟḡḣḭỻḱḷḿṅṓṕỽṙṡṭṳṽẁẋẏẑḀḂḈḊḔḞḠḢḬỺḰḶḾṄṒṔỼṘṠṬṲṼẀẊẎẐ",
        strict=True,
    )
)
"""Each ASCII letter's pseudo letter: 52 distinct letters of Latin Extended Additional
(U+1E00 to U+1EFF), a letter with a mark below or above it; ``j`` and ``q``, which the
block has no form of, take the Middle Welsh ``ll`` and ``v``. No word list but
Vietnamese holds them, so the transform keeps distinct labels distinct."""
_PSEUDO = str.maketrans(PSEUDO_LETTERS)
_GROWTH = 1.4  # a pseudo label's text is at least 40% longer than the label's


def pseudo(text: str, length: int | None = None) -> str:
    """*text* as a pseudo-locale writes it: each ASCII letter its ``PSEUDO_LETTERS``
    letter, padded with a space and ``·`` to 40% more characters, bracketed —
    ``Category`` → ``[Ḉḁṭḕḡṓṙẏ ···]``. Cut to *length* characters, the padding goes
    first, then the text, and both brackets stay. No randomness: the same text gives
    the same pseudo label."""
    body = text.translate(_PSEUDO)
    if body:
        grown = ceil(_GROWTH * len(body))
        body += " " + "·" * max(grown - len(body) - 1, 0)
    if length is not None:
        body = body[: max(length - 2, 0)]
    return f"[{body}]"


def kept(
    sequences: Iterable[int], coverage: Fraction, *, scenario: int, table: str, code: str
) -> frozenset[int]:
    """The entity rows, by *sequences*, whose translation into *code* a run writes at
    *coverage*: exactly ``floor(n * coverage)`` of the *n*, the first by a sha256 over the
    JSON ``[scenario, table, code, sequence]``.

    So the same scenario keeps the same rows whatever its seed, each locale misses its
    own, and raising coverage only adds rows; a change of count may re-pick them.
    """
    rows = list(sequences)

    def rank(sequence: int) -> bytes:
        encoded = json.dumps([scenario, table, code, sequence], separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).digest()

    count = int(len(rows) * coverage)
    return frozenset(sorted(rows, key=rank)[:count])
