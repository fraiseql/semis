"""Domain value providers: i18n and organization.

Each module holds one :class:`Library`: providers a scenario names as
``<library>.<provider>``, and the rules that let an enabled library draw the columns
they match. Pure, as every provider is: the run's seeded ``Faker`` and a column's facts
in, a value out.
"""

from fraiseql_semis.faker_provider import Library, Rule
from fraiseql_semis.providers import i18n, organization

SHIPPED: dict[str, Library] = {
    library.name: library for library in (i18n.LIBRARY, organization.LIBRARY)
}
"""The libraries semis ships, by the name ``semis.yaml`` enables them with."""

__all__ = ["SHIPPED", "Library", "Rule"]
