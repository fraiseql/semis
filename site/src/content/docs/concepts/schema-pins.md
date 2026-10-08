---
title: Schema Pins
description: How a scenario records a digest of the schema facts semis reads, and why a replay against a schema that has moved is refused
---

A scenario replayed against a schema that has moved writes a wrong seed, silently. A pin
turns that into a refusal: `semis pin` records a digest of the facts semis reads, beside
the scenario, and a run against a schema whose facts differ is refused, reporting what
changed.

```json
// scenarios/minimal_seed.pin.json
{
  "confiture": "1.30.0",
  "digest": "sha256:b02f4b364d06650c…",
  "facts": [{"table": "catalog.tb_continent", "surrogate_pk": "pk_continent", …}, …],
  "source": "ddl",
  "taken": "2026-09-23"
}
```

## Pin a scenario

In the project from [Getting started](/getting-started/), take the scenario's pin:

```bash
semis pin scenarios/continents.yaml
```

It writes `continents.pin.json` beside the scenario: the digest, how the schema was read,
the confiture that read it, the date, and the facts the digest was taken from, keys
sorted, so a re-pin reads as a diff in review. Commit it with the scenario.

semis never rewrites a scenario file, and writes its pin only when asked: pinning is the
author's act, and a scenario without a pin runs and says that it is unpinned. When the
file already keeps the schema's pin, `semis pin` says it is unchanged and writes nothing,
so its date does not churn. Loading the scenario reads the file back and refuses one whose
digest is not its facts', so a stale or swapped copy is never trusted; `semis pin`
replaces such a file. A `schema_pin:` block in the scenario, or a `continents.facts.json`
beside it, as semis 0.2.0 kept a pin, is refused with the hint to run `semis pin`.

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
Hint: Review the changes, then accept them with semis pin on the scenario, or pass --no-pin for one run.
```

The command exits 1. The refusal compares the facts the pin kept with the schema's, so it
names each table and column that moved, and how, from any source. To accept the change,
pin the scenario again. It names what moved, and rewrites the file:

```bash
semis pin scenarios/continents.yaml
```

```text
What moved:
  catalog.tb_country.name: type_key varchar(80) → varchar(60)
  catalog.tb_country.name: raw_sql_type VARCHAR(80) → VARCHAR(60)
wrote scenarios/continents.pin.json: scenario continents is pinned (ddl sha256:43fedb3a7ba5d5133827e0e28107827d3f501fb9a0125aa06e5954a153392ca6)
```

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
