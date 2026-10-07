---
title: Exit Codes and Errors
description: What each semis command returns, the errors semis raises with their codes, and the confiture errors it passes through
---

A refusal is printed to stderr, its message then its hint, and becomes the command's exit
code. semis' own refusals exit 1. confiture's errors pass through unwrapped and keep
confiture's exit code and hint. Anything else is a bug, and keeps its traceback.

## Exit codes

| Code | Means |
|---|---|
| `0` | The command did what it says. `validate-seeds` with findings of `WARNING` or `INFO` only exits 0. |
| `1` | semis refused: a scenario, a project, a row, a pin, a scenario already applied, a database it cannot reach or whose schema is not built. Also `semis validate-seeds` with a `CRITICAL` or `ERROR` finding, and `semis list-scenarios` when two files share a scenario id. |
| `2` | The command line is wrong: an unknown command or option, a missing argument, `--output` missing where seeds are written, a UUID `decode-uuid` is given that semis did not encode. |
| `4` | confiture refused the schema: a table or column not in it, a foreign-key cycle. |
| `5` | confiture refused a seed or a configuration: a seed file PostgreSQL rejects on apply, a database URL that is not one, two database URLs given at once. |

## semis' errors

Each is a `SemisError`, shaped as confiture's errors are: a message, an error code, an
exit code, and a hint. The message names what was refused, the table and the column
where there is one, and the hint what to do about it.

| Error | Code | Raised for |
|---|---|---|
| `ScenarioError` | `SEMIS_SCENARIO_001` | a scenario that does not load or whose name is not a file name, declares no mode or one that is not one, names a table without its schema, one the schema lacks, or one twice, leaves a self-referencing table without its hierarchy or a hierarchy's parent to a trigger, gives a hierarchy more rows than roots and no `fan_out`, names under `fill:` a column it may not leave `NULL`, names under `existing:` a table it writes, one read-back cannot point a key at, or any in prep-seed, keeps a pin 0.1.0 wrote or a facts file that is missing or not the one its pin digested, or writes a prep-seed table whose staging twin is missing |
| `ProjectError` | `SEMIS_PROJECT_001` | a `semis.yaml` that does not load, a providers module that does not import, a command run with no project |
| `RowContractError` | `SEMIS_ROWS_001` | a row refused by [the row contract](/concepts/row-contract/), naming the table, the column and the fact it failed |
| `ResolutionError` | `SEMIS_RESOLVE_001` | a foreign key with no parent row in the run; an `existing:` table that holds no rows, or no row for a value its `where:` names |
| `CodeRegistryError` | `SEMIS_CODES_001` | a table with no code, a code that does not fit in 32 bits, two tables with one code |
| `PinError` | `SEMIS_PIN_001` | a scenario replayed against a schema whose facts no longer match its pin, naming each table and column that moved |
| `IncomparablePinError` | `SEMIS_PIN_002` | a pin of one source kind, `ddl` or `live`, replayed against the other |
| `AlreadyAppliedError` | `SEMIS_APPLY_001` | a scenario applied to a database that already holds its rows, refused before anything is written, naming the first table found and the reset to run |
| `UnreachableDatabaseError` | `SEMIS_DATABASE_001` | a database URL nothing answers at, named by host, port and database, or one that is malformed |
| `SchemaNotBuiltError` | `SEMIS_DATABASE_002` | a database that holds no table the run reads, because its schema is not built there, naming the first such table |

From Python, catch `fraiseql_semis.SemisError` for all of them.

## confiture's errors

semis reads schemas and writes, applies and validates seeds through
[confiture](https://fraiseql.dev/confiture/), and passes its errors through as they are:
re-wrapping them would hide their code and hint.

| Error | Exit code | Raised for |
|---|---|---|
| `SchemaError` | 4 | a schema confiture cannot read, or a name not in it |
| `DependencyCycleError` | 4 | foreign keys that form a cycle; the hint suggests a `DEFERRABLE` key |
| `SeedError` | 5 | a seed file that cannot be written or applied: a duplicate key, an identity column written |
| `ConfigurationError` | 5 | a malformed `--database-url`, two explicit database URLs, a missing environment file |

## Next steps

- [The semis command](/reference/cli/): every command and its options
- [semis in CI](/guides/ci/): a failing step and its code
- [The row contract](/concepts/row-contract/): the refusal most runs meet first
