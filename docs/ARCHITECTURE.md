# Architecture: fraiseql-semis

**Version**: 1.0
**Date**: 2026-09-23
**Depends on**: `fraiseql-confiture>=1.27,<2`
**Companion documents**: [PRD.md](./PRD.md) (what semis is for), [../README.md](../README.md) (how it is used)

> Every confiture behaviour this document states was measured against a trinity-pattern
> schema and a live PostgreSQL, and is pinned by a test in `tests/contract/`, named beside
> it, so an upstream change fails there with a name rather than here as a puzzle.

---

## 1. What semis is

semis generates rows. It decides which rows exist, what each value looks like, and what
UUID identifies each one. It decides nothing about the schema those rows land in.

> **The boundary, in one sentence.** Every question of the form *"what does the schema
> say?"* is confiture's; every question of the form *"what should this row contain?"* is
> semis'.

A tool that generates data has to answer both, and the interesting failures live where
they meet — a column invented from a naming convention, a topological sort that disagrees
with the real foreign keys, a `VARCHAR(50)` filled with sixty characters. semis answers
the second kind only, and asks `confiture.platform` the first kind every time.

---

## 2. The boundary

### Division of labour

| Question | Answered by |
|---|---|
| What tables, columns, constraints and enums exist | `parse_schema` (DDL) / `introspect` (live) |
| In what order may rows be inserted | `dependency_order` |
| Which columns may a writer supply | `writable_columns` |
| What must a value respect — type, NOT NULL, UNIQUE, CHECK, enum, FK target | `column_facts` |
| Which column is the surrogate key, which the natural id | `naming_hints` |
| How a seed file is written, applied, validated | `write_copy_seed` / `write_insert_seed`, `apply_seeds`, `validate_seeds` |
| What a schema changed between two revisions | `diff`, `tier_of` |
| **What a value looks like** | **semis** — Faker, locales, domain providers |
| **Which rows exist, how many, in what scenario** | **semis** — scenarios |
| **What identifies a row** | **semis** — the semantic UUID encoding |
| **Whether a row is complete enough to emit** | **semis** — the row contract (§6) |
| **That the schema has not moved under a scenario** | **semis** — the pin (§8) |

### What crosses the boundary

Only confiture's own published types, and a database as a `str` URL or an object meeting
the `confiture.platform.Connection` protocol:

```
inbound:   SchemaModel, ObjectRef, Column, ColumnFacts, ColumnReference, TableHints,
           SeedFile, ApplyResult, PrepSeedReport, PrepSeedViolation, SchemaDiff, RiskTier
errors:    ConfiturError, SchemaError, SeedError, ConfigurationError, NotInModelError,
           DependencyCycleError
outbound:  str | Path, Sequence[str], Iterable[Mapping[str, object]], SchemaModel
```

No `pglast` node and no `psycopg` type crosses it. A `SchemaModel` is the only schema
representation semis holds; semis builds no model of its own.

### What never crosses it

semis never reads `information_schema` or `pg_catalog`, never sorts tables itself, never
composes DDL, and never emits an `INSERT` or a `COPY` block by hand; each is one call.

---

## 3. Module map

| Module | Owns | Touches |
|---|---|---|
| `schema.py` | `SchemaFacts`, `TableFacts` — confiture's model as semis reads it | **`confiture.platform`** |
| `codes.py` | `TableCodes` — the `table_code` registry | — |
| `seeds.py` | Writing, applying and validating seed files | **`confiture.platform`** |
| `readback.py` | Learning a parent's `pk_*` after its seed is applied; setting a hierarchy's paths | **`psycopg`** |
| `uuid_generator.py` | `SemanticUUIDGenerator` — encode and decode | — |
| `faker_provider.py` | `FakerProvider`, `CustomProviderRegistry`, `Library`, `Rule` — what a value looks like | — |
| `rows.py` | The row contract: is this row emittable (§6) | — |
| `generator.py` | `FakeDataGenerator` — assembling a row from facts, providers and UUIDs | — |
| `resolution.py` | The two FK modes, over `ColumnFacts.foreign_key` | — |
| `staging.py` | `Staging` — the twin a prep-seed run writes into (§5) | — |
| `hierarchy.py` | `Hierarchy` — a self-referencing table's tree, breadth-first (§5) | — |
| `emit.py` | A run in a declared mode: walked, written, and in read-back applied and learned | — |
| `pin.py` | `SchemaPin` — the digest a scenario records (§8) | — |
| `scenario.py` | `Scenario`, `TableSpec`, `ScenarioManager`, YAML loading | — |
| `project.py` | `Project` — `semis.yaml`: the schema source, the table codes, the scenarios directory, the providers, the staging schema | — |
| `errors.py` | semis' own exceptions and their codes | — |
| `cli/` | The `semis` command | — |
| `providers/` | The shipped provider libraries, `i18n` and `organization`, each a `Library` | — |

### Where the scenario ids are registered

In the scenario files themselves. Each writes its own `scenario_id`, and
`scenario.catalogue(directory)` reads the headers of every scenario under a project's
scenarios directory, so an id is written in exactly one place; two files using one id
are reported, never silently resolved. `semis decode-uuid` names a UUID's scenario this
way, and its table through `TableCodes`.

### Where a `SchemaFacts` comes from

Three constructors, because confiture reads a schema three ways and the difference matters
to the pin (§8):

```python
SchemaFacts.from_source(source, *, table_codes)            # DDL text, a Path, or a sequence
SchemaFacts.from_env(env, *, project_dir=None, table_codes)  # the project's build
SchemaFacts.from_database(database, *, schemas, table_codes) # a live database
```

`from_source` is what makes the generator testable without PostgreSQL and without a
confiture project: a DDL string is a schema. `from_source` and `from_env` produce a `ddl`
pin; `from_database` produces a `live` one.

There is no single `from_ddl(env=…)`: an environment's build is not DDL text, and
`parse_schema` refuses a source and an `env` together (`ValueError: Give exactly one of a
schema source or an environment.`) — pinned by `tests/contract/test_parse_schema.py`.

### Three seams, each enforced

The repository's own guard tests hold these, in the idiom confiture uses for its own
one-reader rules — an allow-list whose entries each state a reason, and an entry matching
nothing is a failure:

1. **`schema.py` and `seeds.py` are the only modules that call `confiture.platform`.**
   `tests/unit/test_one_confiture_seam.py`. `schema.py` re-exports the confiture types
   semis passes around, so the rest of the package imports them from
   `fraiseql_semis.schema` and a rename in confiture lands in one file.
   One call is not on `confiture.platform`: the database-URL precedence the `semis`
   command follows (#152) lives in `confiture.cli.dsn`, with the environment file it
   defers to in `confiture.config.environment`. `schema.py` imports both, and
   `tests/contract/test_database_url_precedence.py` pins every behaviour semis relies on
   (D21).
2. **`readback.py` is the only module that imports `psycopg`, and holds the only SQL
   semis writes.** `tests/unit/test_one_sql_site.py`. That SQL is two shapes (§5), their
   identifiers composed with `psycopg.sql.Identifier` from names the model supplied, their
   values passed as parameters.
3. **`codes.py` is the only module that maps a table name to a `table_code`.**
   `tests/unit/test_one_code_registry.py`. A code derived at two sites is a code that
   drifts, and every UUID semis ever wrote is a hostage to it.

Everything under `generator.py`, `rows.py`, `faker_provider.py`, `resolution.py`,
`hierarchy.py`, `staging.py`, `pin.py` and `providers/` is pure: facts in, values out, no database and no file
system. That is what lets the row contract be tested against a DDL string with no
PostgreSQL anywhere.

---

## 4. The trinity pattern, as semis writes it

| Column | Who fills it | How |
|---|---|---|
| `pk_*` (integer identity) | **PostgreSQL** | semis cannot write it, and confiture refuses a seed that tries |
| `id` (UUID) | **semis** | the semantic encoding — table code, scenario, version, sequence |
| `identifier` (slug) | **semis** | a Faker word, the scenario id and the sequence — `europe-5001-3` |

The refusal is real, and it is the fact the whole emission design turns on
(`tests/contract/test_writer_refusals.py`):

```python
>>> write_copy_seed(path, "catalog.tb_continent", ["pk_continent", "id"], rows, model=model)
SeedError: PostgreSQL fills catalog.tb_continent.pk_continent (an identity, generated or
           serial column): leave it out of the seed
```

Nothing is written when anything is refused: the file does not exist afterwards.

`writable_columns` has already excluded `pk_continent`, so semis never assembles it in the
first place; the refusal is the backstop, not the mechanism. The same exclusion covers
generated columns: on the probe schema, `full_label TEXT GENERATED ALWAYS AS (…) STORED`
is absent from `writable_columns` and PostgreSQL computes `country-1-FR` on apply.

The slug carries the scenario id for the reason the UUID does: two scenarios seeded into
one database share every unique constraint, and two runs with the same `seed:` draw the
same Faker words.

`naming_hints` names the two columns — `TableHints(surrogate_pk='pk_country',
natural_id='id')` — and semis treats them as the heuristic signals confiture says they
are. A table showing neither gets `None` for both: semis then writes no encoded UUID and
the scenario must supply identity some other way.

---

## 5. Emission and the two FK-resolution modes

A child row needs its parent's integer `pk_*`, and semis cannot choose one. There are
exactly two honest ways to close that gap. **A scenario declares which one it uses and
semis refuses to guess** (D3).

Auto-detection is the one thing here that determinism cannot survive: a scenario that picks its mode by probing the database it meets is a
scenario that means two different things in CI and on a laptop.

In either mode a foreign key points at a row of the run, so its parent must be generated
in it: a parent the run lacks is refused before a row is drawn, by `semis validate` as by
`semis seeds`. A **nullable** key to another table may instead be overridden `null`
(D29) — written `NULL`, its parent not required. That is the scenario's word, never
semis' guess: without it the parent is still required. It is what makes a real table
reachable — measured on one production schema, a table needing 5 tables through its NOT
NULL keys needed 24 through all of them, 7 of those without a staging twin.

### Mode A — prep-seed

Rows are written into the project's staging schema carrying **UUIDs only**; the project's
own `fn_resolve_*` functions translate UUID → `BIGINT` when promoting a row into the
catalog schema. semis knows each parent's UUID because it encoded it, so a child's FK
column carries the parent's `id`, not an integer.

A scenario names the catalog tables — their foreign keys, constraints and pin are what
semis reads — and each is written into its **staging twin** (D26): the same table name in
the staging schema (`prep_seed` unless `semis.yaml` names another), each foreign-key
column written under `<fk>_id`. That is the layout a project's staging tables have, with
no `REFERENCES` of their own, and the one confiture's level 1 checks
(`tests/contract/test_prep_seed_convention.py`):

```
INSERT INTO catalog.tb_country (…)                    → ERROR    Seed INSERT targets catalog schema but should target prep_seed
INSERT INTO prep_seed.tb_country (…, fk_continent, …) → WARNING  FK column 'fk_continent' missing _id suffix (should be 'fk_continent_id')
```

A column is renamed because `ColumnFacts.foreign_key` says it is one, never because of its
name. The twin needs no table code — its rows carry the catalog table's UUIDs, which the
resolver copies across. A twin the schema lacks is refused before a row is drawn; a twin
lacking a column the rows carry is refused before its file is written.

No database is needed to generate. `semis seeds` is a pure file-producing command, and
its output is byte-reproducible.

Confiture validates the pattern at five levels through `validate_seeds`; levels 1–3 are
static, 4–5 run the resolvers against a database in a transaction they roll back.
`ScenarioManager.validate` rehearses a prep-seed scenario into a directory holding only
its seeds, and judges exactly those files: on the caller's connection, levels 4–5 run
inside a savepoint confiture rolls back, and the transaction stays the caller's. The
staging schema is the project's; `catalog_schema` is passed only when named, since
confiture takes each final table from its resolver (#458). A read-back scenario is
refused: the levels judge prep-seed seeds. `semis validate-seeds` runs it, or
`validate_seeds` over a `--seeds` directory, on one connection whose transaction is
rolled back; `prep_seed: schema_dir:` names the resolvers' tree, defaulting to a `ddl:`
directory. Findings print most severe first; a `CRITICAL` or `ERROR` exits 1.

### Mode B — read-back

For a project without resolvers: apply the parents, ask the database for the integers,
then generate the children. For each table in `dependency_order`, parents first, the run
draws its rows, writes them with `seeds.write(…, mode="read-back")`, applies the file on
the caller's connection, and learns the table's keys with `readback.learn` — one query per
table, joined on the UUIDs it just wrote — before the next table is drawn.

The read-back joins on the UUID semis encoded — never `LIMIT 1`, which would point every
child at one parent, and never an integer semis guessed. Children are distributed over
the parents found, round-robin.

This is the only SQL in the package, in two shapes — learning keys, and setting a
hierarchy's paths from them (below):

```sql
SELECT <surrogate_pk>, <natural_id> FROM <schema>.<table> WHERE <natural_id> = ANY(%s)

UPDATE <schema>.<table> AS t SET <path> = v.path::ltree
  FROM unnest(%s::uuid[], %s::text[]) AS v(id, path) WHERE t.<natural_id> = v.id
```

Every identifier in them comes from `naming_hints`, `ObjectRef` or the scenario's
`hierarchy.path` checked against `writable_columns`, composed with
`psycopg.sql.Identifier`; the UUIDs and the paths are parameters.

### Hierarchies: a table that references itself

A self-referencing foreign key — a location's `fk_parent_location`, beside its
`path LTREE` — is a child table whose parents are its own rows. `dependency_order` lists
the table once and raises no `DependencyCycleError`, so the order alone cannot put a parent
row before its children. A scenario declares the tree (D16), and semis draws it
breadth-first, a level at a time:

```yaml
- name: catalog.tb_location
  count: 20
  hierarchy:
    parent: fk_parent_location   # the self-FK that builds the tree
    roots: 2
    fan_out: 3                   # levels of 2, 6 and 12 rows
    path: path                   # read-back only: pk_*-labelled ltree
```

The first `roots` rows are roots and carry `NULL`; row *k* after them points at row
*(k − roots) // fan_out*. The parent column must be nullable, since a root has no parent
(D17). Any other self-FK on the table is written `NULL`, and refused when it is NOT NULL
(D18).

- **prep-seed** writes the table as one file, roots first; a child's parent column carries
  its parent row's UUID, as any FK does in this mode.
- **read-back** writes one file per level, `NNN_<table>.L<n>.sql`, and applies and learns
  each level before the next is drawn (D19). A child's parent column carries its parent
  row's `pk_*`.

`path:` names an `ltree` column semis fills in read-back with **`pk_*` labels** — the
parent's path, a dot and the row's own key (`1.5.12`), the format a
tree-path recalculation conventionally writes. A row's own key exists only after it is inserted, so the
path cannot be in the seed file: once a level is learned, its paths are built in pure
Python and set by the `UPDATE` above, joined on the UUID (D20). The column is left out of
the seed file, drawn by no provider, and refused if it is NOT NULL, overridden or
trusted. Prep-seed has no keys at all and refuses `path:`; the project recalculates its
paths after promotion.

### Which writer, per mode

| Mode | Writer | Why |
|---|---|---|
| prep-seed | `write_insert_seed` | Chosen when prep-seed level 1 read only `INSERT … VALUES` (#366). Since 1.23 level 1 reads a COPY seed too — measured: a bad UUID in a COPY's second row and a COPY into `catalog.` each draw an `ERROR` with its line — so INSERT is the default for continuity, and `--format copy` is validated alike. |
| read-back | `write_copy_seed` | COPY is what the applier streams, and read-back mode has no level-1 gate to feed. |

`--format copy|insert` overrides either. Both writers take the same arguments, return a
`SeedFile`, and write exactly the columns given in the order given.

### semis never hands `apply_seeds` a directory

Until 1.23, `apply_seeds` read a directory's top level while `validate_seeds` recursed
(confiture #374): a nested layout validated and was never applied, silently. The two now
read the same tree (`tests/contract/test_apply_reads_a_tree.py`). semis passes the explicit
list of paths it just wrote, in the order it wrote them, so it applies exactly what it
validates whatever a directory reader does.

### Transactions

`apply_seeds(url, …)` opens, commits and closes — one transaction, a savepoint per file.
`apply_seeds(connection, …)` leaves the transaction to the caller
(`tests/contract/test_transaction_ownership.py`): rows are visible inside the caller's transaction before its commit, and roll back with it. Mode B
needs the second form, because generate-apply-read-back has to be one transaction to be
undoable.

---

## 6. The row contract

Confiture's writers check that a row carries exactly the columns named, that no value
holds a NUL, and that no column is one PostgreSQL fills. They do **not** check that a row
is complete, and they do not validate values against the column's constraints. Both are
deliberate — a trigger may fill a column, and the model does not know what a trigger
writes — and both are measured (`tests/contract/test_writers_do_not_validate.py`):

```python
# NOT NULL, no default, simply left out of `columns`  → accepted
write_copy_seed(p, "catalog.tb_country", ["id", "fk_continent"], rows, model=m)   # ok

# values the column cannot hold                       → accepted, written verbatim
"not-a-uuid"  into uuid          → not-a-uuid
3.7           into integer       → 3.7
"toolong"     into varchar(3)    → toolong
None          into a NOT NULL    → \N
```

So completeness and validity are semis' job, and `rows.check_row` is where they live. A
row is emittable when, for every column in `TableFacts.columns`:

| Fact | The rule |
|---|---|
| `not_null` and `default is None` | the row carries the column, and its value is not `None` |
| `not_null` and `default is not None` | the column may be omitted — PostgreSQL fills it |
| `enum_values` | the value is one of them |
| `type_key` carries a length — `varchar(n)`, `char(n)`; `raw_sql_type` says `bpchar(n)` | the value fits |
| `unique` | the value has not been used for that column earlier in the run |
| `foreign_key` | the value came from `resolution.py`, never from a provider |
| `checks` | reported, not enforced — semis does not evaluate SQL expressions (§11) |

**A row failing the contract is refused, not emitted** (D4). The alternative is that the
gap surfaces at apply time as a constraint violation naming a file and no line, long after
the generator that caused it has gone.

The opt-out is per column, in the scenario, because the honest exception is real:

```yaml
tables:
  - name: catalog.tb_country
    count: 50
    trusts_trigger: [created_by, updated_by]   # a trigger fills these; do not refuse
```

`trusts_trigger` names columns semis then omits and does not complain about. It is
per-table and per-column: there is no scenario-wide "trust everything" switch, because
that is the same as not having the contract.

---

## 7. Determinism

Reproducibility is the product. What holds:

| Component | Deterministic because |
|---|---|
| The UUID of a row | `table_code ‖ scenario_id ‖ version ‖ sequence`, all four fixed by the scenario and the row's position |
| Faker values | one seed per run, taken from the scenario |
| Table order | `dependency_order` is deterministic — among the tables ready at each step, the first by `(schema, name)` |
| Column order | `writable_columns` returns declaration order; the seed writes the columns given, in the order given |
| The schema itself | the pin (§8) |

**What is reproducible, per mode**:

- **prep-seed mode is byte-reproducible.** Every value in the file is semis', including
  the FK columns, which carry UUIDs. The same scenario, seed and schema produce the same
  bytes on any machine. This is what makes a seed file diffable in review and pinnable in
  CI.
- **read-back mode is reproducible except for the FK integers.** Those are PostgreSQL's,
  drawn from an identity sequence whose state semis does not control — measured on the
  same seed file, applied twice with a `TRUNCATE` between:

  ```
  fresh database         -> fk targets: [1, 2]
  after TRUNCATE, re-run -> fk targets: [3, 4]
  ```

  A plain `TRUNCATE` does not reset an identity sequence; `TRUNCATE … RESTART IDENTITY`
  does, and a project that restarts identities gets byte-identical read-back output. semis
  does not control which a project does, so it does not promise the bytes. The *shape* is
  identical either way — the same children point at the same parents, because the join is
  on the UUID. A read-back seed file is an artifact of a run, not
  a reviewable document.

### Replaying a scenario is refused by the schema

A scenario applied twice into one database collides on its own encoded values, because the
UUID is a function of the scenario and the sequence rather than of the run:

```
SeedError: Failed to execute seed file rb.sql: duplicate key value violates unique
           constraint "tb_continent_id_key"
```

semis relies on this rather than tracking what it has applied: a scenario is a statement
about what a database contains, and PostgreSQL is the one that knows whether it already
does. The `identifier` slug therefore carries the scenario's own discipline — it is unique
within a scenario and distinct across scenarios sharing a database — for the same reason
the UUID does.

That asymmetry is a reason to prefer prep-seed where a project has the resolvers, and it
is why the mode is declared rather than inferred.

---

## 8. Pinning a scenario to a schema

A scenario replayed against a schema that has moved produces a wrong seed silently. The
pin turns that into a refusal (D5).

### What is measured, and what it corrects

A scenario could record the digest of `SchemaModel.to_json()`, if it were byte-stable.
Half of that holds (`tests/contract/test_model_json_is_not_cross_source_stable.py`):

```
to_json() same source, twice     → identical      ✓
to_json() DDL vs live database   → different, for one schema that has not changed  ✗
```

The differences are not schema changes. `parse_schema` reports `line: 5` where `introspect`
reports `line: 0`; DDL says `varchar(255)` where the catalog says `character varying(255)`.
A facts-only projection narrows it but does not close it — PostgreSQL normalises
expressions:

```
default:  "'draft'"                  →  "CAST('draft' AS catalog.status)"
check:    "iso_code ~ '^[A-Z]{2}$'"  →  "iso_code ~ CAST('^[A-Z]{2}$' AS text)"
```

So **no digest is portable between `parse_schema` and `introspect`**, and a pin that
pretended otherwise would refuse every run that changed its schema source — which trains
people to pass `--no-pin`.

### The pin

A `SchemaPin` is a digest over **the facts semis consumes**, tagged with the source kind
that produced it:

```yaml
schema_pin:
  source: ddl              # ddl | live — a pin is comparable only with its own kind
  digest: sha256:b02f4b364d06650c…
  confiture: "1.18.0"
  taken: 2026-09-23
  snapshot: schema_pin.ddl # the DDL digested, beside the scenario; ddl pins from a source only
```

A run writes this block to `schema_pin.yaml` beside its seeds, and the DDL it read to
`schema_pin.ddl` — not `.sql`, since confiture's directory readers take every `*.sql` as a
seed and would apply the snapshot (D27); the author copies both beside the scenario to pin it. semis never
rewrites a scenario file, and a scenario without a pin runs and says it is unpinned.

The projection holds, per scenario table in `dependency_order`: the table's display name, its
`surrogate_pk` and `natural_id`, and for each writable column its name, `type_key`,
`raw_sql_type`, `not_null`, `default`, `unique`, `checks`, `enum_values` and the FK's
`(table, column)`; for a prep-seed scenario, then, each staging twin's columns the same
way, or its absence. It holds nothing else.

Two consequences, both measured, both wanted:

- **Adding an index moves `to_json()` and does not move the pin.** A change that cannot
  alter a generated row does not invalidate a scenario.
- **Adding `NOT NULL` to a written column moves the pin.** A change that can alter a
  generated row — or make one refusable under §6 — does.

On replay: same source kind and same digest, the run proceeds. Different digest, it is
refused, and semis reports what moved by calling `diff` on the pinned snapshot and the
current source. A pin of a different source kind is refused *as incomparable*, with the
reason, not reported as a mismatch.

`diff` takes sources, not models (`diff(model, model)` raises `TypeError`,
`tests/contract/test_diff_reads_sources.py`), so
the snapshot is what makes the comparison readable. A `live` pin has no DDL to keep, and
confiture publishes no call returning an env build's text, so a pin from either is refused
with its two digests alone, saying `diff` has nothing to compare.

`--no-pin` skips the check for the one run and says so in the output. There is no config
setting that turns it off permanently.

---

## 9. Errors

Confiture's exceptions propagate as they are. They already carry an error code, an exit
code and a resolution hint — `DependencyCycleError` names the tables in the cycle and suggests
a DEFERRABLE key — and re-wrapping them would only hide that.

semis adds its own hierarchy for its own failures, in the same shape:

```
SemisError(ConfiturError-shaped: message, error_code, exit_code, resolution_hint)
├── ScenarioError      a scenario file that does not load, names a mode that is not one,
│                      leaves a self-referencing table without its hierarchy, or
│                      writes a prep-seed table whose staging twin is missing
├── RowContractError   a row refused by §6 — names table, column and the fact it failed
├── ResolutionError    no parent row to point a foreign key at
├── CodeRegistryError  a table with no code, or two tables with the same one
├── PinError           a digest mismatch, or a pin of an incomparable source kind
├── UnreachableDatabaseError  a database URL nothing answers at — named by host, port and database
└── ProjectError       a semis.yaml that does not load, or a command with no project
```

Every one names the table, the column and the scenario where it applies. A generator
failure that says only "constraint violation" is the failure mode this whole design exists
to remove.

The `semis` command has one error boundary. A refusal of either kind is printed to stderr,
message then hint, and becomes the exit code: 1 for semis' own, and confiture's own code
for confiture's (4 for a schema refusal, 5 for a seed or configuration one). A URL's
password is masked in whatever a refusal prints, since a refusal may repeat the URL it
was given. Anything else is a bug and keeps its traceback.
`validate-seeds` refuses nothing when confiture finds something: it prints the findings
and exits 1 on a `CRITICAL` or `ERROR`, as `list-scenarios` exits 1 on a shared id.

---

## 10. The confiture gaps semis is built around

| Gap | What it was | Closed in | What semis does |
|---|---|---|---|
| **#366** | prep-seed level 1 read only `INSERT … VALUES`, so a COPY seed was unchecked there | 1.23 | prep-seed still writes INSERT by default (§5); a contract test pins that COPY is judged alike |
| **#374** | `apply_seeds` read a directory's top level; `validate_seeds` recursed — a nested layout validated and never applied, silently | 1.23 | semis passes explicit file lists, never a directory (§5) |
| **#385** | prep-seed levels 3–5 found resolvers by the file name's prefix (`fn_resolve*.sql`), so numbered files were skipped silently | 1.23 | pinned by a contract test: resolvers are found by routine name |
| **#386** | level 1 read the seeds directory recursively, level 5 its top level only: #374 inside `validate_seeds` | 1.23 | pinned by a contract test |
| **#387** | level 1 took any hyphenated value for a UUID — every semis slug — and read only an `INSERT`'s first row | 1.23 | pinned by a contract test |
| **#457** | `parse_schema` raised `RecursionError` on a routine with an input parameter and 21+ output columns, so a real schema could not be read | 1.24 | pinned by a contract test |
| **#458** | levels 2–5 took every staging table to resolve into one `catalog_schema`: a table resolved elsewhere drew false ERRORs, and its NULL FKs went uncounted | 1.24 | pinned by a contract test; `catalog_schema` is passed only when a project names it, as confiture's fallback |
| **#498** | level 3 read a foreign key's parent from the column's name, so a key named for its role (`fk_owner` → `tb_person`) was an ERROR however its resolver mapped it | 1.25 | pinned by a contract test: the parent is the table `REFERENCES` names |
| **#530** | level 3 judged a key with no `REFERENCES` — a key into a partitioned table cannot have one — by its name, so a role-named key its resolver maps was an ERROR | 1.27 | pinned by a contract test: the key is resolved by what its resolver joins |
| **#537** | a `--database-url` refused as `CONFIG_003` was repeated whole, password included | 1.27 | pinned by a contract test; semis still masks a URL's password in whatever a refusal prints (§9) |

The floor is 1.27: 1.27 closes #530 and #537, and is the release semis is measured on.
The explicit file lists are what semis would do anyway, so they stay.

---

## 11. Non-goals

semis does not:

- read `information_schema` or `pg_catalog` for schema facts — `introspect` does that;
- order tables itself — `dependency_order` does;
- generate DDL, migrations or `ALTER` statements — that is confiture's whole other half;
- evaluate CHECK expressions. A CHECK is reported to the provider that generates the value
  and pinned by §8, so a provider can satisfy `iso_code ~ '^[A-Z]{2}$'` deliberately; semis
  does not parse SQL to infer what would satisfy it. PostgreSQL is the judge, at apply time;
- own the prep-seed resolution functions. They are the project's, and `validate_seeds`
  checks them;
- hold a UUID→integer map. The integers are PostgreSQL's, and a parent's key is learned
  per table — per level, for a hierarchy — not per row.

---

## 12. Decision log

| # | Decision | Source |
|---|---|---|
| D1 | Every schema fact comes from `confiture.platform`; two modules call it | PRD |
| D2 | semis never writes an identity `pk_*` | PRD, measured refusal |
| D3 | A scenario declares its FK mode; semis refuses to guess | owner, 2026-09-23 |
| D4 | A row missing a NOT NULL column with no default is refused; per-column `trusts_trigger` opt-out | owner, 2026-09-23 |
| D5 | A scenario pins the schema by a facts digest tagged with its source kind; replay refuses on mismatch; a cross-source comparison is refused as incomparable | owner, 2026-09-23 |
| D6 | `table_code` and `scenario_id` live in a checked-in registry, written in hex so the UUID text reads back | owner, 2026-09-23 |
| D7 | The writer follows the mode — prep-seed writes INSERT, read-back writes COPY; `--format` overrides | measured (#366) |
| D8 | semis hands `apply_seeds` an explicit file list, never a directory | measured (#374) |
| D9 | `readback.py` holds the only `psycopg` import and the only SQL semis writes: the key read-back, and a hierarchy's path `UPDATE` | this document; amended by the owner, 2026-09-23 |
| D10 | Determinism is stated per mode: prep-seed is byte-reproducible, read-back is not | measured |
| D11 | Confiture's exceptions propagate unwrapped; semis adds its own for its own failures | this document |
| D12 | The command is `semis`, the package's own name | `pyproject.toml` |
| D13 | A schema is read by `from_source` (DDL) or `from_env` (an environment's build), never one constructor taking both | measured |
| D14 | The table-code registry is keyed by the **qualified** name (`catalog.tb_continent`): `catalog.tb_city` and `etl.tb_city` are two tables and must be two codes | this document |
| D15 | `SemanticUUIDGenerator.previous(table_code)` takes a code, not a table name: the sequence counters are keyed by code, and a name would need the registry a second time | this document |
| D16 | A self-referencing table needs a declared `hierarchy:` (`parent`, `roots`, `fan_out`), drawn breadth-first; without one it is refused, naming the column | owner, 2026-09-23 |
| D17 | A NOT NULL self-FK is refused: a root cannot point at itself before PostgreSQL gives it a key | owner, 2026-09-23 |
| D18 | A self-FK other than the declared parent is written `NULL` when nullable, and refused when NOT NULL | owner, 2026-09-23 |
| D19 | Read-back applies and learns a hierarchy level by level, one seed file per level; prep-seed writes it as one file | owner, 2026-09-23 |
| D20 | A hierarchy's `ltree` path is `pk_*`-labelled, set in read-back by an `UPDATE` per level joined on the UUID; prep-seed refuses `path:` | owner, 2026-09-23, measured |
| D21 | The command connects by confiture's own #152 URL precedence, imported from `confiture.cli.dsn` (off `platform`) and pinned by a contract test; a command that writes refuses the ambient `DATABASE_URL` alone | owner, 2026-09-23 |
| D22 | A project file, `semis.yaml`, names the schema source, the table codes (inline or a file of their own) and the scenarios directory; it loads into a public `Project` another tool can build in Python | owner, 2026-09-23 |
| D23 | The scenario files are the scenario-id registry: `decode-uuid` scans them, and an id two files use is reported | owner, 2026-09-23 |
| D24 | `apply` is one transaction, committed at the end; `--dry-run` on read-back runs it and rolls back | owner, 2026-09-23 |
| D25 | `semis.yaml` names the providers: a shipped library by name, a project's own as `module:attribute`, imported and never evaluated; an enabled library draws the columns its rules match by name and type, after a scenario's `providers:` and an enum, before the built-ins | owner, 2026-09-23 |
| D26 | A prep-seed run writes each catalog table into its staging twin, `<prep_seed_schema>.<table>`, each foreign key as `<fk>_id` | owner, 2026-09-23, measured |
| D27 | A run's DDL snapshot is `schema_pin.ddl`, so no directory reader takes it for a seed | owner, 2026-09-23, measured |
| D28 | Dropped: a refusal of resolver files level 3 would skip (#385), made needless when 1.23 found resolvers by routine name | owner, 2026-09-26 |
| D29 | A nullable foreign key to another table may be overridden `null`, per column: written `NULL`, its parent not required; a NOT NULL or self-referencing key overridden `null` is refused | owner, 2026-09-27, measured |
| D30 | Every date, time and timestamp is drawn from a fixed window (1970 to 2026), never up to *now*, so D10 holds whenever a run is made | measured |
| D31 | An installed package's `Library`, registered under the `fraiseql_semis.providers` entry point, is enabled by its short name as a shipped one is; installing enables nothing, only the named entry point is imported, and a name claimed twice or shipped is refused | owner, 2026-09-30 |

### On the UUID encoding's own arithmetic (D6)

The decoder is the definition:

```python
table_code  = int.from_bytes(b[0:4],  'big')    # 32 bits
scenario_id = int.from_bytes(b[4:6],  'big')    # 16 bits
version     = int.from_bytes(b[6:8],  'big')    # 16 bits
sequence    = int.from_bytes(b[8:16], 'big')    # 64 bits
```

A UUID whose digits read `5001` carries the scenario id `0x5001`. Registered in decimal,
`5001` encodes to `1389`, and the UUID no longer shows the number anyone typed. Codes and
ids are therefore written in hex — `scenario_id: 0x5001` — so the UUID reads back as
written:

```
01020304-5001-0001-0000-000000000042
^^^^^^^^ ^^^^ ^^^^ ^^^^^^^^^^^^^^^^
tb_lang  5001 v1   sequence 0x42 = 66
```
