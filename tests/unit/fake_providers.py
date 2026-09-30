"""A project's own providers, as `semis.yaml` names them: `tests.unit.fake_providers:…`."""

from faker import Faker

from fraiseql_semis.faker_provider import Library, Rule
from fraiseql_semis.schema import ColumnFacts


def slogan(faker: Faker, _column: ColumnFacts) -> str:
    return faker.catch_phrase()


def ticker(faker: Faker, _column: ColumnFacts) -> str:
    return faker.lexify("????").upper()


PROVIDERS = {"slogan": slogan}
LIBRARY = Library("acme", {"ticker": ticker}, rules=(Rule("ticker", frozenset({"ticker"})),))
NOT_PROVIDERS = 42
BAD_MAPPING = {"broken": 42}
SHADOWING = {"i18n.country_code": slogan}
