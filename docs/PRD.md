# PRD: fraiseql-semis

**Status**: 0.2.0
**Reads with**: [ARCHITECTURE.md](./ARCHITECTURE.md) (how it is built, and why),
[../README.md](../README.md) (how it is used)

---

## Summary

semis generates reproducible seed and test data for PostgreSQL schemas that follow the
trinity pattern — a UUID `id`, an integer `pk_*` the database fills, and a readable
`identifier`. Every row's UUID encodes the table, the scenario, a version and a
sequence, so a row found in a dump says where it came from. Every question about the
schema itself goes to [confiture](https://github.com/fraiseql/confiture): semis
supplies the values, the scenarios and the encoding.

---

## The problem

Seed data for a trinity-pattern schema is written by hand, and it shows:

1. **UUIDs are opaque.** A hard-coded `gen_random_uuid()` value carries nothing: not its
   table, not the fixture it belongs to, not its place in it.
2. **Runs are not reproducible.** Random UUIDs and unseeded fake values make two runs of
   the same fixture differ, so a seed file cannot be reviewed or pinned in CI.
3. **Foreign keys are integers nobody chose.** A child needs its parent's `pk_*`, which
   PostgreSQL assigns on insert; files that guess them break when the order changes.
4. **The schema moves under the fixtures.** A column gains `NOT NULL`, and a fixture
   written last month fails at apply time with a constraint violation naming no line.
5. **Generators guess the schema.** A foreign key read from the spelling of
   `fk_continent`, a `VARCHAR(50)` filled with sixty characters, a table order that
   disagrees with the real foreign keys.

## Who it is for

- **A developer** wants realistic data for a feature in one command, the same every
  time, and wants to know which fixture a row in their database came from.
- **A QA engineer** wants large datasets and specific edge cases, generated
  deterministically, and a known-good state to return to.
- **A schema owner** wants fixtures that are refused, not silently wrong, when the schema
  changes under them.

---

## The trinity pattern

```sql
CREATE SCHEMA catalog;

CREATE TABLE catalog.tb_continent (
    pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,  -- PostgreSQL's
    id UUID NOT NULL UNIQUE,                                       -- semis' encoding
    identifier TEXT NOT NULL UNIQUE,                               -- a readable slug
    name VARCHAR(50) NOT NULL
);

CREATE TABLE catalog.tb_country (
    pk_country BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_continent BIGINT NOT NULL REFERENCES catalog.tb_continent (pk_continent),
    iso_code CHAR(2) NOT NULL CHECK (iso_code ~ '^[A-Z]{2}$')
);
```

Foreign keys are integers, pointing at `pk_*`. APIs and views expose `id`. semis writes
`id` and `identifier`, never `pk_*`.

## The semantic UUID

```
┌────────────┬──────────┬───┬──────────┬────┬──────────────────────┐
│ table code │ scenario │ 8 │ version  │ 10 │ sequence             │
│  32 bits   │ 16 bits  │ 4 │ 12 bits  │ 2  │ 62 bits              │
└────────────┴──────────┴───┴──────────┴────┴──────────────────────┘

02030405-5001-8001-8000-000000000042
^^^^^^^^ ^^^^ ^^^^ ^^^^^^^^^^^^^^^^
tb_continent  5001  v1  sequence 0x42 = 66
```

It is an RFC 9562 version 8 UUID: the `8` that opens the third group is its version, and
the `8` that opens the fourth its variant, so a validator that accepts any standard UUID
accepts it. Table codes and scenario ids are written in hex, so the UUID shows the number
its author typed:

```python
>>> from uuid import UUID
>>> from fraiseql_semis import SemanticUUIDGenerator
>>> SemanticUUIDGenerator(scenario_id=0x5001).decode(UUID("02030405-5001-8001-8000-000000000042"))
UUIDFields(table_code=33752069, scenario_id=20481, version=1, sequence=66)
```

---

## Requirements

### Functional

1. **Semantic UUIDs.** Each row's natural id encodes its table code, scenario id,
   version and sequence; `semis decode-uuid` reads one back, naming the table and the
   scenario.
2. **Values that satisfy the column.** A generated value respects the column's type,
   declared length, enum labels, NOT NULL and UNIQUE, as confiture reports them. A CHECK
   is reported to the provider that draws the value, not evaluated.
3. **Real foreign keys.** A foreign key's parent comes from its `REFERENCES`, never from
   its name. Every parent is generated in the same run, or the scenario leaves a
   nullable key `NULL` explicitly. Children are spread round-robin over their parents.
4. **Two declared FK modes.** A scenario declares **prep-seed** (a child carries its
   parent's UUID, which the project's resolvers translate) or **read-back** (the parent is
   applied, its `pk_*` read back by UUID, then the children drawn). semis never guesses.
5. **Complete rows or none.** A row missing a NOT NULL column without a default is
   refused, naming the table, the column and the fact; a column a trigger fills is named
   in the scenario, per table and column.
6. **Scenarios as files.** A YAML scenario names its id, mode, seed, locale and tables,
   with per-column overrides (a value, a list with one per row, or `null`), per-column
   providers, and hierarchies for self-referencing tables.
7. **Pinned to a schema.** A scenario can pin a digest of the facts semis consumes, and a
   replay against a schema that has moved is refused, reporting what changed.
8. **Providers.** Faker, seeded per run, with locale support; shipped libraries for
   ISO codes and organisations; a project's own providers imported by name; and
   installable libraries enabled by a short name. No scenario string is evaluated.
9. **Validation.** `semis validate` checks a scenario against the schema and its pin
   without drawing a row; `semis validate-seeds` runs confiture's five prep-seed levels.

### Non-functional

1. **Deterministic.** The same scenario, seed and schema produce the same prep-seed
   bytes on any machine. Read-back output is reproducible except for the integers
   PostgreSQL assigns.
2. **Fast.** At least 10,000 rows per second generated, UUIDs and values included.
3. **At scale.** A 100,000-row scenario writes and applies in under two minutes, with
   memory that does not grow with the row count.
4. **Transactional.** An applied run is one transaction; a failure anywhere leaves
   nothing behind.
5. **Clear refusals.** Every error names the table, the column and the scenario where
   they apply, and what to do about it.
6. **Supported platforms.** Python 3.14, and PostgreSQL 16 or later; CI runs the
   integration suite on the oldest and the newest.

## Success metrics

| Metric | Target | Measured for 0.2.0 |
|---|---|---|
| Generation rate | 10,000 rows/s | 55,000 rows/s |
| 100,000 rows written and applied | under 2 minutes | 11 s |
| Referential integrity of generated data | 100% | every FK points at a row of the run, a row an `existing:` table holds, or is a declared `NULL` |

---

## Out of scope

- Reading `information_schema`, ordering tables, writing seed SQL: confiture's. semis'
  only SQL is the handful of parameterised statements in one module, for read-back, the
  re-apply check and its lock.
- Generating DDL or migrations.
- Evaluating CHECK expressions: PostgreSQL judges them at apply time.
- Owning a project's prep-seed resolvers: they are the project's, and confiture checks
  them.
- Holding a UUID→integer map of every row: the integers are PostgreSQL's.
