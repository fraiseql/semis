# Changelog

All notable changes to fraiseql-semis are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-10-08

semis carries what a scenario did by hand on 0.2.0: a nullable key with no parent, a
column copied from its parent row, a reset that keeps other rows, and a re-pin in one
command. Two formats change: the pin leaves the scenario file, and a run writes no pin.

### Upgrading from 0.2.0

- **Move each pin with `semis pin`**: delete the scenario's `schema_pin:` block and the
  `<name>.facts.json` beside it, run `semis pin <scenario>`, and commit the
  `<name>.pin.json` it writes. Until then the scenario is refused, naming both.
- **Reset with semis**: replace a `TRUNCATE … RESTART IDENTITY` a refusal printed, or a
  dropped and rebuilt database, with `semis apply --reset`, or `semis reset` then
  `semis apply`. Either deletes the scenario's rows and keeps every other.
- **Drop the copies a run no longer needs**: `-o` on `semis apply`, a step copying
  `schema_pin.yaml` out of an output directory, a nullable key overridden `null` only
  because its parent is not in the run, and a list of UUIDs a `copies:` can derive.
- **From Python**: `Scenario.schema_pin` is `Scenario.pin`, `Run.pin_path` is gone, and
  a `Resolver` of a project's own adds `pointed_at`.

### Added

- **A column copies its parent row's value** with a table's `copies:`, as
  `tenant_id: fk_customer_org.id`: each row holds the value of `id` in the row its
  `fk_customer_org` points at, in both modes, so no UUID is computed by hand and no list
  is kept in step with another table's. A copy the run cannot make is refused before a
  row is drawn, naming the table, the column and why: a key it does not draw or that
  points at the table itself, an `existing:` table or one the run does not write; a
  parent column whose value semis does not know; two types; a NOT NULL column copying a
  key left `NULL`. `TableSpec.copies` maps a column to a `Copied(key, column)`.
- **`semis reset SCENARIO` deletes the rows the scenario wrote, and only those**: in
  each table its run writes into, and in prep-seed each staging twin, the rows whose
  `id` lies in the scenario's UUID range, children first, in one transaction behind the
  scenario's lock; `--dry-run` rolls back. Rows another tool or scenario wrote stay, and
  `existing:` tables are never touched. One line per table says what went and what was
  kept. A row the scenario did not write that points at one it did, from any schema and
  whatever its key's `ON DELETE`, blocks the reset with `ResetBlockedError`
  (`SEMIS_RESET_001`), naming its table and key: nothing is deleted, so no cascade
  reaches a row semis did not write. A table the reset leaves empty has its identity
  restarted, so a table the scenario owns alone applies again byte-identical; one that
  keeps other rows keeps its sequence, and says so. A table with no uuid `id` cannot be scoped, and is refused with
  `ResetScopeError` (`SEMIS_RESET_002`). From Python, `ScenarioManager.reset` returns a
  `Deleted(table, rows, kept, restarted)` per table.
- **`semis pin SCENARIO` writes the scenario's pin to `<name>.pin.json` beside it**: the
  digest, how the schema was read and the facts it was taken from, in one file to review
  as a diff. An unchanged pin writes nothing; a moved one prints what moved, then is
  rewritten; one that does not read is replaced. `--check` writes nothing and exits 1
  when the pin would change, for CI. From Python, `ScenarioManager.pin(scenario, path)`
  returns a `PinChange`.
- **`semis apply --reset`** deletes the scenario's rows as `semis reset` does, then
  applies it, in one transaction behind one lock: a failed apply leaves the first run's
  rows in place, and on a database holding none of them it is `apply`.

### Changed

- **Breaking: a scenario's pin is the file `<name>.pin.json` beside it**, read when the
  scenario loads and written only by `semis pin`; the scenario file holds no pin. A
  `schema_pin:` block, or the `<name>.facts.json` 0.2.0 kept beside the scenario, is
  refused with the hint to run `semis pin`. To upgrade, delete each scenario's
  `schema_pin:` block and its facts file, run `semis pin` on it, and commit the
  `<name>.pin.json` it writes. A moved schema's refusal names `semis pin` to accept the
  change. `Scenario`'s `schema_pin` is now `pin`, and `ScenarioManager.load` takes
  `read_pin=False` to load a scenario unpinned.
- **`semis apply` needs no `-o`**: without it, the seeds are applied and no file is kept,
  each line naming the table it applied; with it, they are kept there as before.
  `ScenarioManager.apply` takes `out_dir=None` alike. `semis seeds` still needs `-o`:
  its files are its product.
- **Breaking: a run writes its seeds and nothing else**: `semis seeds`, `generate` and
  `apply` no longer write `schema_pin.yaml` and `<name>.facts.json` beside them, and
  `Run.pin_path` is gone; `Run.pin` is still the pin of the schema the run read.
  `Validation.seeds_dir` names the rehearsal's directory a finding is relative to.
- **A nullable foreign key whose parent the run lacks is written `NULL`**, not refused:
  its parent table is not in the run, or is in it with `count: 0`, and is not under
  `existing:`. A NOT NULL key so placed is still refused, as is an `existing:` table
  with no rows. Overriding such a key `null` is no longer needed. The run names the key
  in its line of columns left `NULL`, in column order, and the hint says what gives each
  kind a value: `shop.tb_customer leaves fk_segment, deleted_at NULL; fill: draws the
  values, a parent under tables: or existing: points the keys`. A line naming only
  value columns reads as in 0.2.0.
- **A refused re-apply names `semis apply --reset` and `semis reset`**, where its hint
  printed a `TRUNCATE … RESTART IDENTITY` of the run's tables: that statement emptied
  them whole, rows the scenario never wrote included.
- **`Resolver` has a `pointed_at(column, *, table)`**: the parent row its last
  `value_for` pointed a key at, which a copy reads. A resolver of a project's own adds it.

## [0.2.0] - 2026-10-06

Resolves fraiseql/semis#1 to #5. A breaking release: every UUID semis writes changes, so
every format that changed is changed once, here.

### Upgrading from 0.1.0

- **Python 3.14 and fraiseql-confiture 1.30** are required, with typer 0.19, psycopg
  3.2.10 and PyYAML 6.0.1 or later; PostgreSQL 16 or later is supported.
- **Every semantic UUID changes**, to an RFC 9562 version 8 UUID: re-generate committed
  seeds, and reset any database 0.1.0 seeded before applying a scenario again.
- **Nullable columns are written `NULL` unless named**: list them under a table's
  `fill:`, or write `fill: all` for 0.1.0's rows.
- **Pins must be re-taken**: delete each scenario's `schema_pin:` block, run it with
  `-o <dir>`, then paste the `schema_pin.yaml` written beside its seeds in the block's
  place and copy the `<scenario>.facts.json` beside the scenario file.
- **A scenario's name is a file name**: rename a scenario whose `name:` holds anything
  but letters, digits, `.`, `-` and `_`.
- **A scenario applies once, to a reset database**: a second `semis apply` is refused,
  with the `TRUNCATE … RESTART IDENTITY` to run. It has no `CASCADE`: when a table
  outside the run references one of its tables, PostgreSQL refuses it, and that table's
  rows are yours to empty or keep.

### Added

- **Read-back takes parents from rows already in the database** (fraiseql/semis#2): a
  scenario's `existing:` lists tables the run reads and does not write — reference rows
  the schema's DDL inserted — each optionally narrowed and ordered by
  `where: {identifier: [...]}`; a foreign key to one points at its rows, round-robin.
  Their keys are read before the first table is drawn; an existing table needs no
  table code, and the pin covers its keys. A table with no rows, or a `where:` value
  no row matches, is refused naming the table. Prep-seed refuses `existing:`.
- **A flat set of rows in a self-referencing table is one line**: a nullable self-FK
  overridden `null` writes every row with no parent, with no `hierarchy:`, in both FK
  modes (fraiseql/semis#3); so does a self-FK trusted to a trigger, which fills it. A
  table's other self-FKs still need a hierarchy or their own `null`; a NOT NULL self-FK
  left `null` is still refused, and so is a hierarchy's own `parent:` overridden `null`,
  which would draw a tree of nothing but roots.
- **`fan_out` is optional for a tree of roots alone**: a `hierarchy:` whose `roots` are
  as many as its `count` loads without one; with more rows than roots, a missing
  `fan_out` is refused, saying how many rows have a parent.
- **Two applies of one scenario never overlap**: `semis apply` (and `--dry-run`, and
  `generate` or `table` in read-back) holds an advisory lock on the scenario's id, on a
  connection of its own, from before the already-applied check until its transaction
  ends. A second apply waits, then is refused once the first commits, or proceeds once
  it rolls back. `fraiseql_semis.readback.exclusive` takes the same lock for a Python
  caller applying on its own connection.
- **The package is typed**: it ships `py.typed` (PEP 561), so a type checker reads
  semis' annotations in a project that imports it, and says so with the `Typing :: Typed`
  classifier.

### Changed

- **Breaking: semis requires Python 3.14 and fraiseql-confiture 1.30**, where it required
  Python 3.11 and confiture 1.27. confiture 1.30 requires Python 3.14, and semis follows
  it. The other floors are the lowest releases that install on 3.14 and pass semis'
  tests, which CI now runs against them: typer 0.19 (0.12 to 0.18 fail on import),
  psycopg 3.2.10 and PyYAML 6.0.1 (older ones have no 3.14 wheel), and Faker 24.
- **PostgreSQL 16 or later**, as the README and the getting-started page now say; CI
  runs the integration suite on PostgreSQL 16 and 18, where it ran 17 alone.
- **Breaking: a semantic UUID is an RFC 9562 version 8 UUID**, so validators that accept
  only standard UUIDs, such as Zod 4's `z.uuid()` and the npm `uuid` package's
  `validate()` from 10.0, accept the ids semis writes. The table code and the scenario id
  keep their places in the text; the version field narrows from 16 bits to 12 and the
  sequence from 64 to 62. Every UUID a scenario writes changes:
  `02030405-5001-0001-0000-000000000042` is now `02030405-5001-8001-8000-000000000042`.
  `decode` and `semis decode-uuid` refuse any other UUID, the 0.1.0 layout included,
  where they decoded any.
- **Breaking: a nullable column is written `NULL` unless the scenario names it**, so a
  scenario with no overrides writes rows every view sees: no row arrives soft-deleted,
  no audit column points at nobody (fraiseql/semis#1). An override or a `providers:`
  entry for the column draws it, and so does the new per-table `fill: [column, …]`;
  `fill: all` draws every nullable column of the table, as 0.1.0 did. A provider
  library's rule matching a column does not name it. NOT NULL columns, columns with a
  default and nullable foreign keys are drawn as before. A run says, one line per
  table, which columns it left `NULL`. A `fill:` entry naming no column semis would
  write `NULL` — a NOT NULL one, a key, one with a default, one trusted to a trigger,
  or a hierarchy's `path`, which read-back fills from the keys — is refused, naming the
  table, the column and why.
- **Breaking: a pin keeps its facts, so every refusal names what moved**
  (fraiseql/semis#5). A run writes the projection it digested beside `schema_pin.yaml`
  as `<scenario>.facts.json`, and the pin block names it under `facts:`, where it named
  a DDL snapshot under `snapshot:`. A replay against a moved schema names each table and
  column that moved, and how — `catalog.tb_country.note: not_null false → true` — for a
  `database:` source as for `ddl:` and `env:`, where a live pin named nothing.
  `schema_pin.ddl` is no longer written, and `SchemaFacts.snapshot`,
  `SchemaFacts.changes_since` and `SchemaFacts`' `source` argument are gone. The facts
  are read from one file only, the `<scenario>.facts.json` beside the scenario file: a
  `schema_pin` naming any other path is refused before anything is read, and so is a
  facts file the digest was not taken from, or one nested too deeply for JSON to read.
  An unchanged schema digests as 0.1.0 did, but a pin 0.1.0 wrote keeps no facts: it is
  refused, with a hint to re-pin.
- **Breaking: a scenario's name is a file name**: letters, digits, `.`, `-` and `_`, in
  the file as in a `Scenario` built in Python, since it names the
  `<scenario>.facts.json` its pin keeps, used as written. Any other name, or one that is
  not a string, is refused when the scenario loads, naming the file. `semis
  init-scenario` accepts a `.` too.
- **A scenario applied twice is refused up front** (fraiseql/semis#4): `semis apply`
  (and `--dry-run`, and `semis generate` or `semis table` in read-back) asks, one query
  per table on the scenario's UUID range, whether the database already holds its rows,
  and refuses before anything is written with `AlreadyAppliedError` (`SEMIS_APPLY_001`,
  exit 1). It was a raw unique violation, mid-transaction, exit 5. A table with no `id`
  column, or whose `id` is not a uuid, is not asked, so a prep-seed table with a text
  `id` applies as in 0.1.0. The refusal names the first table found and the
  `TRUNCATE … RESTART IDENTITY` that resets the run's tables and no other, every name
  quoted and the statement quoted for the shell. With no `CASCADE`, PostgreSQL refuses
  it when a table outside the run has a foreign key into one of them, and the reader
  decides whether that table's rows go too. A database whose schema is not built is
  refused as `SchemaNotBuiltError` (`SEMIS_DATABASE_002`, exit 1), `scenario <name>: the
  database holds no <table>`, for a table the run writes as for an `existing:` one. The
  README's "Running it again" and the determinism page say how to reset, and why there
  is no upsert.
- **The documentation lives at https://semis.fraiseql.dev**: the package's
  Documentation URL names it, where it named https://fraiseql.dev/semis.
- **The sdist holds no tests**: they read the repository's site, workflows and git, and
  failed unpacked.

### Fixed

- **A malformed scenario or project is a refusal, never a traceback**:
  - a file that is not YAML is a `ScenarioError` or `ProjectError` (exit 1) naming the
    file and the line and column PyYAML stopped at, for a scenario, the scenarios
    directory, `semis.yaml` and its `table_codes:` file;
  - a key that is not a string is refused as an unknown key, in a scenario (at the top,
    in a table, a `hierarchy:` or an `existing:` entry) and in `semis.yaml`, where `1: x`
    raised a `TypeError`;
  - a `description` that is not a string, a `seed` that is not a whole number, a
    `locale` that is not one of Faker's (from the file or `--locale`) and a quoted
    `scenario_id` are each refused naming the scenario and the key. Faker raised the
    seed and the locale as tracebacks, a description of any type was accepted, and a
    quoted id was refused as one that "does not fit in 16 bits".
- **Every refusal of a malformed scenario says what to write**: `count: -1`, no
  `tables:`, a table entry with no `name:`, a malformed `hierarchy:`, `fill:`,
  `existing:`, `overrides:`, `providers:`, `trusts_trigger:` or `schema_pin:` each give a
  hint naming the shape the key takes, where they pointed at the README.
- **Every refusal names the scenario**: a table entry the file gets wrong (`count`,
  `overrides:`, `hierarchy:`), a provider no project or library registers, a column the
  run's check refuses, a table with no code, and a row or a parent the run refuses
  (`RowContractError`, `ResolutionError`) open with `scenario <name>:`, as ARCHITECTURE
  §9 says every error does. A malformed `hierarchy:` names its table too.
- **A table is schema-qualified wherever it is named**: under `tables:` and `existing:`,
  in a `TableSpec` or an `ExistingTable` built in Python, and in `semis table`. A bare
  name is refused before anything reads it, where it raised a `KeyError` or a
  `CodeRegistryError` naming no scenario. A qualified table the schema lacks is refused
  as `scenario <name>: table <table> is not in the schema`, where confiture's
  `NotInModelError` (exit 4) named no scenario and offered bare names.
- **The scenarios directory lists scenarios alone**: `semis list-scenarios`,
  `init-scenario` and `decode-uuid` skip a `schema_pin.yaml` there, where they refused
  the whole directory, and refuse a scenario whose `scenario_id` is quoted or does not
  fit in 16 bits naming its file, where a quoted id among integer ones raised a
  `TypeError`. `semis decode-uuid` still decodes when the directory does not read,
  saying why on stderr.
- **A hierarchy whose parent is trusted to a trigger is refused**, naming the scenario,
  the table and the column: semis draws the parent to build the tree. The tree was
  silently not drawn, and with a `path:` the run raised a `KeyError`.
- **`--dry-run` says what it does on each command**: `semis apply` applies every row,
  then rolls back; `semis generate` and `semis table` do so in read-back and write no
  file in prep-seed; `semis seeds` writes nothing. The help read "write and apply
  nothing" on every one of them.
- **`--scenario-id` says what it takes**: each command's help shows `<hex>`, where it
  showed `<lambda>`, the name of the function that parses it.
- **A moved pin's hint names a pin that exists**: `semis validate`, and `semis seeds`
  or `semis apply` under `--dry-run`, keep no pin, so their refusal names the
  `schema_pin.yaml` that `semis seeds -o <dir>` or `semis apply` writes, where it named
  one "this run wrote".
- **A project's providers module that does not import says how it would**: install it,
  or put its directory on `PYTHONPATH`. The hint said to run from the directory that
  holds it, which the installed `semis` command does not import from.
- **One of anything is counted in the singular**: `semis validate` says `1 table, 1 row`,
  `semis seeds` and `semis apply` say `1 row` for a seed file of one, and a list override
  of the wrong length says `1 value` or `1 row`.

### Security

- **A database URL libpq cannot read never shows its password**: it is refused as
  `UnreachableDatabaseError` (`SEMIS_DATABASE_001`, exit 1), saying the URL is malformed,
  before confiture or psycopg is given it, whichever command reads the database: `apply`
  and `validate-seeds`, and every command of a project whose schema is read from a
  `database:`. libpq's own message, repeating the token it could not read, escaped as a
  traceback, or through confiture's refusal of a `database:` schema. A URL whose
  password holds an unencoded `@` or `/`, which libpq reads as part of the host or as a
  port, is refused as malformed too, where the host it named repeated part of the
  password.
- **Every form a password takes is masked in a refusal**: a URL's password up to its
  last `@` (`p@ss`, `pa:ss`, `pa/ss`), a keyword DSN's `password=`, quoted or bare, and
  a URL query string's `?password=`. A keyword DSN was repeated whole by confiture's
  refusal, and a password with an `@` was masked only up to its first.

## [0.1.0] - 2026-09-30

The first release.

### Added

- **Semantic UUIDs**: each row's `id` encodes its table code, scenario id, version and
  sequence; `semis decode-uuid` reads one back, naming the table and the scenario.
- **Values from the schema's facts**, read through `confiture.platform`: type, declared
  length, enum labels, NOT NULL and UNIQUE; foreign keys from their `REFERENCES`.
- **Two declared FK modes**: prep-seed (a child carries its parent's UUID, written into
  the staging twin) and read-back (parents applied, their `pk_*` read back by UUID).
- **The row contract**: a row missing a NOT NULL column without a default is refused,
  with a per-column `trusts_trigger:` opt-out.
- **YAML scenarios** with overrides (a value, a list, a callable, or `null` for a
  nullable key), per-column providers, and hierarchies for self-referencing tables,
  including `ltree` paths in read-back.
- **Schema pins**: a digest of the facts semis consumes, refusing a replay against a
  schema that has moved and reporting what changed.
- **Provider libraries**: `i18n` and `organization` shipped; a project's own providers
  as `module:attribute`; installed libraries enabled by name through the
  `fraiseql_semis.providers` entry point.
- **The `semis` command**: `seeds`, `apply`, `generate`, `table`, `validate`,
  `validate-seeds` (confiture's five prep-seed levels), `list-scenarios`,
  `init-scenario` and `decode-uuid`, configured by `semis.yaml`.
- **Scale**: 21,500 rows per second generated; 100,000 rows written and applied in 15
  seconds, streamed rather than held.

### Security

- **A database URL's password is never printed.** semis masks it in whatever a refusal
  prints, and requires fraiseql-confiture 1.27, whose `CONFIG_003` refusal of a
  `--database-url` no longer repeats the password either.

[Unreleased]: https://github.com/fraiseql/semis/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/fraiseql/semis/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/fraiseql/semis/releases/tag/v0.1.0
