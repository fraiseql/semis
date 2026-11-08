---
title: The semis Command
description: Every semis command, its arguments and its options, and where a command finds its project and its database
---

`semis` runs inside a project: it reads `./semis.yaml`, or the file `--config` names, for
the schema, the table codes and the scenarios directory. `semis --help` and
`semis <command> --help` print what this page lists.

| Command | Does | Reaches a database |
|---|---|---|
| [`semis seeds`](#semis-seeds) | writes a prep-seed scenario's seed files | no |
| [`semis apply`](#semis-apply) | writes a scenario's seeds and applies them, in one transaction | yes |
| [`semis coverage`](#semis-coverage) | counts the rows and tenants of each view a scenario's tables reach | yes, read-only |
| [`semis generate`](#semis-generate) | runs a scenario in its mode: `seeds` for prep-seed, `apply` for read-back | read-back only |
| [`semis table`](#semis-table) | runs one table, as a scenario of that table alone | read-back only |
| [`semis validate`](#semis-validate) | checks a scenario against the schema and its pin, drawing no rows | for a `database:` schema |
| [`semis validate-seeds`](#semis-validate-seeds) | judges prep-seed seeds at confiture's five levels | levels 4 and 5 |
| [`semis list-scenarios`](#semis-list-scenarios) | lists the project's scenarios | no |
| [`semis init-scenario`](#semis-init-scenario) | writes a new scenario with the next free id | no |
| [`semis decode-uuid`](#semis-decode-uuid) | prints the fields a UUID carries | no |

## The database URL

A command that connects finds its database as confiture does:

1. `--database-url`, or `-d`, always;
2. for a project whose schema is read from `env:`, that confiture environment's
   `database_url`. A `CONFITURE_DATABASE_URL` set as well is refused: two explicit
   sources are never reconciled;
3. otherwise `CONFITURE_DATABASE_URL`;
4. otherwise `DATABASE_URL`, for a command that only reads.

A command that writes, `apply`, `generate` and `table` in read-back, and `validate-seeds`
at levels 4 and 5, refuses the ambient `DATABASE_URL` alone. A URL's password is masked in
whatever a refusal prints.

## semis seeds

`semis seeds [OPTIONS] SCENARIO` writes a prep-seed scenario's seed files, one per table,
and nothing else: a scenario's pin is [`semis pin`](#semis-pin)'s. No database is
reached. A read-back scenario is refused: its seeds are applied as they are written.

| Argument or option | Takes | Description |
|---|---|---|
| `SCENARIO` | a file | The scenario's YAML file. Required. |
| `--output`, `-o` | a directory | The directory the seed files are written to. Required, except with `--dry-run`. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The PostgreSQL URL, for a project whose schema is read from a database. |
| `--format` | `insert` or `copy` | The writer. Default: the mode's own, `insert` for prep-seed. |
| `--dry-run` | | Generate and check every row, then write nothing. |
| `--no-pin` | | Skip the schema pin check for this run, and say so. |
| `--verbose`, `-v` | | Name each seed file's format and columns. |
| `--scenario-id` | hex, `0x5001` | The scenario id the UUIDs carry, for this run. |
| `--seed` | an integer | The Faker seed, for this run. |
| `--locale` | a locale | The Faker locale, for this run. |

## semis apply

`semis apply [OPTIONS] SCENARIO` writes a scenario's seeds and applies them, in one
transaction committed at the end. A prep-seed scenario writes every file, then applies
them in order, into the staging twins. A read-back scenario applies each table as it is
written, and learns its keys before the next. With `--dry-run`, the whole run is applied
and rolled back.

The seed files are kept in `--output` when it is given. Without it, they are written to a
directory deleted before the command returns, so nothing is left on disk, and each line
names the table it applied, `applied catalog.tb_country  6 rows`, rather than a file.

A second apply of the same scenario waits for the first: each holds an advisory lock on
the scenario's id until its transaction ends. Once the first commits, the second is
refused as already applied; once it rolls back, the second proceeds.

A scenario applies once, to a reset database. When a table the run writes into, or in
prep-seed its staging twin, already holds a row carrying the scenario's id, `apply` is
refused before anything is written (the check skips a table with no `id` column, or whose
`id` is not a uuid), `--dry-run` included, with `AlreadyAppliedError`: the
message names the first table found, and the hint `semis apply --reset` and
[`semis reset`](#semis-reset). See
[Determinism](/concepts/determinism/#a-scenario-applies-once).

With `--reset`, the scenario's rows are deleted first, as `semis reset` deletes them, and
the scenario applied, in the same transaction and under the same lock: a failed apply
leaves the first run's rows in place, and on a database that holds none of them it is
`apply`. Each table's reset line is printed before the apply's.

| Argument or option | Takes | Description |
|---|---|---|
| `SCENARIO` | a file | The scenario's YAML file. Required. |
| `--output`, `-o` | a directory | The directory the seed files are kept in. Default: none, and no file is kept. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The database to apply to. See [the database URL](#the-database-url). |
| `--format` | `insert` or `copy` | The writer. Default: the mode's own, `insert` for prep-seed, `copy` for read-back. |
| `--dry-run` | | Apply every row, then roll back: nothing is kept. |
| `--reset` | | Delete the scenario's rows first, in the same transaction: a failed apply keeps them. |
| `--no-pin` | | Skip the schema pin check for this run, and say so. |
| `--verbose`, `-v` | | Name each seed file's format and columns. |
| `--scenario-id` | hex, `0x5001` | The scenario id the UUIDs carry, for this run. |
| `--seed` | an integer | The Faker seed, for this run. |
| `--locale` | a locale | The Faker locale, for this run. |

## semis reset

`semis reset [OPTIONS] SCENARIO` deletes the rows a scenario wrote, and only those, in one
transaction committed at the end: the rows of each table the run writes into, and in
prep-seed of each staging twin, whose `id` lies in the scenario's UUID range, as the
already-applied check reads it. Children go before parents. A table the run does not
write, an `existing:` one among them, is never touched, and neither is a row another
tool or scenario wrote. A table the reset leaves empty has its identity restarted, so a
table the scenario owns alone applies again exactly as it first did, keys and seed files
alike; a table that keeps other rows keeps its sequence. One line per table says how many
rows went, how many other rows it kept, and whether its identity was restarted:

```text
deleted 6 rows from shop.tb_order; identity restarted
deleted 2 rows from shop.tb_customer; 3 other rows kept, identity not restarted
committed
```

A serial column's sequence is not restarted, only an identity column's. It holds the scenario's advisory lock, as `apply` does. With `--dry-run`,
the rows are deleted and the transaction rolled back.

Nothing outside the scenario may point at its rows. Before a row is deleted, every
foreign key into the run's tables is asked, from any schema and whatever its
`ON DELETE`, whether a row the scenario did not write points at one it did: a row of a
table outside the run, or one another tool wrote into a run table. One does, and the
reset is refused with `ResetBlockedError`, naming that row's table, the key and how many
rows, and nothing is deleted. So no `CASCADE` or `SET NULL` ever reaches a row semis did
not write. The referencing tables are locked `SHARE` until the transaction ends, so no
row starts pointing in meanwhile.

| Argument or option | Takes | Description |
|---|---|---|
| `SCENARIO` | a file | The scenario's YAML file. Required. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The database to reset. See [the database URL](#the-database-url). |
| `--dry-run` | | Delete the scenario's rows, then roll back: nothing is lost. |

## semis coverage

`semis coverage [OPTIONS] SCENARIO` says, of a database the scenario was applied to, what
each view its rows reach holds: every view, materialized view and TVIEW that reads a
table the scenario writes, directly or through other views, with its rows and, when it
outputs the tenant column `semis.yaml`'s [`tenancy:`](/reference/semis-yaml/) names, how
many distinct tenants they belong to. It applies nothing: the database is read in a
read-only transaction, rolled back. One run reads one database, for one scenario.

```text
view                    rows  tenants
shop.mv_customer_stats     0        -  no rows
shop.tv_order             40        2
shop.v_customer           12        1  one tenant
shop.v_order_line        120        2
a view that reads a table only through a routine's body is not listed
```

A view that does not output the tenant column shows `-`. Beside a count, `no rows` says
the view is empty (a materialized view is filled only when it is refreshed), `one
tenant` that a test of tenant isolation over it has no other tenant's rows to hide, and
`no tenant` that every row's tenant column is `NULL`. confiture follows views,
materialized views and TVIEWs, and a TVIEW's `function_reads`, but not a routine's body:
a view reading a table only through a function it calls is not listed, and the report
says so.

With `--format json`, the report is a JSON list, one object per view, and that last line
goes to stderr:

```json
[
  {
    "view": "shop.v_customer",
    "kind": "view",
    "rows": 12,
    "tenants": 1,
    "single_tenant": true
  }
]
```

`kind` is `view`, `matview` or `tview`; `tenants` is `null` for a view that does not
output the tenant column. `coverage` exits 0 whatever it finds: some scenarios hold one
tenant on purpose. With `--fail-on-single-tenant`, it exits 1 when a view that outputs
the tenant column holds fewer than two tenants, and says how many on stderr; a project
with no `tenancy:` is refused that flag.

A view whose columns confiture cannot name, a `*` over a relation the schema does not
hold, is refused with confiture's `SchemaError`, exit 4, naming the view.

| Argument or option | Takes | Description |
|---|---|---|
| `SCENARIO` | a file | The scenario's YAML file: its tables are the ones a view must reach. Required. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The database to read. See [the database URL](#the-database-url). |
| `--format` | `table` or `json` | The report's form. Default: `table`. |
| `--fail-on-single-tenant` | | Exit 1 when a view that outputs the tenant column holds fewer than two tenants. |

## semis generate

`semis generate [OPTIONS] SCENARIO` runs a scenario in its declared mode: a prep-seed
scenario writes its seeds, as `semis seeds` does, and a read-back scenario applies them,
as `semis apply` does.

| Argument or option | Takes | Description |
|---|---|---|
| `SCENARIO` | a file | The scenario's YAML file. Required. |
| `--output`, `-o` | a directory | The directory the seed files are written to. Required, except with `--dry-run`. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The database a read-back scenario applies to. |
| `--format` | `insert` or `copy` | The writer. Default: the mode's own. |
| `--dry-run` | | Read-back applies every row and rolls back; prep-seed writes no file: nothing is kept. |
| `--no-pin` | | Skip the schema pin check for this run, and say so. |
| `--verbose`, `-v` | | Name each seed file's format and columns. |
| `--scenario-id` | hex, `0x5001` | The scenario id the UUIDs carry, for this run. |
| `--seed` | an integer | The Faker seed, for this run. |
| `--locale` | a locale | The Faker locale, for this run. |

## semis table

`semis table [OPTIONS] NAME` generates one table's rows, as a scenario of that table
alone, in the mode given. A foreign key's parent must be in the run, so it suits a table
without foreign keys; a table that has them runs in a scenario. The run is unpinned.

| Argument or option | Takes | Description |
|---|---|---|
| `NAME` | a table | The schema-qualified table, `catalog.tb_city`. Required. |
| `--count` | an integer, 0 or more | How many rows. Required. |
| `--mode` | `prep-seed` or `read-back` | How a child learns its parent's key. Required. |
| `--scenario-id` | hex, `0x5001` | The scenario id the UUIDs carry. Required. |
| `--output`, `-o` | a directory | The directory the seed files are written to. Required, except with `--dry-run`. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The database a read-back run applies to. |
| `--format` | `insert` or `copy` | The writer. Default: the mode's own. |
| `--dry-run` | | Read-back applies every row and rolls back; prep-seed writes no file: nothing is kept. |
| `--verbose`, `-v` | | Name each seed file's format and columns. |
| `--seed` | an integer | The Faker seed. |

## semis validate

`semis validate [OPTIONS] SCENARIO` checks a scenario against the schema and its pin,
drawing no rows: each table exists and has a code, each foreign key's parent is in the
run or left `NULL`, each hierarchy fits its table, each provider is registered, and in
prep-seed each staging twin exists.

After the pin's line, `validate`, `seeds` and `apply` print a line per table that leaves
nullable columns `NULL`, then one per translation table whose `coverage:` leaves labels
out, naming each locale that misses some:
`catalog.tl_category: 20 rows × 4 locales, en-GB 4 missing, de-DE 20 missing`.

| Argument or option | Takes | Description |
|---|---|---|
| `SCENARIO` | a file | The scenario's YAML file. Required. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The database a `database:` schema is read from. |
| `--no-pin` | | Skip the schema pin check, and say so. |

## semis pin

`semis pin [OPTIONS] SCENARIO` takes the scenario's schema pin from the project's
schema, and writes it to `<name>.pin.json` beside the scenario file: the digest, how the
schema was read, and the facts the digest was taken from. When the file already keeps
that pin, it says the file is unchanged and writes nothing, so its date does not churn.
When the schema moved, it prints what moved, as a refused run names it, then rewrites
the file. A pin file that does not read is replaced. The scenario file itself is never
written. See [Schema pins](/concepts/schema-pins/).

For a prep-seed scenario with `existing:` tables, the pin also records the rows it
points at: each row's `identifier`, when the table has one, and its UUID, read from the
database in a transaction rolled back. The database must be named on purpose, by
`--database-url`, `CONFITURE_DATABASE_URL` or the project's `env:`: an ambient
`DATABASE_URL` alone is refused, since the UUIDs become a reviewed file. A whole table's
rows keep the order they were first pinned in, and the rows it gains are appended.

With `--check`, nothing is written. It exits 1 when the seeds the pin gives would be
wrong: the schema moved, a pinned row is gone or its UUID changed, the pin lacks a
table the scenario reads, or the scenario was never pinned. It exits 0, saying so, when
a whole table only gained rows, and 0 when nothing would change. A CI job runs it to fail
on a pin nobody accepted.

| Argument or option | Takes | Description |
|---|---|---|
| `SCENARIO` | a file | The scenario's YAML file. Required. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The database a `database:` schema, and a prep-seed scenario's existing rows, are read from. See [the database URL](#the-database-url). |
| `--check` | | Write nothing; exit 1 if the pin would change, for CI. |

## semis validate-seeds

`semis validate-seeds [OPTIONS] [SCENARIO]` judges prep-seed seeds at confiture's five
levels: a scenario's, rehearsed into a temporary directory, or the files of `--seeds DIR`.
Give one of the two. Levels 4 and 5 load the seeds and run the resolvers on the database,
inside a transaction rolled back. Findings are printed most severe first; a `CRITICAL` or
an `ERROR` exits 1. See [Validating seeds](/guides/validating-seeds/).

| Argument or option | Takes | Description |
|---|---|---|
| `SCENARIO` | a file | The prep-seed scenario to rehearse and validate. |
| `--seeds` | a directory | Validate the seed files in this directory instead of a scenario's. |
| `--max-level` | 1 to 5 | The last level run. Default: 5 with a database URL, else 3, and said. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The database levels 4 and 5 run on. |
| `--no-pin` | | Skip the scenario's schema pin check, and say so. |

## semis list-scenarios

`semis list-scenarios [OPTIONS]` lists the scenario files under the project's `scenarios:`
directory: id, mode, name and file, by id. Two files that use one id are named on stderr,
and the command exits 1. A scenario's `<name>.pin.json` is not listed; a YAML file that is
not a scenario, or a scenario whose `name` or `scenario_id` is malformed, is refused naming
the file.

| Argument or option | Takes | Description |
|---|---|---|
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |

## semis init-scenario

`semis init-scenario [OPTIONS] NAME` writes `NAME.yaml` into the scenarios directory: every
table `semis.yaml` gives a code, ten rows each, with the next free scenario id, starting
at `0x5001`. An existing file is never overwritten.

| Argument or option | Takes | Description |
|---|---|---|
| `NAME` | letters, digits, `.`, `-` and `_` | The new scenario's name, and its file's. Required. |
| `--mode` | `prep-seed` or `read-back` | The scenario's mode. Required. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |

## semis decode-uuid

`semis decode-uuid [OPTIONS] VALUE` prints the table code, scenario id, version and
sequence a UUID carries. Inside a project, it names the table and the scenario too; when
the scenarios directory does not read, it says why on stderr and prints the fields
without the scenario's name. See [the semantic UUID](/concepts/semantic-uuid/).

| Argument or option | Takes | Description |
|---|---|---|
| `VALUE` | a UUID | A UUID semis encoded. Required. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`, when it exists. |

## Next steps

- [semis.yaml](/reference/semis-yaml/): the project file every command reads
- [Exit codes and errors](/reference/exit-codes/): what each command returns
- [Getting started](/getting-started/): the commands, in order
