---
title: Writing Scenarios
description: Give columns their values with overrides, providers and fill, leave nullable keys empty, and draw hierarchies of self-referencing tables
---

A scenario lists the tables of a run and how many rows each gets. Everything else is
drawn from the schema's facts, and a scenario says only where it wants something else:
a value, a provider, a key left `NULL`, a tree, or a column a trigger fills. This page
works in the project from [Getting started](/getting-started/).
[The scenario file](/reference/scenario-file/) lists every key.

## Overrides

`overrides:` maps a column to a value for every row, or to a list with one value per row:

```yaml title="scenarios/europe_asia.yaml"
scenario_id: 0x5003
name: europe_asia
description: Two continents, and two countries on each
mode: prep-seed
seed: 42

tables:
  - name: catalog.tb_continent
    count: 2
    overrides:
      name: [Europe, Asia]
  - name: catalog.tb_country
    count: 4
    overrides:
      name: [France, Japan, Spain, India]
      iso_code: [FR, JP, ES, IN]
```

Children are spread over their parents round-robin, so the first and third countries
belong to the first continent:

```bash
semis seeds scenarios/europe_asia.yaml -o db/seeds/europe_asia
head -n 5 db/seeds/europe_asia/002_prep_seed.tb_country.sql
```

```sql
INSERT INTO prep_seed.tb_country (id, identifier, fk_continent_id, name, iso_code) VALUES
    ('03040506-5003-8001-8000-000000000001', 'ago-5003-1', '02030405-5003-8001-8000-000000000001', 'France', 'FR'),
    ('03040506-5003-8001-8000-000000000002', 'site-5003-2', '02030405-5003-8001-8000-000000000002', 'Japan', 'JP'),
    ('03040506-5003-8001-8000-000000000003', 'face-5003-3', '02030405-5003-8001-8000-000000000001', 'Spain', 'ES'),
    ('03040506-5003-8001-8000-000000000004', 'election-5003-4', '02030405-5003-8001-8000-000000000002', 'India', 'IN');
```

A list must hold exactly one value per row, or the scenario is refused. An override is
written as given: the [row contract](/concepts/row-contract/) still checks it, and a
value longer than its column is refused rather than cut.

A nullable foreign key whose parent table is not in the run is written `NULL`. One
overridden `null` is written `NULL` even when its parent is:

```yaml
  - name: inventory.tb_item
    count: 50
    overrides: {fk_account: null, fk_order: null}   # no account, no order
```

From Python, an override may also be a function of the row's 0-based index, as the
[Python API](/reference/python-api/) shows. A scenario file holds values only: nothing in
it is evaluated.

## Parents already in the database

In read-back, a parent may be rows the database already holds, reference rows the
schema's own DDL inserts, rather than rows the run generates. List their table under
`existing:`, beside `tables:`:

```yaml
existing:
  - name: shop.tb_category            # every row, round-robin, in pk_* order
  - name: shop.tb_country
    where: {identifier: [fr, de, es]} # these rows, in this order
```

[The two FK modes](/concepts/fk-modes/#parents-already-in-the-database) says how their
keys are read, and what is refused.

## Copied columns

A column may hold a value of the row its foreign key points at: a tenant id carried down
from the organization a contact belongs to, say. `copies:` names the column, the key and
the parent's column:

```yaml
  - name: tenant.tb_organization
    count: 6
  - name: tenant.tb_contact
    count: 12
    copies:
      tenant_id: fk_customer_org.id   # the id of the organization fk_customer_org points at
```

The twelve contacts spread over the six organizations, round-robin, and each one's
`tenant_id` is its own organization's `id`, in either mode. Nothing is written by hand,
so a change of scenario id, table code or count moves both together. [Copied
columns](/reference/scenario-file/#copied-columns) lists what a copy may follow and what
is refused.

## Nullable columns

A nullable column that is not a key — a soft-delete or an audit column — is written
`NULL` unless the scenario names it: by an override, by a `providers:` entry for it, or
under `fill:`. A provider library's rule matching the column does not name it. The run
prints a line per table naming the columns it left `NULL`.

```yaml
  - name: shop.tb_customer
    count: 3
    fill: [created_by]                # drawn; deleted_at is written NULL
```

`fill: all` draws every nullable column of the table, as 0.1.0 did. See
[which columns are drawn](/reference/scenario-file/#which-columns-are-drawn).

## Providers

`providers:` names the provider that draws one column. A shipped or installed library's
provider is named `<library>.<provider>`, and a project's own by the name its module
gives it:

```yaml
  - name: catalog.tb_currency
    count: 20
    providers: {iso_code: i18n.currency_code, name: slogan}
```

A column that neither an override nor a scenario's provider names is drawn by the first
of these that applies:

1. the column's enum labels, when its type is an enum;
2. a rule of a library `semis.yaml` enables, in the order it lists them: `lang VARCHAR(2)`
   receives `fr`;
3. a built-in provider for the column's name: `email`, `city`, `company`, `url` and others;
4. a built-in provider for the column's type: integers, decimals within their precision,
   booleans, dates and times, UUIDs, and a word for text.

Text these draw is cut to the length the column declares; a value a scenario's own
provider draws is not, and one too long is refused. A type no provider covers draws
`NULL`, and the row contract refuses it when the column is NOT NULL. A UNIQUE column never repeats
a value within the run. [Shipped provider libraries](/reference/providers/) lists every
rule.

### A project's own providers

A provider is a function that takes the run's seeded `Faker` and the column's facts, and
returns a value. A project keeps its own in a module, as a mapping of names to functions:

```python title="fakes/mottos.py"
from faker import Faker

from fraiseql_semis import ColumnFacts


def motto(faker: Faker, _column: ColumnFacts) -> str:
    return faker.catch_phrase()


PROVIDERS = {"motto": motto}
```

`semis.yaml` names the module and the attribute, as `module:attribute`:

```yaml title="semis.yaml"
schema:
  ddl: db/schema/
scenarios: scenarios/
table_codes:
  catalog.tb_continent: 0x02030405
  catalog.tb_country: 0x03040506
providers: [i18n, fakes.mottos:PROVIDERS]
```

```yaml title="scenarios/mottos.yaml"
scenario_id: 0x5004
name: mottos
mode: prep-seed
seed: 42

tables:
  - name: catalog.tb_continent
    count: 2
    providers: {name: motto}
```

The module is imported, never evaluated, so it must be importable by the `semis` command:
installed in the project's environment, or on `PYTHONPATH`. Once `semis.yaml` names it,
every `semis` command of the project imports it:

```bash
PYTHONPATH=. semis seeds scenarios/mottos.yaml -o db/seeds/mottos
head -n 3 db/seeds/mottos/001_prep_seed.tb_continent.sql
```

```sql
INSERT INTO prep_seed.tb_continent (id, identifier, name) VALUES
    ('02030405-5004-8001-8000-000000000001', 'purpose-5004-1', 'Customizable 4thgeneration support'),
    ('02030405-5004-8001-8000-000000000002', 'face-5004-2', 'Fully-configurable directional challenge');
```

A provider every project of a company shares belongs in a library of its own:
[Writing a provider library](/guides/provider-libraries/).

## Hierarchies

A table whose foreign key points at itself, a location inside a location, declares its
tree under `hierarchy:`. semis draws it breadth-first: the first `roots` rows carry `NULL`,
and every later row hangs from an earlier one, `fan_out` children to a parent.

```yaml
  - name: catalog.tb_location
    count: 20                        # levels of 2, 6 and 12 rows
    hierarchy:
      parent: fk_parent_location     # the nullable self-FK that builds the tree
      roots: 2
      fan_out: 3
      path: path                     # read-back only: an ltree of pk_* labels, 1.5.12
```

Row *k* after the roots points at row *(k − roots) // fan_out*. The parent column must be
nullable, since a root has no parent. Another self-referencing key on the table is
written `NULL`, and refused when it is NOT NULL.

A flat set of rows, every one a root, needs no tree: override the nullable self-FK `null`,
as any nullable foreign key may be. A hierarchy's own `parent:` overridden `null` is
refused: the two say opposite things. A `hierarchy:` whose `roots` are as many as its
`count` draws the same flat set and needs no `fan_out`.

```yaml
  - name: catalog.tb_location        # a flat set: no row has a parent
    count: 5
    overrides: {fk_parent_location: null}
```

A self-referencing table that neither declares a `hierarchy:` nor leaves each of its
self-referencing keys `null` is refused.

- In **prep-seed**, the table is one seed file, roots first, and a child's parent column
  carries its parent row's UUID. `path:` is refused: the project recalculates its paths
  after promotion.
- In **read-back**, each level is its own seed file, `NNN_<table>.L<n>.sql`, applied and
  read back before the next is drawn, and a child's parent column carries its parent's
  `pk_*`. `path:` names a nullable `ltree` column semis sets from those keys: the parent's
  path, a dot, and the row's own `pk_*`.

## Columns a trigger fills

A NOT NULL column without a default that a trigger fills is named under
`trusts_trigger:`, per table. semis leaves it out of the seed and does not refuse the
row. [The row contract](/concepts/row-contract/#trust-a-trigger) shows it at work.

## One run with another seed, locale or id

`--seed`, `--locale` and `--scenario-id` change a run without changing its file, and the
run says so. `locale:` is a [Faker locale](https://faker.readthedocs.io/en/master/locales.html),
`en_US` unless the scenario names another.

## Next steps

- [The scenario file](/reference/scenario-file/): every key, its type and its default
- [Shipped provider libraries](/reference/providers/): `i18n` and `organization`, rule by rule
- [Writing a provider library](/guides/provider-libraries/): providers other projects install
- [The two FK modes](/concepts/fk-modes/): what a foreign key carries in each mode
