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
| `name` | letters, digits, `.`, `-` and `_` | required: how commands and refusals name the scenario, and the name of the facts file its pin keeps |
| `description` | a string | empty |
| `mode` | `prep-seed` or `read-back` | required: see [the two FK modes](/concepts/fk-modes/) |
| `locale` | one of Faker's locales | `en_US` |
| `seed` | a whole number | none: without a seed, the values differ from run to run |
| `tables` | a list of tables, at least one | required |
| `existing` | a list of tables the run takes parents from and does not write, read-back only | none. See [existing table keys](#existing-table-keys) |
| `schema_pin` | the block a run writes to `schema_pin.yaml` | none: the scenario runs unpinned, and says so. See [schema pins](/concepts/schema-pins/) |

A value of the wrong type is refused when the scenario loads, naming the scenario and the
key, and so is a key that is not a string.

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

A list override with more or fewer values than `count` is refused, as is a column both
overridden and trusted, and a provider no project or library registers. See
[Writing scenarios](/guides/scenarios/).

## Which columns are drawn

A NOT NULL column is always drawn, unless it has a default, which PostgreSQL applies. A
**nullable** column that is not a foreign key is written `NULL`, unless the scenario names
it: by an override, by a `providers:` entry for it, or under `fill:`. A provider library's rule
matching the column does not name it: a library says how a column is filled when it is,
not whether. A nullable foreign key gets a parent when the run has one, and is written
`NULL` when overridden `null`.

The change is never silent: `semis validate`, `semis seeds` and `semis apply` print a line
per table naming the columns the run leaves `NULL`:

```text
shop.tb_customer leaves deleted_at, created_by NULL; fill: draws them
```

`fill: all` draws every nullable column of the table, as 0.1.0 did. A `fill:` entry that
names no nullable column semis would write `NULL` is refused, naming the table, the
column and why: unknown or not writable, NOT NULL, a foreign key, a column with a
default, the natural id or the slug, a column trusted to a trigger, or the hierarchy's
`path`, which read-back fills from the keys.

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
