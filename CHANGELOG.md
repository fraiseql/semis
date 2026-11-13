# Changelog

All notable changes to fraiseql-semis are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.9.0] - 2026-11-13

### Changed

- **fraiseql-confiture 1.49 or later is required.** A schema whose tables, schemas or
  columns need quotes, such as `"User"`, `"Sales".tb` or `"order line"`, is now refused
  by confiture with `DIFFER_403` (exit 5), naming the file and line that declares the
  name. semis wrote such names bare, so it misnamed them; it now never reaches one.
  Rename the name so it needs no quotes; `confiture lint` lists every one.

## [0.8.2] - 2026-11-11

### Changed

- **typer 0.24 or later is required.** semis declared 0.19, but fraiseql-confiture
  already required 0.24, so no install resolved anything older. The floor is now the
  one CI tests.

### Fixed

- **A scenario id that is not a number says so.** `--scenario-id zz` printed only `zz`,
  or, under typer 0.29, Python's `invalid literal for int() with base 0`. It is now
  `'zz' is not a number, as 0x5001 is.` (exit 2).
- **A malformed UUID says so on any typer.** Under typer 0.29, `semis decode-uuid nope`
  printed pydantic's `Input should be a valid UUID, invalid character: found n at 0`.
  It says `'nope' is not a valid UUID.` again (exit 2).

## [0.8.1] - 2026-11-09

### Added

- **`coverage: session_variables:` in `semis.yaml`**: the session variables
  `semis coverage` sets, with `set_config(name, value, true)` in its read-only
  transaction, before it counts. A view that scopes its rows by
  `current_setting('app.agent_role', true)` showed `no rows` over full tables; it now
  shows the rows a request of that identity sees. Names are custom settings,
  `prefix.name`; a built-in one (`role`, `search_path`), a malformed name and a value
  that is not a string are refused at load. The table report's first line names them,
  `read with app.agent_role='manager'`, each value quoted as a PostgreSQL string; with
  `--format json`, that line goes to stderr. With none named, the report is 0.8.0's.
  From Python, `ScenarioManager.coverage` takes `session_variables=`, and
  `Project.coverage_variables` holds the project's.

### Fixed

- **A view the database refuses to read is a refusal, not a traceback.** `semis
  coverage` ended with psycopg's traceback on a materialized view created `WITH NO
  DATA` and never refreshed, or on a view reading a setting without `missing_ok` while
  it is unset. It is now `ViewReadError` (`SEMIS_DATABASE_003`, exit 1), naming the
  view and PostgreSQL's reason, with PostgreSQL's hint (`REFRESH MATERIALIZED VIEW`) or
  one naming `coverage: session_variables:` for an unset setting.
- **The two `coverage:` keys point at each other.** `session_variables:` under a
  scenario's `coverage:` was refused as "not a fraction from 0 to 1"; it now says it
  goes under `semis.yaml`'s `coverage:`. Any other key under `semis.yaml`'s `coverage:`,
  a locale among them, is refused with a hint naming `session_variables:` and the
  scenario's `coverage:`.
- **An unknown key in a `semis.yaml` block is named after one colon**:
  `semis.yaml: tenancy: unknown key tenant`, not `tenancy:: unknown key tenant`, in
  `schema:`, `prep_seed:`, `translations:`, `tenancy:` and `coverage:`.
- **`semis coverage`'s `rows` column widens to its largest count**: a view of more than
  9999 rows pushed its line out of line with the others.

## [0.8.0] - 2026-11-08

### Added

- **`semis coverage SCENARIO`**: of a database the scenario was applied to, every view,
  materialized view and TVIEW that reads a table the scenario writes, directly or
  through other views, with its rows and, when it outputs `tenancy:`'s column, its
  distinct tenants; `no rows`, `one tenant` and `no tenant` are marked. It applies
  nothing, in a read-only transaction. `--format json` gives a list of objects;
  `--fail-on-single-tenant` exits 1 when a multi-tenant view holds fewer than two
  tenants. A view reaching a table only through a routine's body is not listed, and
  the report says so (fraiseql/semis#9). From Python, `ScenarioManager.coverage`
  returns a `ViewCount` per view.

### Changed

- **fraiseql-confiture 1.47 or later is required**, for the view API `semis coverage`
  reads. confiture refuses to read a database whose `pg_tviews` is older than
  0.1.0-beta.30: upgrade the extension first.

## [0.7.3] - 2026-11-05

### Changed

- **`fill:` draws a column with a default.** `fill: [status]` on
  `status TEXT NOT NULL DEFAULT 'draft'` was refused as "NOT NULL, so semis always draws
  it", which it did not: the default applied to every row. A column with a default that
  `fill:` names, NOT NULL or not, is now drawn, from its CHECK's list when it has one; one
  `fill:` does not name is still left to PostgreSQL, and `fill: all` draws no default
  (fraiseql/semis#13).

## [0.7.2] - 2026-11-04

### Fixed

- **A table the DDL leaves in `public` is seedable.** A schema read from DDL whose
  `CREATE TABLE tb_x` names no schema could not be seeded: `public.tb_x` was "not in the
  schema" and `tb_x` "not schema-qualified". A scenario, `table_codes:` and `tenancy:`
  now name it `public.tb_x`, from DDL as from a live database.

## [0.7.1] - 2026-11-04

### Fixed

- **psycopg 3.3.0 or later is required.** confiture executes its SQL as template strings,
  which psycopg 3.2 refuses with `TypeError: expected bytes, string.templatelib.Template
  found`: an install resolving psycopg 3.2 could not apply a seed. CI now runs the
  integration suite on the lowest release of every dependency, so a floor that cannot
  apply a seed fails there.

## [0.7.0] - 2026-11-03

A scenario names fewer columns: what the schema already says — a CHECK's list of values,
a json or array type, a soft-delete column — is read from it.

### Added

- **json, jsonb and array columns are drawn** (fraiseql/semis#10): a json column as a small
  object of words, an array as one to three values of its element type, each chosen as a
  column of that type and name would be (`emails TEXT[]` holds emails, `VARCHAR(4)[]`
  values fit). A NOT NULL `jsonb` needs no override, and `fill: all` fills them.
- **A CHECK listing values is drawn from** (fraiseql/semis#11): `role TEXT CHECK (role IN
  ('manager', 'sales'))` receives `manager`, `sales`, `manager`, … round-robin, read from
  DDL or a live database, where PostgreSQL stores it as `= ANY (ARRAY[…])`. A registered
  provider and an override still win; any other CHECK is not read. No confiture floor
  change: 1.32 reports the checks.

### Changed

- **`fill: all` leaves `deleted_at` and `deleted_by` NULL** (fraiseql/semis#12), so no row
  it writes arrives soft-deleted, and the run says so:
  `shop.tb_customer leaves deleted_at NULL; fill: all leaves soft-delete columns NULL: fill: [deleted_at] writes one`.
  A `fill:` list, an override or a provider writes them.
- **A column of a type semis does not draw is refused before a row is drawn**, naming the
  column and its type (`app.tb_host.address is inet, a type semis does not draw`), where it
  was drawn `None` and refused at the NOT NULL check, or silently written `NULL` when
  `fill:` named it. `fill: all` leaves such a column `NULL` and names it.
- Values move for a scenario whose tables have a column these change: a json, array or
  CHECK-listed column is drawn where it was `NULL` or a Faker word, and a soft-delete column
  is `NULL` under `fill: all`. Each draws from the run's one seeded stream, so the columns
  after it in those tables draw other values than 0.6.0's, as the same seed always will
  from now on. A scenario touching none of them writes 0.6.0's bytes. A pin does not move.

## [0.6.0] - 2026-11-03

A scenario writes rows for several tenants from one file, each foreign key inside its
row's tenant, so a test of tenant isolation has another tenant's rows to hide. The
release is additive: a project without `tenancy:`, and a scenario without tenants, write
what they wrote in 0.5.1.

### Added

- **Tenants** (fraiseql/semis#8): `semis.yaml`'s `tenancy:` names the tenant column once,
  and the tenant table whose rows are the tenants; `exclude:` and `include:` override the
  detection. A table holding the column is tenant-scoped, and so is a table whose key
  points at a scoped one. A run's tenants are the rows it writes into the tenant table,
  or names under `existing: where:` there, or else the scenario's `tenants:` values.
  Each scoped row is written its tenant in the tenant column, round-robin, a tree in its
  root's tenant, and each key into a scoped table points at a parent of that tenant. A
  tenant whose child rows have no parent there is refused before a row is drawn, and so
  is a scenario that names the tenant column another way. A table's `crosses_tenants:`
  lets a key point at any tenant's row, on purpose. The run says what it spread:
  `scenario s spreads app.tb_contact, app.tb_order over 2 tenants of app.tb_organization`.
- `Project.tenancy`, `ScenarioManager(…, tenancy=)`, `Scenario.tenants` and
  `TableSpec.crosses_tenants`; `TenancyRule` in `fraiseql_semis.tenancy`.

## [0.5.1] - 2026-11-02

### Fixed

- **The confiture floor is 1.32, not 1.44.** 1.32 brings what 0.5.0 asked for, the report
  of a row an inner-join resolver drops. Since 1.37 confiture refuses a `pg_tviews` older
  than 0.1.0-beta.30, so 0.5.0's floor made a project upgrade that extension to seed data.
  The suite passes on 1.32, 1.33, 1.35 and the locked 1.44.

## [0.5.0] - 2026-11-02

### Added

- **`where:` names existing rows by any unique text column** (fraiseql/semis#7):
  `where: {email: [a@example.invalid, b@example.invalid]}`, in read-back and in prep-seed,
  with today's semantics: these rows, in this order, refused when one is missing. A column
  the table lacks, one not unique, or one neither `text` nor `varchar` is refused by check.
  The column is pinned, so it moving moves the digest; a scenario naming rows by
  `identifier` keeps its digest. A repeated value points at its row again, so `[a, a, b, b]`
  gives children in runs.

### Changed

- `ExistingTable(name, identifiers=…)` is `ExistingTable(name, by=…, values=…)`. A scenario
  file needs no change.
- **The floor is fraiseql-confiture 1.44.** Since 1.32, level 5 reports a staged row an
  inner-join resolver drops, as `CRITICAL STAGED_ROW_NOT_PROMOTED` (fraiseql/confiture#666):
  a stale UUID that passed every level now exits `semis validate-seeds` 1.

## [0.4.0] - 2026-11-02

A scenario writes its entities' translations: one label per declared locale, a
pseudo-locale, and gaps left on purpose, so the code that picks a label by locale and
falls back is tested on data. A prep-seed scenario may now point at existing rows, by
the UUIDs its pin records. The release is additive, which tests show: a 0.3.0 pin
verifies unchanged, and a 0.3.0 read-back scenario writes the same bytes. There is no
"Upgrading" section. The one change of behaviour, a `where:` value two rows share, is
under Changed.

### Added

- **Translations** (fraiseql/semis#6): a scenario's `locales:` lists the locale table's
  codes, and a table's `translations:` names its translation table. For each row, the run
  writes one translation row per locale, its entity key the row, its `fk_locale` the
  locale's row — read by `code` in read-back, from the pin in prep-seed — and its `label`
  and every other value drawn from that locale's own seeded Faker, so adding `locales:`
  changes no other row. A translation table with a uuid `id` takes `entity sequence ×
  256 + slot`, so appending a locale changes no UUID; one without is reset through its
  entity. Every shape semis cannot write, and a code with no Faker locale, no row or two,
  is refused naming the table. `Scenario.locales` is a tuple of `Locale(code, faker)`.
- **A pseudo-locale**: `pseudo_locale:` names one more locale code, and each entity
  gets a last translation row, at slot 255, whose label is the first locale's
  transformed — every letter marked, 40% longer, bracketed (`Category` →
  `[Ḉḁṭḕḡṓṙẏ ···]`), cut to its column with the brackets kept. Only the label is
  transformed. `fraiseql_semis.translation.pseudo` is the transform, for snapshot tests.
- **A project's translation rule in `semis.yaml`**: `translations:` names the
  translation table's template (`{schema}.tl_{entity}`), `label_column`, `locale_fk` and
  `locale_table` (`schema.table (column)`), each defaulting to the names above, and a
  scenario's `translations: true` writes into the table the rule names. From Python,
  `Project.translations` is a `TranslationRule`.
- **Coverage leaves labels out on purpose**: `coverage: {en-GB: 0.8}` writes `en-GB`
  labels for exactly `⌊n × 0.8⌋` of each translated table's `n` rows, the pseudo-locale
  included when named. Which rows lack one is ranked by a sha256 over the scenario, the
  table, the locale and the row, so it ignores `seed:` and raising coverage only adds
  rows. Every label is still drawn, so the rows written keep their labels and UUIDs. The
  run prints `catalog.tl_category: 20 rows × 4 locales, en-GB 4 missing`.

- **A prep-seed scenario may list `existing:` tables**: a child points at their rows by
  the UUIDs the scenario's pin records, round-robin in read-back's order, and a run still
  reaches no database and stays byte-reproducible from its files. `semis pin -d` records
  them; a prep-seed scenario whose pin records none of a table's rows, or whose existing
  table has no uuid `id`, is refused by `semis validate` and every run, with the hint to
  run `semis pin -d`.

### Changed

- **A prep-seed pin records the existing rows its scenario points at**: `semis pin -d`
  reads them, in a transaction rolled back, from a database named on purpose, and
  `<name>.pin.json` keeps a `rows` section of each row's `identifier` and UUID, with a
  digest of its own. A whole table's rows keep the order first pinned and append the
  rows it gains. `semis pin --check` now fails when the seeds would be wrong — the facts
  moved, a pinned row is gone or its UUID changed, a read is missing — and passes, saying
  so, when a whole table only gained rows. A read-back scenario's pin records no rows,
  and one that does is refused: its mode changed since it was pinned.
- **A `where:` value two rows of an `existing:` table share is refused**, naming the
  table, the column and the value: which of the two a child pointed at was arbitrary.
  Give the rows distinct identifiers, or drop the shared value from `where:`.

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

[Unreleased]: https://github.com/fraiseql/semis/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/fraiseql/semis/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/fraiseql/semis/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/fraiseql/semis/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/fraiseql/semis/compare/v0.7.3...v0.8.0
[0.7.3]: https://github.com/fraiseql/semis/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/fraiseql/semis/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/fraiseql/semis/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/fraiseql/semis/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/fraiseql/semis/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/fraiseql/semis/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/fraiseql/semis/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/fraiseql/semis/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/fraiseql/semis/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/fraiseql/semis/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/fraiseql/semis/releases/tag/v0.1.0
