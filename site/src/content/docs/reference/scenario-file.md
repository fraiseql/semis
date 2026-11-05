---
title: The Scenario File
description: Every key of a scenario file, of each table it lists, and of a table's hierarchy, with their types, defaults and refusals
---

A scenario is a YAML file under the project's `scenarios:` directory. It names its id, its
mode, its seed and its tables; the project's [semis.yaml](/reference/semis-yaml/) names the
schema and the codes. An unknown key is refused, naming the keys it takes. Nothing in a
scenario is evaluated: a provider is named, never written as code.

```yaml
# scenarios/minimal_seed.yaml
scenario_id: 0x5001
name: minimal_seed
description: Seven continents and the countries on them
mode: prep-seed          # required: prep-seed | read-back
locale: en_US
seed: 42

tables:
  - name: catalog.tb_continent
    count: 7
    overrides:
      name: [Africa, Antarctica, Asia, Europe, North America, Oceania, South America]
  - name: catalog.tb_country
    count: 50
    trusts_trigger: [created_by]      # a trigger fills it; do not refuse the row
```

## Scenario keys

| Key | Takes | Default |
|---|---|---|
| `scenario_id` | an integer from `0x0` to `0xffff`, written in hex and unquoted | required: the second group of each UUID; `semis list-scenarios` reports an id two files share |
| `name` | letters, digits, `.`, `-` and `_` | required: how commands and refusals name the scenario, and the name of its pin file, `<name>.pin.json` |
| `description` | a string | empty |
| `mode` | `prep-seed` or `read-back` | required: see [the two FK modes](/concepts/fk-modes/) |
| `locale` | one of Faker's locales | `en_US` |
| `locales` | a list of the locale table's codes, each a string or `{code: <code>, faker: <Faker locale>}` | none: a scenario with translated tables declares at least one |
| `pseudo_locale` | the locale table's code of a pseudo-locale, whose labels transform the first locale's | none |
| `coverage` | a mapping of a declared locale to a fraction from 0 to 1: the share of each translated table's rows given its label | none: every locale is 1 |
| `seed` | a whole number | none: without a seed, the values differ from run to run |
| `tables` | a list of tables, at least one | required |
| `existing` | a list of tables the run takes parents from and does not write | none. See [existing table keys](#existing-table-keys) |
| `tenants` | a list of the tenant column's values, each once, for a project with no tenant table in the run | none: the tenants are the rows the run writes into, or names under `existing:` in, `semis.yaml`'s [tenant table](/reference/semis-yaml/#tenancy), if any. See [several tenants](/guides/tenants/) |

A value of the wrong type is refused when the scenario loads, naming the scenario and the
key, and so is a key that is not a string.

`locale:` is the Faker locale of every table the scenario writes, entities included.
`locales:` is the locales translations are written in, independent of it: `locale:
en_US` with `locales: [fr-FR, en-GB]` draws entity names in US English and labels in
French and British English. Each code is as the locale table holds it, and its Faker
locale is the code itself, the code with `-` read as `_` and its region upper-cased
(`fr-fr` → `fr_FR`), or its bare language when Faker has one (`en`, `de`); a code naming
none of them, `fr` among them, is refused with the Faker locales of its language, and is
given its own as `{code: fr, faker: fr_FR}`. A code listed twice, more than 254 of them,
`locales:` with no table translated, and a translated table with no `locales:` are
refused.

`pseudo_locale:` names one more locale, by its code in the locale table, which is not a
Faker locale. Each translated row gets one more translation row in it, after the
declared locales', whose label is the first locale's label transformed: each letter a
marked one, 40% longer, bracketed, `Category` → `[Ḉḁṭḕḡṓṙẏ ···]`. Only the label is
transformed. A pseudo-locale with no `locales:`, one also under `locales:`, one that is not
a single code, and a label column under 3 characters, too short for the brackets, are
refused.

`coverage:` gives a declared locale, or the pseudo-locale, the share of each translated
table's rows that get its label: of `n` rows, exactly `⌊n × c⌋`, read from the decimal
as written, so `0.29` of 100 keeps 29. `0` and `1` are allowed, and a locale not named is
1. Which rows are left out does not depend on `seed:`. A mapping of anything else, a
locale the scenario does not declare, a value outside 0 to 1, `.nan`, `.inf`, a boolean
or a string, and `coverage:` with no `locales:` are refused. See
[Gaps on purpose](/guides/multilingual/#gaps-on-purpose).

A scenario's pin is not one of its keys: it is the file `<name>.pin.json` beside it,
which `semis pin` writes. A `schema_pin:` block, where semis 0.2.0 kept it, is refused
with the hint to run `semis pin`. See [Schema pins](/concepts/schema-pins/).

## Table keys

Each entry of `tables:` names one table, once. The run draws them in the order of their
foreign keys, whatever the order of the list.

| Key | Takes | Default |
|---|---|---|
| `name` | a schema-qualified table, with a code in `semis.yaml` | required |
| `count` | an integer, 0 or more | required |
| `overrides` | a mapping of a column to a value, a list with one value per row, or `null` for a nullable foreign key | none |
| `providers` | a mapping of a column to the name of a registered provider, `<library>.<provider>` or a project's own | none |
| `fill` | a list of columns to draw: nullable ones rather than write `NULL`, ones with a default rather than leave it to PostgreSQL; or `all`, every nullable one with no default | none: a nullable column nobody names is `NULL`, a default is PostgreSQL's |
| `trusts_trigger` | a list of writable columns a trigger fills, left out of the seed: a NOT NULL one is not refused for its absence | none |
| `hierarchy` | the tree of a table whose foreign key points at itself | none, and required for such a table unless its self-referencing key is overridden `null`, for a flat set, or trusted to a trigger |
| `translations` | the schema-qualified table each row's translations are written to, one row per locale of `locales:`, or `true` for the table `semis.yaml`'s [translations rule](/reference/semis-yaml/#translations) names | none |
| `copies` | a mapping of a column to `<foreign key>.<parent column>`: the column holds that column's value in the row the key points at | none. See [copied columns](#copied-columns) |
| `crosses_tenants` | a list of keys into tenant-scoped tables that point at a parent of any tenant, round-robin | none: with tenants, every such key points within its row's tenant. See [several tenants](/guides/tenants/) |

A list override with more or fewer values than `count` is refused, as is a column both
overridden and trusted, and a provider no project or library registers. See
[Writing scenarios](/guides/scenarios/).

## Which columns are drawn

A NOT NULL column is always drawn, unless it has a default, which PostgreSQL applies
unless `fill:` names the column. A
**nullable** column that is not a foreign key is written `NULL`, unless the scenario names
it: by an override, by a `providers:` entry for it, or under `fill:`. A provider library's rule
matching the column does not name it: a library says how a column is filled when it is,
not whether. A nullable foreign key gets a parent when the run has one, a table it
generates or, in read-back, one under `existing:`; otherwise it is written `NULL`, as it
is when overridden `null`. A NOT NULL key whose parent the run lacks is refused.

The change is never silent: `semis validate`, `semis seeds` and `semis apply` print a line
per table naming the columns the run leaves `NULL`:

```text
shop.tb_customer leaves deleted_at, created_by NULL; fill: draws them
```

A nullable foreign key whose parent the run lacks is named in the same line, in column
order, and the hint then says what gives each kind a value: `fill:` a value column, its
parent table under `tables:`, or in read-back `existing:`, a key:

```text
shop.tb_customer leaves fk_segment, deleted_at NULL; fill: draws the values, a parent under tables: or existing: points the keys
```

`fill: all` draws every nullable column of the table, but two kinds, which it leaves
`NULL` and names. The soft-delete columns, `deleted_at` and `deleted_by`, so no row it
writes arrives deleted; a `fill:` list, an override or a provider writes them:

```text
shop.tb_customer leaves deleted_at NULL; fill: all leaves soft-delete columns NULL: fill: [deleted_at] writes one
```

And a column of a type semis does not draw, which an override or a provider writes:

```text
shop.tb_customer leaves last_ip NULL; semis draws no inet: an override or a provider writes them
```

A `fill:` entry that
names no column semis would write `NULL` or leave to its default is refused, naming the
table, the column and why: unknown or not writable, NOT NULL with no default, a foreign
key, the natural id or the slug, a column trusted to a trigger, or the hierarchy's
`path`, which read-back fills from the keys. So is one of a type semis does not draw.

## Copied columns

`copies:` maps a column to `<foreign key>.<parent column>`, both columns of the scenario's
tables: the column holds the parent column's value in the row the foreign key points
at, in either mode: semis knows which parent row each child points at, and what it wrote
there.

```yaml
copies:
  tenant_id: fk_customer_org.id       # <this column>: <foreign key>.<parent column>
```

An entry of any other shape is refused, naming the table, with the shape to write. So is
a copy the run cannot make, before a row is drawn, naming the table, the column and why:

- into a column semis writes itself: the natural id, the slug, a foreign key, or the
  hierarchy's `path`; or into a column the table also overrides, gives a provider, lists
  under `fill:` or trusts to a trigger;
- through a column that is not a foreign key, a key to the table itself, or a key to a
  table the run does not write, `existing:` tables included: semis reads their keys, not
  their columns;
- of a parent column whose value semis does not know when the parent row is drawn: one
  left `NULL`, one with a default, one trusted to a trigger, or the hierarchy's `path`.
  The natural id, the slug, and a column semis draws, overrides or copies are known;
- between two types: a copy is not cast;
- into a NOT NULL column, through a key the run leaves `NULL`. Into a nullable column, a
  key left `NULL` copies `NULL`.

## Translation table keys

A table's `translations:` names its translation table. For each of the table's rows, in
order, the run writes one row per locale of `locales:`, in that order, its columns found
by name: the entity key is the translation table's one foreign key to the table; the
locale key is `fk_locale`, whose foreign key names the locale table; the label is `label`,
always drawn; the locale table finds a locale by its `code`. In read-back, each code's
key is read from the locale table before the first table is drawn; in prep-seed, each
code's UUID from the scenario's pin, which `semis pin -d` records. Every other column of
the translation table follows the rules of any table the run writes. A translation table
with a uuid `id` takes `entity sequence × 256 + slot`, the slot the locale's place in
`locales:`. See [Multilingual scenarios](/guides/multilingual/).

Refused, naming the table, before a row is drawn: a translation table the schema lacks,
or with no `fk_locale` or no `label`; an `fk_locale` that declares no foreign key; no
foreign key to the entity, or two; a locale table with no `code` column, with no
surrogate key in read-back or no uuid `id` in prep-seed; a translation table also under
`tables:` or `existing:`. Refused when the run starts, before a row is written, or by
`semis pin`: a declared code with no row in the locale table, or two. In prep-seed, a code
the pin does not record is refused, with the hint to run `semis pin -d`.

## Hierarchy keys

| Key | Takes | Default |
|---|---|---|
| `parent` | the nullable self-referencing foreign key that builds the tree, which semis draws: neither overridden `null` nor trusted to a trigger | required |
| `roots` | how many rows are roots, with a `NULL` parent | required |
| `fan_out` | how many children each parent row has | required when `count` is more than `roots`; a tree of roots alone has no parent rows |
| `path` | a nullable `ltree` column, set to the `pk_*` path of each row | none; read-back only, refused in prep-seed |

Rows are drawn breadth-first: row *k* after the roots points at row
*(k − roots) // fan_out*. See [hierarchies](/guides/scenarios/#hierarchies).

## Existing table keys

Each entry of `existing:` names a table whose rows are already in the database — reference
rows the schema's own DDL inserted — so a foreign key to it points at them, round-robin,
as at a parent the run generated. In read-back, their keys are read from the database. In
prep-seed, a child carries its parent's UUID, and the run takes the UUIDs the scenario's
pin recorded: run `semis pin` with `-d` naming the database they are read from, and a
prep-seed scenario whose pin records none is refused.

| Key | Takes | Default |
|---|---|---|
| `name` | a schema-qualified table | required |
| `where` | `{column: [value, …]}`: the rows whose column is one of the values, in that order; the column is `identifier`, or a unique `text` or `varchar` one | every row, ordered by its surrogate key |

```yaml
existing:
  - name: shop.tb_category            # every row, round-robin, in pk_* order
  - name: shop.tb_country
    where: {identifier: [fr, de, es]} # these rows, in this order
```

A table with no `identifier` is named by a unique text column, as `where: {email: […]}`.
Children go round-robin over the list as written, so a repeated value points at its row
again: `[ann, ann, bob]` gives `ann` two children of each three. A table listed both under `tables:` and
`existing:`, or twice, is refused, as is a `where:` naming two columns, a column the table
lacks, or one neither `identifier` nor a unique text column. When the run starts, a table with no
rows, a `where:` value no row matches, or one two rows share, is refused, naming the
table; so is a table with no surrogate key. See [the two FK modes](/concepts/fk-modes/#parents-already-in-the-database).

## Next steps

- [Writing scenarios](/guides/scenarios/): every key at work
- [semis.yaml](/reference/semis-yaml/): the project's half
- [The semis command](/reference/cli/): `--seed`, `--locale` and `--scenario-id` for one run
