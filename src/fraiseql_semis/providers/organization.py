"""Companies and the people in them: names, French registry numbers, titles, contacts."""

from faker import Faker

from fraiseql_semis.faker_provider import TEXT_TYPES as TEXT
from fraiseql_semis.faker_provider import Library, Rule
from fraiseql_semis.schema import ColumnFacts

_DEPARTMENTS = (
    "Finance", "Human Resources", "IT", "Legal", "Marketing", "Operations",
    "Procurement", "Sales", "Facilities", "Customer Service", "Research", "Logistics",
)  # fmt: skip


def _luhn_completed(digits: str) -> str:
    """*digits* followed by the check digit that makes the whole pass Luhn's test."""
    total = 0
    for position, digit in enumerate(reversed(digits)):
        value = int(digit) * (2 if position % 2 == 0 else 1)
        total += value - 9 if value > 9 else value  # noqa: PLR2004 — Luhn's digit sum
    return f"{digits}{(10 - total % 10) % 10}"


def company(faker: Faker, _column: ColumnFacts) -> str:
    return faker.company()


def siren(faker: Faker, _column: ColumnFacts) -> str:
    """A French company number: nine digits, the last a Luhn check digit."""
    return _luhn_completed(faker.numerify("########"))


def siret(faker: Faker, column: ColumnFacts) -> str:
    """A French establishment number: a SIREN, four digits, and a Luhn check digit."""
    return _luhn_completed(siren(faker, column) + faker.numerify("####"))


def vat_number(faker: Faker, column: ColumnFacts) -> str:
    """A French VAT number: ``FR``, the two-digit key its SIREN determines, the SIREN."""
    number = siren(faker, column)
    return f"FR{(12 + 3 * (int(number) % 97)) % 97:02d}{number}"


def job_title(faker: Faker, _column: ColumnFacts) -> str:
    return faker.job()


def department(faker: Faker, _column: ColumnFacts) -> str:
    return faker.random_element(_DEPARTMENTS)


def email(faker: Faker, _column: ColumnFacts) -> str:
    return faker.company_email()


def website(faker: Faker, _column: ColumnFacts) -> str:
    return faker.url()


def phone(faker: Faker, _column: ColumnFacts) -> str:
    """E.164, at most thirteen characters: ``+33612345678``."""
    return f"+{faker.random_int(1, 99)}{faker.numerify('#########')}"


LIBRARY = Library(
    "organization",
    {
        "company": company,
        "siren": siren,
        "siret": siret,
        "vat_number": vat_number,
        "job_title": job_title,
        "department": department,
        "email": email,
        "website": website,
        "phone": phone,
    },
    rules=(
        Rule(
            "company",
            frozenset({"company", "company_name", "legal_name", "organization_name"}),
            TEXT,
        ),
        Rule("siren", frozenset({"siren"}), TEXT, 9),
        Rule("siret", frozenset({"siret", "legal_identifier"}), TEXT, 14),
        Rule("vat_number", frozenset({"vat_number", "vat_identifier", "vat_id"}), TEXT, 13),
        Rule("job_title", frozenset({"job_title"}), TEXT),
        Rule("department", frozenset({"department", "department_name"}), TEXT),
        Rule("email", frozenset({"email", "email_address", "company_email"}), TEXT),
        Rule("website", frozenset({"website", "web_site"}), TEXT),
        Rule(
            "phone",
            frozenset({"phone", "phone_number", "office_phone", "mobile_phone", "fax"}),
            TEXT,
            13,
        ),
    ),
)
