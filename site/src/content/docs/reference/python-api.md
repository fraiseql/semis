---
title: Python API
description: The classes and functions fraiseql_semis exports for driving semis from Python, from reading a schema to applying seeds
---

Everything the `semis` command does is a call on the `fraiseql_semis` package, and a tool
that drives semis from Python makes the same calls. Every name on this page is imported
from `fraiseql_semis`.

## The chain, end to end

Read a schema, generate rows, write a seed, apply it:

```python
from pathlib import Path

from fraiseql_semis import FakeDataGenerator, SchemaFacts, TableCodes, seeds

database_url = "postgresql:///myproject_dev"
codes = TableCodes({"catalog.tb_continent": 0x02030405})

# the project's DDL tree; SchemaFacts.from_env("development", table_codes=codes) reads
# a confiture environment's build, and .from_database(url, …) a live database
facts = SchemaFacts.from_source(Path("db/schema"), table_codes=codes)
gen   = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)

rows = gen.generate_rows(
    "catalog.tb_continent",
    count=3,
    overrides={"identifier": lambda i: f"cont-{i + 1}", "name": lambda i: f"Continent {i + 1}"},
)
seed = seeds.write(
    "db/seeds/010_tb_continent.sql", "catalog.tb_continent", rows, facts=facts, mode="read-back"
)
seeds.apply(database_url, [seed.path])
```

## Schema facts and table codes

`SchemaFacts` is the schema as semis reads it, through confiture. It is built three ways,
each with the project's `TableCodes`:

```python
SchemaFacts.from_source(source, *, table_codes)            # DDL text, a Path, or a sequence
SchemaFacts.from_env(env, *, project_dir=None, table_codes)  # the project's build
SchemaFacts.from_database(database, *, schemas, table_codes) # a live database
```

`from_source` reads DDL text, a file or a directory, with no project and no database:
a DDL string is a schema, which makes a generator testable without PostgreSQL.
`from_source` and `from_env` take a `ddl` [pin](/concepts/schema-pins/), `from_database` a
`live` one.

```python
TableCodes(mapping)
```

`mapping` maps each schema-qualified table name to its 32-bit code.

## Generating rows

```python
FakeDataGenerator(facts, scenario_id, *, seed=None, locale='en_US', providers=None, identifier_column='identifier')
FakeDataGenerator.generate_rows(table, count, *, trusted=frozenset(), overrides=None, fill=frozenset(), resolver=None)
```

`generate_rows` yields `count` rows for the qualified `table`, each a mapping of column to
value and each checked against [the row contract](/concepts/row-contract/). `overrides`
maps a column to a value, a list with one value per row, or a function of the row's
0-based index. A nullable column is written `NULL` unless it is overridden, has a
provider registered for it by name, or is named in `fill`; `fill="all"` draws every one.
`trusted` names the columns a trigger fills. `providers` is a
`CustomProviderRegistry`; a `resolver` gives foreign keys their values, and a scenario
run supplies both.

## Seed files

```python
seeds.write(path, table, rows, *, facts, mode, format=None)
seeds.apply(database, paths)
```

`seeds.write` writes `rows` into `table`'s seed file at `path`, in the writer of `mode`,
`"prep-seed"` or `"read-back"`, unless `format` names `"insert"` or `"copy"`. It returns
confiture's `SeedFile`, whose `path` `seeds.apply` takes. `seeds.apply` applies the files
at `paths`, in order: given a URL, in one transaction it commits; given a connection, in
the caller's transaction.

## Scenarios

```python
ScenarioManager(facts, *, providers=None, libraries=(), staging=None)
ScenarioManager.load(path, *, read_pin=True)
ScenarioManager.execute(scenario, out_dir, *, connection=None, format=None, no_pin=False)
ScenarioManager.apply(scenario, out_dir=None, *, connection, format=None, no_pin=False)
ScenarioManager.rehearse(scenario, *, connection=None, format=None, no_pin=False)
ScenarioManager.reset(scenario, *, connection)
ScenarioManager.pin(scenario, path, *, check=False)
ScenarioManager.check(scenario, *, no_pin=False)
ScenarioManager.validate(scenario, *, schema_dir, max_level=3, connection=None, catalog_schema=None, format=None, no_pin=False)
ScenarioManager.validate_seeds(seeds_dir, *, schema_dir, max_level=3, connection=None, catalog_schema=None)
```

`ScenarioManager` loads scenarios against one schema, with a project's own `providers`,
a mapping of names to providers, and its enabled `libraries`; a prep-seed scenario writes
into `staging`'s twins. `load` reads a scenario file, refusing a malformed one with
`ScenarioError`, and the pin `<name>.pin.json` beside it keeps, when there is one, as
the scenario's `pin`; with `read_pin=False` it loads it unpinned, whatever that file
holds.

- `execute` runs the scenario in its declared mode, writing one seed file per table to
  `out_dir`. A read-back scenario needs the `connection` its run applies on,
  whose transaction stays the caller's; prep-seed takes none.
- `apply` runs it and applies its seeds on `connection`, in the caller's transaction:
  read-back applies each table as it is written, prep-seed writes every file and then
  applies them. A database that already holds the scenario's rows is refused before
  anything is written, with `AlreadyAppliedError`, naming the reset. Without `out_dir`,
  the files are written to a directory deleted before it returns.
- `rehearse` runs it into a directory deleted before it returns: every row generated and
  checked, every file written, then discarded. Given a `connection`, the seeds are also
  applied, as `apply` does, and the caller rolls back.
- `reset` deletes the rows the scenario wrote, and only those, on `connection`, in the
  caller's transaction: each table the run writes into, and in prep-seed each staging
  twin, by the scenario's UUID range on its `id`, children first, and restarts the
  identity of a table it empties. It returns a `Deleted(table, rows, kept, restarted)`
  per table: the rows deleted, the other rows it still holds, and whether its identity
  was restarted, `None` for a table with none. A row the scenario did not write
  pointing at one it did is refused with `ResetBlockedError`. A table with no uuid `id` is refused with `ResetScopeError`.
- `pin` takes the scenario's schema pin from this schema into `<name>.pin.json` beside
  the scenario file at `path`, as `semis pin` does, and returns a `PinChange`: the `path`,
  the schema's `pin`, the `previous` one the file kept, what `changes` moved between
  them, whether the pin `changed`, and whether the file was `written`. An unchanged pin
  writes nothing, and with `check=True` nothing is written at all.
- `check` refuses what can be refused before a row is drawn: providers, the pin, the
  schema, each foreign key's parent and each hierarchy. It returns what the pin check did,
  then a line per table that leaves nullable columns `NULL`.
- `validate` rehearses a prep-seed scenario and judges exactly its seeds at confiture's
  levels 1 to `max_level`; `validate_seeds` judges a directory's. Levels 4 and 5 load the
  seeds on `connection`, in a savepoint confiture rolls back.

`execute`, `apply` and `rehearse` return a `Run`: its `seeds`, the `pin` of the schema it
read, which it writes nowhere, and its `notices`, the lines a run says, as `check`
returns them. `validate` returns a `Validation`: the `run`, confiture's `report`, and the
`seeds_dir` the rehearsal wrote to, deleted since, which a finding's path is relative to.

`semis apply` holds `readback.exclusive(database_url, scenario.id)` around its
transaction: an advisory lock on a connection of its own, so a second apply of one
scenario waits for the first, then finds its rows. `ScenarioManager` takes no lock on a
connection it is given; a caller applying or resetting on its own connection while
another may apply the same scenario holds `exclusive` around its transaction too, as
`semis reset` does.

```python
Scenario(id, name, mode, tables, locale='en_US', seed=None, description='', pin=None, existing=())
ExistingTable(name, identifiers=None)
Scenario.for_run(*, scenario_id=None, seed=None, locale=None)
```

A scenario built in Python is refused as a loaded one is. `tables` is a tuple of
`TableSpec(name, count, …)`, each schema-qualified table with its `overrides`,
`providers`, `fill`, `trusts_trigger`, `hierarchy` and `copies`, as [the scenario
file](/reference/scenario-file/) names them; `copies` maps a column to a
`Copied(key, column)`, the foreign key and the parent column it copies. `existing` is a tuple of `ExistingTable`, the
tables a read-back run takes parents from without writing them: every row, or those whose
`identifier` is one of `identifiers`, in that order. `for_run` returns the scenario with
one run's overrides, and a line saying each.

```python
from fraiseql_semis import ScenarioManager

manager = ScenarioManager(facts)            # the SchemaFacts above
run = manager.execute(manager.load("scenarios/minimal_seed.yaml"), Path("db/seeds/prep"))
print(*run.notices)    # scenario minimal_seed is unpinned: its schema is not checked
```

## A project

```python
Project.load(path)
Project.manager(*, database_url=None)
```

`semis.yaml` loads into a `Project`: its table codes, its schema source, its scenarios
directory, its providers and its staging schema. `Project.manager` reads the schema and
returns a `ScenarioManager` with the project's providers. A tool that already holds a
schema builds a `Project` directly, with no file.

## Providers

```python
Library(name, providers, rules=())
Rule(provider, names=frozenset(), types=frozenset(), min_length=0)
```

A provider is a function of the run's seeded `Faker` and the column's `ColumnFacts`,
returning a value. `TEXT_TYPES` is the set of text type families a rule matches with. See
[Writing a provider library](/guides/provider-libraries/).

## UUIDs

`SemanticUUIDGenerator.decode` returns the four fields a UUID carries, as `UUIDFields`:

```python
>>> from uuid import UUID
>>> from fraiseql_semis import SemanticUUIDGenerator
>>> SemanticUUIDGenerator(scenario_id=0x5001).decode(UUID("02030405-5001-8001-8000-000000000042"))
UUIDFields(table_code=33752069, scenario_id=20481, version=1, sequence=66)
```

## Errors

semis' own refusals are `SemisError` and its subclasses, exported from `fraiseql_semis`;
confiture's pass through unwrapped. See [Exit codes and errors](/reference/exit-codes/).

## Next steps

- [semis.yaml](/reference/semis-yaml/): what `Project.load` reads
- [The scenario file](/reference/scenario-file/): what `ScenarioManager.load` reads
- [The two FK modes](/concepts/fk-modes/): what `execute` and `apply` do in each mode
