---
title: Schema Pins
description: How a scenario records a digest of the schema facts semis reads, and why a replay against a schema that has moved is refused
---

A scenario replayed against a schema that has moved writes a wrong seed, silently. A pin
turns that into a refusal: the scenario records a digest of the facts semis read, and a
run against a schema whose facts differ is refused, reporting what changed.

```yaml
schema_pin:
  source: ddl              # ddl | live — a pin is comparable only with its own kind
  digest: sha256:b02f4b364d06650c…
  confiture: "1.30.0"
  taken: 2026-09-23
  facts: minimal_seed.facts.json # the facts digested, kept beside the scenario
```

## Pin a scenario

Every run writes `schema_pin.yaml` beside its seeds, and beside it the facts it digested,
as JSON, in a file named after the scenario: `continents.facts.json`. In the project from
[Getting started](/getting-started/), write the seeds, copy the facts beside the scenario,
and paste the block into it:

```bash
semis seeds scenarios/continents.yaml -o db/seeds
cp db/seeds/continents.facts.json scenarios/
cat db/seeds/schema_pin.yaml >> scenarios/continents.yaml
```

semis never rewrites a scenario file: pinning is the author's act, and a scenario without
a pin runs and says that it is unpinned. The facts file is the pin's own record of what it
digested, keys sorted, so a re-pin reads as a diff in review. It must be the file the
digest was taken from: a stale or swapped copy is refused when the scenario loads. A pin
written by semis 0.1.0 kept a DDL snapshot, or nothing, in place of its facts: it is
refused with a hint to re-pin.

## A schema that moves

Narrow the country's name, from `VARCHAR(80)` to `VARCHAR(60)`:

```sql title="db/schema/010_catalog.sql" {16}
CREATE SCHEMA catalog;

CREATE TABLE catalog.tb_continent (
    pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL
);

CREATE TABLE catalog.tb_country (
    pk_country BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    fk_continent BIGINT NOT NULL REFERENCES catalog.tb_continent (pk_continent),
    name VARCHAR(60) NOT NULL,
    iso_code CHAR(2) NOT NULL
);
```

The scenario no longer matches its pin, and is refused before a row is drawn:

```bash
semis validate scenarios/continents.yaml
```

```text
scenario continents was pinned to sha256:be7c152ffa4c2ca5da8691e3549b1b5fa2d4a403482bb26ad0236016b41f65aa, and the schema now reads sha256:43fedb3a7ba5d5133827e0e28107827d3f501fb9a0125aa06e5954a153392ca6
What moved:
  catalog.tb_country.name: type_key varchar(80) → varchar(60)
  catalog.tb_country.name: raw_sql_type VARCHAR(80) → VARCHAR(60)
Hint: Review the changes, then re-pin the scenario from the schema_pin.yaml `semis seeds -o <dir>` or `semis apply` writes beside its seeds, or pass --no-pin for one run.
```

The command exits 1. The refusal compares the facts the pin kept with the schema's, so it
names each table and column that moved, and how, from any source. To accept the change,
re-pin: `semis seeds` writes the schema's new pin beside the seeds before it refuses the
run. Paste its `schema_pin.yaml` in place of the scenario's `schema_pin:` block, and copy
its `continents.facts.json` over the one beside the scenario.

`--no-pin` skips the check for one run, and says so:

```bash
semis validate scenarios/continents.yaml --no-pin
```

```text
scenario continents: the schema pin is not checked for this run (no_pin)
scenario continents is valid: 2 tables, 28 rows, prep-seed
```

There is no setting that turns the check off for good.

## What the digest holds

The digest covers the facts semis reads, and nothing else. For each table of the
scenario, in dependency order: its name, its surrogate key and natural id, and for each
column semis writes, its name, type, nullability, default, uniqueness, CHECKs, enum labels
and foreign-key target. For a prep-seed scenario, each staging twin's columns are digested
the same way, or its absence.

- **Adding an index does not move the pin.** A change that cannot alter a generated row
  does not invalidate a scenario.
- **Adding `NOT NULL` to a column semis writes moves the pin.** A change that can alter a
  generated row, or make one refusable under [the row contract](/concepts/row-contract/),
  does.

## DDL pins and live pins

The same schema reads differently from DDL and from a live database: PostgreSQL normalises
types and expressions. A digest is therefore comparable only with a digest of its own
kind, and a pin records which kind it is:

| `source:` | Taken when `semis.yaml` reads the schema from |
|---|---|
| `ddl` | `ddl:` or `env:` |
| `live` | `database:` |

A pin of the other kind is refused as incomparable, with the reason, rather than reported
as a change. Either kind keeps its facts, so either names what moved.

## Next steps

- [Determinism](/concepts/determinism/): the other half of a reproducible run
- [semis.yaml](/reference/semis-yaml/): the `schema:` source a pin is taken from
- [semis in CI](/guides/ci/): validating every scenario against its pin
