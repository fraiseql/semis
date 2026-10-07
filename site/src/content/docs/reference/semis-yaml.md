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

## From Python

`semis.yaml` loads into `fraiseql_semis.Project`, and a tool that drives semis from Python
builds a `Project` directly, with no file. See the [Python API](/reference/python-api/).

## Next steps

- [The scenario file](/reference/scenario-file/): what each scenario adds
- [The semis command](/reference/cli/): every command that reads this file
- [Getting started](/getting-started/): a project file, written step by step
