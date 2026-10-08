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
| `seed` | a whole number | none: without a seed, the values differ from run to run |
| `tables` | a list of tables, at least one | required |
| `existing` | a list of tables the run takes parents from and does not write, read-back only | none. See [existing table keys](#existing-table-keys) |

A value of the wrong type is refused when the scenario loads, naming the scenario and the
key, and so is a key that is not a string.

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
| `fill` | a list of nullable columns to draw rather than write `NULL`, or `all` for every one | none: a nullable column nobody names is `NULL` |
| `trusts_trigger` | a list of writable columns a trigger fills, left out of the seed: a NOT NULL one is not refused for its absence | none |
| `hierarchy` | the tree of a table whose foreign key points at itself | none, and required for such a table unless its self-referencing key is overridden `null`, for a flat set, or trusted to a trigger |
| `copies` | a mapping of a column to `<foreign key>.<parent column>`: the column holds that column's value in the row the key points at | none. See [copied columns](#copied-columns) |

A list override with more or fewer values than `count` is refused, as is a column both
overridden and trusted, and a provider no project or library registers. See
[Writing scenarios](/guides/scenarios/).

## Which columns are drawn

A NOT NULL column is always drawn, unless it has a default, which PostgreSQL applies. A
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

`fill: all` draws every nullable column of the table, as 0.1.0 did. A `fill:` entry that
names no nullable column semis would write `NULL` is refused, naming the table, the
column and why: unknown or not writable, NOT NULL, a foreign key, a column with a
default, the natural id or the slug, a column trusted to a trigger, or the hierarchy's
`path`, which read-back fills from the keys.

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
as at a parent the run generated. Read-back only: prep-seed refuses it, since a child
carries its parent's UUID, and rows semis did not write have none it can know.

| Key | Takes | Default |
|---|---|---|
| `name` | a schema-qualified table | required |
| `where` | `{identifier: [value, …]}`: the rows whose identifier is one of the values, in that order | every row, ordered by its surrogate key |

```yaml
existing:
  - name: shop.tb_category            # every row, round-robin, in pk_* order
  - name: shop.tb_country
    where: {identifier: [fr, de, es]} # these rows, in this order
```

A table listed both under `tables:` and `existing:`, or twice, is refused, as is a
`where:` naming a column other than `identifier`. When the run starts, a table with no
rows, or a `where:` value no row matches, is refused, naming the table; so is a table
with no surrogate key. See [the two FK modes](/concepts/fk-modes/#parents-already-in-the-database).

## Next steps

- [Writing scenarios](/guides/scenarios/): every key at work
- [semis.yaml](/reference/semis-yaml/): the project's half
- [The semis command](/reference/cli/): `--seed`, `--locale` and `--scenario-id` for one run
