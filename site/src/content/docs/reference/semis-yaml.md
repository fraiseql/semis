---
title: semis.yaml
description: Every key of the project file, which names the schema source, the table codes, the scenarios directory, the providers and the staging schema
---

`semis.yaml` names, once for a project, what its scenarios do not: where the schema is
read from, the code each table's UUIDs carry, and where the scenarios are. Every command
reads `./semis.yaml`, or the file `--config` names. Paths in it are relative to the
directory that holds it. An unknown key is refused, naming the keys it takes.

```yaml
# semis.yaml
schema:
  env: development          # a confiture environment; or  ddl: db/schema/  or
                            #   database: {schemas: [catalog]}
scenarios: scenarios/
table_codes:                # or a path to a YAML file of them
  catalog.tb_continent: 0x02030405
  catalog.tb_country: 0x03040506
providers: [i18n, organization, myproject.fake:PROVIDERS]   # optional; see Providers
prep_seed:
  prep_seed_schema: prep_seed   # where prep-seed scenarios write; the default
  schema_dir: db/0_schema       # the tree holding the resolvers; default: a ddl: directory
  catalog_schema: catalog       # optional: a final table's fallback schema
```

## `schema:`

Required. Where the schema is read from, through confiture: exactly one of `ddl:`, `env:`
and `database:`.

| Key | Takes | Reads |
|---|---|---|
| `ddl` | a file or a directory | the DDL in it, with no project and no database |
| `env` | a confiture environment's name | that environment's build, as `confiture build` assembles it |
| `project_dir` | a directory, with `env:` only | the confiture project the environment belongs to; default: the directory of `semis.yaml` |
| `database` | `{schemas: [catalog, …]}` | the live database at [the database URL](/reference/cli/#the-database-url), only the schemas listed |

A schema read from `ddl:` or `env:` takes a `ddl` [schema pin](/concepts/schema-pins/), and
one read from `database:` a `live` pin. The two are not comparable. See
[confiture](https://fraiseql.dev/confiture/) for environments and builds.

## `table_codes:`

Required. A mapping of each schema-qualified table name to its code, an integer written in
hex, or the path of a YAML file holding that mapping. A code fits in 32 bits. A bare
table name, a code that is not an integer, and two tables with one code are refused.
Every table a scenario names needs a code: it is the first group of each UUID.

## `scenarios:`

The directory of the project's scenario files, read recursively for `*.yaml`. Default:
`scenarios`. `semis list-scenarios`, `semis init-scenario` and `semis decode-uuid` read it.

## `providers:`

A list of the provider libraries and providers the project enables:

- `i18n` or `organization`, the [shipped libraries](/reference/providers/);
- the name of an installed library, registered under the `fraiseql_semis.providers`
  entry point: see [Writing a provider library](/guides/provider-libraries/);
- `module:attribute`, a project's own: the attribute is a `Library`, or a mapping of
  provider names to providers. The module is imported, so it is installed or on
  `PYTHONPATH`.

An enabled library draws every column one of its rules matches, in the order listed,
when the run fills it: a nullable column is drawn only when the scenario names it. A
provider name enabled twice is refused. Default: none.

## `prep_seed:`

Where prep-seed scenarios write, and where confiture's levels find the resolvers.

| Key | Takes | Default |
|---|---|---|
| `prep_seed_schema` | a schema name | `prep_seed`: the staging schema the twins are in |
| `schema_dir` | a directory | the `ddl:` directory, when the schema is read from one: the tree holding the resolvers, for `semis validate-seeds` |
| `catalog_schema` | a schema name | none: a final table's fallback schema, passed to confiture only when named |

## `translations:`

How the project's translation tables are named, and the columns they use. Every key is
optional, and a project whose schema follows the defaults writes no block.

```yaml
translations:
  table: '{schema}.tl_{entity}'          # tb_category → tl_category, same schema
  label_column: label
  locale_fk: fk_locale
  locale_table: public.tb_locale (code)  # optional: read from locale_fk's foreign key
```

| Key | Takes | Default |
|---|---|---|
| `table` | a template naming an entity's translation table, from `{entity}`, the entity's name without its `tb_` prefix, and `{schema}`, its schema | `{schema}.tl_{entity}`: `catalog.tb_category` → `catalog.tl_category`; a template with no `.` is in the entity's schema |
| `label_column` | a column name | `label` |
| `locale_fk` | a column name: the translation table's key to the locale table | `fk_locale` |
| `locale_table` | `schema.table (column)`, the locale table and the column a locale is found by, as confiture writes it | the table `locale_fk`'s foreign key names, found by `code` |

`locale_table:` names the code column, and must be the table `locale_fk`'s foreign key
names: a run points the key at no other. The rule is configuration, not a schema fact,
and is not pinned: a rule that makes a scenario write another table, or find a locale by
another column, moves the pin, but one switching `label_column` or `locale_fk` between two
columns the translation table already has changes the rows and not the pin, and shows in
review as the `semis.yaml` diff. semis reads no `db/project.yaml`: the same keys, written
here.

A scenario's `translations: true` writes into the table the template names; a table a
scenario names under `translations:` is written as named. When the template names a
table the schema lacks, `translations: true` is refused, naming the rule. A template
without `{entity}`, with another placeholder, or with `{schema}` after its dot is refused, as is a value that is not a string, or a `locale_table` not written
`schema.table (column)`.

## From Python

`semis.yaml` loads into `fraiseql_semis.Project`, and a tool that drives semis from Python
builds a `Project` directly, with no file. See the [Python API](/reference/python-api/).

## Next steps

- [The scenario file](/reference/scenario-file/): what each scenario adds
- [The semis command](/reference/cli/): every command that reads this file
- [Getting started](/getting-started/): a project file, written step by step
