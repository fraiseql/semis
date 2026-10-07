"""The provider library: domain values that satisfy the column facts they are matched to."""

import re
from dataclasses import replace

import pytest
from faker import Faker

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.faker_provider import CustomProviderRegistry, FakerProvider, Library, Rule
from fraiseql_semis.generator import FakeDataGenerator
from fraiseql_semis.providers import SHIPPED, i18n, organization
from fraiseql_semis.schema import ColumnFacts, SchemaFacts

COUNTRY = "catalog.tb_country"
I18N = f"""
CREATE SCHEMA catalog;
CREATE TABLE {COUNTRY} (
    pk_country BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    country_code CHAR(2) NOT NULL CHECK (country_code ~ '^[A-Z]{{2}}$')
);
"""


def _columns(ddl: str, table: str) -> dict[str, ColumnFacts]:
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({table: 0x01}))
    return {column.name: column for column in facts.facts_for(table).columns}


def _drawn(column: ColumnFacts, table: str, count: int = 50) -> list[object]:
    registry = CustomProviderRegistry()
    registry.register_library(i18n.LIBRARY)
    provider = FakerProvider(seed=7, registry=registry)
    return [provider.value_for(column, table=table) for _ in range(count)]


def test_iso_code_provider_satisfies_the_check_constraint() -> None:
    column = _columns(I18N, COUNTRY)["country_code"]
    assert column.checks == ("country_code ~ '^[A-Z]{2}$'",)
    values = _drawn(column, COUNTRY)
    assert all(re.fullmatch(r"[A-Z]{2}", str(value)) for value in values), values


# A CRM's contacts and companies, keeping the columns the libraries draw.
CONTACT = "crm.tb_contact"
COMPANY = "crm.tb_company"
CRM = f"""
CREATE SCHEMA crm;
CREATE TABLE {CONTACT} (
    pk_contact BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT UNIQUE,
    job_title TEXT,
    first_name TEXT,
    last_name TEXT,
    email_address TEXT UNIQUE,
    office_phone VARCHAR(20),
    mobile_phone VARCHAR(20),
    lang VARCHAR(2),
    locale VARCHAR(10),
    timezone TEXT,
    role TEXT NOT NULL DEFAULT 'viewer'
);
CREATE TABLE {COMPANY} (
    pk_company BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name TEXT,
    domain_tld VARCHAR(10),
    legal_identifier TEXT,
    vat_identifier TEXT
);
"""
CRM_CODES = TableCodes({CONTACT: 0x10, COMPANY: 0x11})


def _crm(table: str) -> dict[str, ColumnFacts]:
    facts = SchemaFacts.from_source(CRM, table_codes=CRM_CODES)
    return {column.name: column for column in facts.facts_for(table).columns}


def _enabled(*libraries: Library) -> FakerProvider:
    registry = CustomProviderRegistry()
    for library in libraries:
        registry.register_library(library)
    return FakerProvider(seed=7, registry=registry)


def _from(library: Library, table: str, column: str, count: int = 50) -> list[str]:
    provider = _enabled(library)
    facts = _crm(table)[column]
    return [str(provider.value_for(facts, table=table)) for _ in range(count)]


def _luhn_valid(digits: str) -> bool:
    total = 0
    for position, digit in enumerate(reversed(digits)):
        value = int(digit) * (2 if position % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def test_i18n_fills_a_contacts_language_locale_and_time_zone() -> None:
    langs = _from(i18n.LIBRARY, CONTACT, "lang")
    locales = _from(i18n.LIBRARY, CONTACT, "locale")
    zones = _from(i18n.LIBRARY, CONTACT, "timezone")
    assert all(re.fullmatch(r"[a-z]{2}", lang) for lang in langs), langs
    assert all(re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}", value) for value in locales), locales
    assert all(len(value) <= 10 for value in locales)
    assert all("/" in zone for zone in zones), zones


class _Drawing:
    """A Faker whose one time zone is *zone*."""

    def __init__(self, zone: str) -> None:
        self.zone = zone

    def timezone(self) -> str:
        return self.zone


@pytest.mark.parametrize(
    ("drawn", "written"),
    [
        ("Asia/Calcutta", "Asia/Kolkata"),
        ("Europe/Zaporozhye", "Europe/Kyiv"),
        ("Pacific/Truk", "Pacific/Chuuk"),
        ("Europe/Paris", "Europe/Paris"),
    ],
)
def test_a_time_zone_is_written_by_its_canonical_name(drawn: str, written: str) -> None:
    faker: Faker = _Drawing(drawn)  # ty: ignore[invalid-assignment]
    assert i18n.timezone(faker, _crm(CONTACT)["timezone"]) == written


def test_a_rule_does_not_match_a_column_too_short_for_its_values() -> None:
    column = ColumnFacts(
        name="currency", type_key="char(2)", raw_sql_type="bpchar(2)",
        not_null=True, default=None, unique=False,
    )  # fmt: skip
    rule = next(rule for rule in i18n.LIBRARY.rules if rule.provider == "currency_code")
    assert not rule.matches(column)
    assert rule.matches(replace(column, type_key="char(3)"))


def test_a_rules_text_is_cut_to_the_declared_length() -> None:
    column = replace(_crm(COMPANY)["domain_tld"], name="company_name")
    provider = _enabled(organization.LIBRARY)
    values = [str(provider.value_for(column, table=COMPANY)) for _ in range(20)]
    assert all(len(value) <= 10 for value in values), values
    assert any(len(value) == 10 for value in values)  # cut, not drawn short


def test_a_scenario_provider_beats_a_library_rule() -> None:
    registry = CustomProviderRegistry()
    registry.register_library(i18n.LIBRARY)
    registry.register_column(CONTACT, "lang", lambda _faker, _column: "xx")
    provider = FakerProvider(seed=7, registry=registry)
    assert provider.value_for(_crm(CONTACT)["lang"], table=CONTACT) == "xx"


def test_an_enums_labels_beat_a_library_rule() -> None:
    column = replace(_crm(CONTACT)["lang"], enum_values=("fr", "en"))
    values = {_enabled(i18n.LIBRARY).value_for(column, table=CONTACT) for _ in range(20)}
    assert values <= {"fr", "en"}


def test_organization_fills_a_contact() -> None:
    titles = _from(organization.LIBRARY, CONTACT, "job_title")
    emails = _from(organization.LIBRARY, CONTACT, "email_address")
    phones = _from(organization.LIBRARY, CONTACT, "office_phone")
    assert all(title and "@" not in title for title in titles)
    assert all(re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]+", email) for email in emails), emails
    assert len(set(emails)) == len(emails)  # email_address is UNIQUE
    assert all(re.fullmatch(r"\+\d{10,11}", phone) for phone in phones), phones


def test_organization_draws_registry_numbers_that_check() -> None:
    sirets = _from(organization.LIBRARY, COMPANY, "legal_identifier")
    vats = _from(organization.LIBRARY, COMPANY, "vat_identifier")
    assert all(re.fullmatch(r"\d{14}", siret) and _luhn_valid(siret) for siret in sirets)
    assert all(_luhn_valid(siret[:9]) for siret in sirets)
    for vat in vats:
        siren = vat[4:]
        assert re.fullmatch(r"FR\d{11}", vat), vat
        assert _luhn_valid(siren)
        assert int(vat[2:4]) == (12 + 3 * (int(siren) % 97)) % 97


def test_a_generated_row_carries_the_librarys_values() -> None:
    # A rule draws a column the row must carry; a nullable one is left NULL unless named.
    ddl = CRM.replace("locale VARCHAR(10)", "locale VARCHAR(10) NOT NULL")
    facts = SchemaFacts.from_source(ddl, table_codes=CRM_CODES)
    registry = CustomProviderRegistry()
    registry.register_library(i18n.LIBRARY)
    rows = FakeDataGenerator(facts, 0x5001, seed=7, providers=registry).generate_rows(
        CONTACT, count=3
    )
    assert all(re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}", str(row["locale"])) for row in rows)


def test_a_rule_naming_no_provider_of_its_library_is_refused() -> None:
    with pytest.raises(ValueError, match="a rule names absent"):
        Library("broken", {}, rules=(Rule("absent"),))


def test_a_library_names_its_providers_after_itself() -> None:
    assert "i18n.country_code" in i18n.LIBRARY.named()
    assert i18n.LIBRARY.named()["i18n.country_code"] is i18n.country_code


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_every_shipped_provider_is_reachable_by_name(name: str) -> None:
    library = SHIPPED[name]
    faker = Faker("en_US")
    faker.seed_instance(7)
    text = ColumnFacts(
        name="anything", type_key="text", raw_sql_type="text",
        not_null=True, default=None, unique=False,
    )  # fmt: skip
    named = library.named()
    assert library.name == name
    assert sorted(named) == sorted(f"{name}.{provider}" for provider in library.providers)
    assert all(provider(faker, text) not in {None, ""} for provider in named.values())


def test_semis_ships_two_libraries() -> None:
    assert sorted(SHIPPED) == ["i18n", "organization"]
