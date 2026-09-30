"""FakerProvider: what a value looks like, chosen from the whole ColumnFacts.

Pure: facts in, values out — no database and no file system.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from faker import Faker

from fraiseql_semis.errors import RowContractError
from fraiseql_semis.rows import Violation, declared_length
from fraiseql_semis.schema import ColumnFacts

Provider = Callable[[Faker, ColumnFacts], object]
"""A custom provider: the run's seeded ``Faker`` and the column's facts in, a value out."""

_BY_NAME: dict[str, Callable[[Faker], object]] = {
    "name": lambda f: f.name(),
    "first_name": lambda f: f.first_name(),
    "last_name": lambda f: f.last_name(),
    "email": lambda f: f.email(),
    "phone": lambda f: f.phone_number(),
    "address": lambda f: f.address(),
    "city": lambda f: f.city(),
    "country": lambda f: f.country(),
    "company": lambda f: f.company(),
    "url": lambda f: f.url(),
    "description": lambda f: f.text(max_nb_chars=200),
    "title": lambda f: f.sentence(nb_words=4),
    "iso_code": lambda f: f.country_code(),
}
# Longest first, so `company_name` is a company rather than a person's name.
_PATTERNS = sorted(_BY_NAME, key=len, reverse=True)

# How many draws a `unique` column gets before its value space counts as spent.
_REDRAWS = 100

# Faker's time values end *now* by default; a seeded run must draw the same ones whenever
# it runs (D10), so every one is drawn from this fixed window instead.
_WINDOW = (datetime(1970, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))


def _instant(faker: Faker) -> datetime:
    return faker.date_time_between(start_date=_WINDOW[0], end_date=_WINDOW[1], tzinfo=UTC)


TEXT_TYPES = frozenset({"text", "varchar", "char", "citext"})
"""The type families a text value fits: what a rule matching any text names."""
_NUMERIC = re.compile(r"numeric(?:\((\d+)(?:,(\d+))?\))?")
_BY_TYPE: dict[str, Callable[[Faker], object]] = {
    "smallint": lambda f: f.random_int(1, 32_767),
    "integer": lambda f: f.random_int(1, 2_147_483_647),
    "bigint": lambda f: f.random_int(1, 2_147_483_647),
    "real": lambda f: f.pyfloat(left_digits=5, right_digits=2, positive=True),
    "double precision": lambda f: f.pyfloat(left_digits=5, right_digits=2, positive=True),
    "boolean": lambda f: f.pybool(),
    "date": lambda f: _instant(f).date(),
    "timestamp": lambda f: _instant(f).replace(tzinfo=None),
    "timestamptz": _instant,
    "time": lambda f: _instant(f).time(),
    "uuid": lambda f: f.uuid4(cast_to=None),
}


@dataclass(frozen=True)
class Rule:
    """Where a library's *provider* applies: to a column by its name and its type.

    A column matches when its name is one of *names* and its type family one of
    *types*; an empty set matches any. A column declaring fewer than *min_length*
    characters is not matched, so a three-letter code never lands in a ``char(2)``.
    """

    provider: str
    names: frozenset[str] = frozenset()
    types: frozenset[str] = frozenset()
    min_length: int = 0

    def matches(self, column: ColumnFacts) -> bool:
        length = declared_length(column)
        return (
            (not self.names or column.name.lower() in self.names)
            and (not self.types or _family(column) in self.types)
            and (length is None or length >= self.min_length)
        )


@dataclass(frozen=True)
class Library:
    """A named set of providers, and the rules that match them to columns.

    A scenario names a library's provider as ``<library>.<provider>``; enabled, a
    library also draws every column one of its rules matches.
    """

    name: str
    providers: Mapping[str, Provider]
    rules: tuple[Rule, ...] = ()

    def __post_init__(self) -> None:
        unknown = sorted({rule.provider for rule in self.rules} - set(self.providers))
        if unknown:
            raise ValueError(f"library {self.name}: a rule names {', '.join(unknown)}")

    def named(self) -> dict[str, Provider]:
        """Every provider, by the name a scenario gives it."""
        return {f"{self.name}.{name}": provider for name, provider in self.providers.items()}


class CustomProviderRegistry:
    """Providers a project registers, which beat every built-in choice.

    A column entry is keyed by the qualified table (D14); a global entry matches every
    column whose name contains its pattern, in the order registered. A library's rules
    are consulted after both, and after an enum's labels, in the order registered.
    """

    def __init__(self) -> None:
        self._columns: dict[tuple[str, str], Provider] = {}
        self._globals: dict[str, Provider] = {}
        self._rules: list[tuple[Rule, Provider]] = []

    def register_column(self, table: str, column: str, provider: Provider) -> None:
        self._columns[table, column] = provider

    def register_global(self, pattern: str, provider: Provider) -> None:
        self._globals[pattern] = provider

    def register_library(self, library: Library) -> None:
        self._rules.extend((rule, library.providers[rule.provider]) for rule in library.rules)

    def matching(self, column: ColumnFacts) -> Provider | None:
        """The provider of the first library rule *column* matches."""
        return next((provider for rule, provider in self._rules if rule.matches(column)), None)

    def provider_for(self, table: str, column: str) -> Provider | None:
        if (table, column) in self._columns:
            return self._columns[table, column]
        return next(
            (provider for pattern, provider in self._globals.items() if pattern in column.lower()),
            None,
        )


class FakerProvider:
    """A value per column, drawn from one seeded ``Faker`` so a run is reproducible.

    The choice runs: a registered provider, then an enum's labels, then a library rule
    the column matches, then a provider named by the column's name, then one whose name
    the column's contains, then one for its type. A type nothing covers yields ``None``,
    and the row contract decides. Text is cut to the length the column declares.

    A ``unique`` column never repeats a value within the run, per qualified table.
    """

    def __init__(
        self,
        *,
        locale: str = "en_US",
        seed: int | None = None,
        registry: CustomProviderRegistry | None = None,
    ) -> None:
        self.faker = Faker(locale)
        self.faker.seed_instance(seed)
        self._registry = registry or CustomProviderRegistry()
        self._used: dict[tuple[str, str], set[object]] = {}

    def value_for(self, column: ColumnFacts, *, table: str) -> object:
        """A value for *column* of the qualified *table*."""
        return self.drawer(column, table=table)()

    def drawer(self, column: ColumnFacts, *, table: str) -> Callable[[], object]:
        """What draws each value for *column* of the qualified *table*, chosen once.

        Choosing consumes no randomness, so a table's drawers can be chosen before its
        first row and the run still draws the same values.
        """
        draw = self._choose(column, table)
        if not column.unique:
            return draw
        used = self._used.setdefault((table, column.name), set())

        def distinct() -> object:
            for _ in range(_REDRAWS):
                value = draw()
                if value is None or value not in used:
                    used.add(value)
                    return value
            found = f"{_REDRAWS} draws each repeated a value this run already used"
            raise RowContractError(
                str(Violation(table, column.name, "unique", found)),
                resolution_hint=(
                    "Lower the count, widen the column's type, or register a provider "
                    "that yields distinct values."
                ),
            )

        return distinct

    def _choose(self, column: ColumnFacts, table: str) -> Callable[[], object]:
        faker = self.faker
        custom = self._registry.provider_for(table, column.name)
        if custom is not None:
            return lambda: custom(faker, column)
        labels = column.enum_values
        if labels is not None:
            return lambda: faker.random_element(labels)
        matched = self._registry.matching(column)
        if matched is not None:
            return _fitting(lambda: matched(faker, column), column)
        family = _family(column)
        if family in TEXT_TYPES:
            return _fitting(self._text(column), column)
        numeric = _NUMERIC.fullmatch(column.type_key or "")
        if numeric:
            # `numeric(p)` has scale 0; a bare `numeric` has neither, so pick a price.
            precision, scale = (
                (int(numeric.group(1)), int(numeric.group(2) or 0)) if numeric.group(1) else (7, 2)
            )
            return lambda: faker.pydecimal(
                left_digits=precision - scale, right_digits=scale, positive=True
            )
        by_type = _BY_TYPE.get(family)
        return (lambda: None) if by_type is None else (lambda: by_type(faker))

    def word(self) -> str:
        """One word, from the same seeded source as every value."""
        return self.faker.word()

    def _text(self, column: ColumnFacts) -> Callable[[], object]:
        name = column.name.lower()
        pattern = name if name in _BY_NAME else next((p for p in _PATTERNS if p in name), None)
        if pattern is None:
            return self.faker.word
        by_name, faker = _BY_NAME[pattern], self.faker
        return lambda: by_name(faker)


def _family(column: ColumnFacts) -> str:
    """The type without its modifiers: ``varchar`` for ``varchar(20)``."""
    return (column.type_key or "").split("(")[0]


def _fitting(draw: Callable[[], object], column: ColumnFacts) -> Callable[[], object]:
    """*draw*, each string it yields cut to the characters *column* declares."""
    length = declared_length(column)
    return draw if length is None else lambda: _cut(draw(), length)


def _cut(value: object, length: int) -> object:
    return value[:length].rstrip() if isinstance(value, str) else value
