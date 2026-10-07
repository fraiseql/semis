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
with `schema_pin.yaml` and `<scenario>.facts.json` beside them. No database is reached. A
read-back scenario is refused: its seeds are applied as they are written.

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

A second apply of the same scenario waits for the first: each holds an advisory lock on
the scenario's id until its transaction ends. Once the first commits, the second is
refused as already applied; once it rolls back, the second proceeds.

A scenario applies once, to a reset database. When a table the run writes into, or in
prep-seed its staging twin, already holds a row carrying the scenario's id, `apply` is
refused before anything is written (the check skips a table with no `id` column, or whose
`id` is not a uuid), `--dry-run` included, with `AlreadyAppliedError`: the
message names the first table found, and the hint the `TRUNCATE … RESTART IDENTITY`
that resets every table of the run, each name quoted. It has no `CASCADE`: PostgreSQL
refuses it when a table outside the run has a foreign key into one of them. See
[Determinism](/concepts/determinism/#a-scenario-applies-once).

| Argument or option | Takes | Description |
|---|---|---|
| `SCENARIO` | a file | The scenario's YAML file. Required. |
| `--output`, `-o` | a directory | The directory the seed files are written to. Required, except with `--dry-run`. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The database to apply to. See [the database URL](#the-database-url). |
| `--format` | `insert` or `copy` | The writer. Default: the mode's own, `insert` for prep-seed, `copy` for read-back. |
| `--dry-run` | | Apply every row, then roll back: nothing is kept. |
| `--no-pin` | | Skip the schema pin check for this run, and say so. |
| `--verbose`, `-v` | | Name each seed file's format and columns. |
| `--scenario-id` | hex, `0x5001` | The scenario id the UUIDs carry, for this run. |
| `--seed` | an integer | The Faker seed, for this run. |
| `--locale` | a locale | The Faker locale, for this run. |

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

| Argument or option | Takes | Description |
|---|---|---|
| `SCENARIO` | a file | The scenario's YAML file. Required. |
| `--config`, `-c` | a file | The project file. Default: `./semis.yaml`. |
| `--database-url`, `-d` | a URL | The database a `database:` schema is read from. |
| `--no-pin` | | Skip the schema pin check, and say so. |

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
and the command exits 1. A `schema_pin.yaml` or `<scenario>.facts.json` a run wrote there
is not listed; any other YAML file that is not a scenario, or a scenario whose `name` or
`scenario_id` is malformed, is refused naming the file.

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
