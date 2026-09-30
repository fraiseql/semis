"""Countries, languages, locales, currencies and time zones, as their standards spell them."""

from faker import Faker

from fraiseql_semis.faker_provider import TEXT_TYPES as TEXT
from fraiseql_semis.faker_provider import Library, Rule
from fraiseql_semis.schema import ColumnFacts

# ISO 639-1: two letters. Faker's own list mixes in the three-letter codes of 639-2/3.
_LANGUAGES = (
    "ar", "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr", "ga", "he", "hi",
    "hr", "hu", "id", "is", "it", "ja", "ko", "lt", "lv", "mt", "nl", "no", "pl", "pt",
    "ro", "ru", "sk", "sl", "sr", "sv", "th", "tr", "uk", "vi", "zh",
)  # fmt: skip

# Faker's zones still include names tzdata keeps only as legacy links, which a database
# built without them (Debian's tzdata, since 2024, without tzdata-legacy) refuses.
_CANONICAL_ZONES = {
    "Africa/Asmera": "Africa/Asmara",
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Choibalsan": "Asia/Ulaanbaatar",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Rangoon": "Asia/Yangon",
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Europe/Uzhgorod": "Europe/Kyiv",
    "Europe/Zaporozhye": "Europe/Kyiv",
    "Pacific/Enderbury": "Pacific/Kanton",
    "Pacific/Ponape": "Pacific/Pohnpei",
    "Pacific/Truk": "Pacific/Chuuk",
}


def country_code(faker: Faker, _column: ColumnFacts) -> str:
    """ISO 3166-1 alpha-2: ``FR``."""
    return faker.country_code()


def country_code_alpha3(faker: Faker, _column: ColumnFacts) -> str:
    """ISO 3166-1 alpha-3: ``FRA``."""
    return faker.country_code(representation="alpha-3")


def country_name(faker: Faker, _column: ColumnFacts) -> str:
    return faker.country()


def language_code(faker: Faker, _column: ColumnFacts) -> str:
    """ISO 639-1: ``fr``."""
    return faker.random_element(_LANGUAGES)


def language_name(faker: Faker, _column: ColumnFacts) -> str:
    return faker.language_name()


def locale(faker: Faker, _column: ColumnFacts) -> str:
    """A POSIX locale name: ``fr_FR``."""
    return faker.locale()


def currency_code(faker: Faker, _column: ColumnFacts) -> str:
    """ISO 4217: ``EUR``."""
    return faker.currency_code()


def currency_name(faker: Faker, _column: ColumnFacts) -> str:
    return faker.currency_name()


def currency_symbol(faker: Faker, _column: ColumnFacts) -> str:
    return faker.currency_symbol()


def timezone(faker: Faker, _column: ColumnFacts) -> str:
    """An IANA zone by its canonical name: ``Europe/Paris``, ``Asia/Kolkata``."""
    zone = faker.timezone()
    return _CANONICAL_ZONES.get(zone, zone)


LIBRARY = Library(
    "i18n",
    {
        "country_code": country_code,
        "country_code_alpha3": country_code_alpha3,
        "country_name": country_name,
        "language_code": language_code,
        "language_name": language_name,
        "locale": locale,
        "currency_code": currency_code,
        "currency_name": currency_name,
        "currency_symbol": currency_symbol,
        "timezone": timezone,
    },
    rules=(
        Rule(
            "country_code",
            frozenset({"country_code", "iso_country_code", "country_iso_code", "iso_code"}),
            TEXT,
            min_length=2,
        ),
        Rule("country_code_alpha3", frozenset({"country_code_alpha3", "iso3"}), TEXT, 3),
        Rule("country_name", frozenset({"country_name"}), TEXT),
        Rule("language_code", frozenset({"language_code", "iso_language_code", "lang"}), TEXT, 2),
        Rule("language_name", frozenset({"language_name"}), TEXT),
        Rule("locale", frozenset({"locale", "locale_code"}), TEXT),
        Rule(
            "currency_code", frozenset({"currency_code", "iso_currency_code", "currency"}), TEXT, 3
        ),
        Rule("currency_name", frozenset({"currency_name"}), TEXT),
        Rule("currency_symbol", frozenset({"currency_symbol"}), TEXT),
        Rule("timezone", frozenset({"timezone", "time_zone", "tz"}), TEXT),
    ),
)
