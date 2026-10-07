---
title: Writing a Provider Library
description: Package providers and the rules that match them to columns as a library other projects install and enable by name
---

A provider library is a named set of providers, and the rules that match them to columns
by name and type. semis ships two, `i18n` and `organization`. A library another project
can install, private to a company or published, is a `Library` registered under the
`fraiseql_semis.providers` entry point.

## Write the library

```python
# acme_semis/__init__.py
from faker import Faker

from fraiseql_semis import TEXT_TYPES, ColumnFacts, Library, Rule


def ticker(faker: Faker, _column: ColumnFacts) -> str:
    return faker.lexify("????").upper()


LIBRARY = Library(
    "acme",                                   # the name semis.yaml enables it by
    {"ticker": ticker},                       # a scenario names acme.ticker
    rules=(Rule("ticker", names=frozenset({"ticker", "symbol"}), types=TEXT_TYPES),),
)
```

A provider takes the run's seeded `Faker` and the column's facts, and returns a value. It
draws from the `Faker` it is given and from nothing else, so the run stays reproducible.
The facts carry the column's name, type, declared length, nullability and CHECKs, for a
provider that draws differently by column.

## Rules

A `Rule` says which columns a provider draws once the library is enabled, without a
scenario naming it:

| Field | Matches a column when |
|---|---|
| `provider` | the provider the rule applies, by its name in the library |
| `names` | the column's name, lowercased, is one of them; empty matches any name |
| `types` | the column's type family, `varchar` for `varchar(20)`, is one of them; empty matches any type |
| `min_length` | the column declares at least this many characters, or no length at all |

`TEXT_TYPES` is the set of text families: `text`, `varchar`, `char` and `citext`.
`min_length` keeps a three-letter code out of a `char(2)`. A rule naming a provider the
library does not hold is refused when the library is built.

An enabled library's rules come after a scenario's own providers and an enum's labels,
and before semis' built-in providers. Libraries are consulted in the order `semis.yaml`
lists them, and within a library in the order of its rules.

## Register the entry point

```toml
# acme-semis's pyproject.toml
[project.entry-points."fraiseql_semis.providers"]
acme = "acme_semis:LIBRARY"
```

The entry point's name is the library's name. Build and publish the package as any
other, to PyPI or to a company's own index.

## Enable it

Installed beside semis, with `uv add acme-semis`, the library is enabled as a shipped one
is, by its name under `providers:` in `semis.yaml`:

```yaml
providers: [i18n, acme]
```

Installing a library enables nothing: only the libraries `semis.yaml` names are imported.
A scenario then names `acme.ticker` for a column, and every column a rule matches is
drawn by it when the run fills it: a NOT NULL column, or a nullable one the scenario
names under `fill:`.

semis refuses, naming the packages:

- a name two installed packages claim;
- an installed library that claims a shipped name, `i18n` or `organization`;
- an entry point that holds no `Library`, or a library whose name is not its entry
  point's;
- a provider name `semis.yaml` enables twice, through two entries.

## Next steps

- [Shipped provider libraries](/reference/providers/): `i18n` and `organization` as models
- [Writing scenarios](/guides/scenarios/#providers): how a scenario names a provider
- [semis.yaml](/reference/semis-yaml/#providers): the `providers:` key
