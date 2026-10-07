---
title: The Two FK Modes
description: Prep-seed and read-back, the two ways a child row learns its parent's key, and how to choose between them
---

A child row needs its parent's integer `pk_*`, and PostgreSQL assigns that integer on
insert, so semis cannot choose one. There are two ways to close that gap. A scenario
declares which one it uses with `mode:`, and semis refuses to guess: a scenario that picked
its mode by probing the database it met would mean two different things in CI and on a
laptop.

| | **prep-seed** | **read-back** |
|---|---|---|
| Rows are written into | the staging twin, `prep_seed.<table>` | the table itself |
| A child's FK carries | the parent's **UUID**, as `<fk>_id` | the parent's **integer**, learned after apply |
| Database needed to generate | no | yes |
| Translation done by | the project's `fn_resolve_*` functions | a join on the UUID semis wrote |
| Output is | byte-reproducible | reproducible except the FK integers, which are PostgreSQL's |
| Default writer | `INSERT` | `COPY` |

## Choose a mode

Choose **prep-seed** when the project has resolver functions that promote staged rows
into the catalog, translating each UUID into its parent's `pk_*`. Its seed files are
byte-reproducible, so they can be committed, reviewed and diffed in CI, and generating
them needs no database. confiture checks the seeds and the resolvers together at five
levels: see [Validating seeds](/guides/validating-seeds/).

Choose **read-back** when the project has no resolvers. semis applies each parent table,
reads back the keys PostgreSQL gave its rows, and then draws the children, so the rows
land in the catalog tables directly. Generating needs a database, and the seed files are
an artifact of the run rather than a document to review.

## Prep-seed

A prep-seed scenario names the catalog tables: their foreign keys, constraints and pin are
what semis reads. Each is written into its **staging twin**, the same table name in the
project's staging schema (`prep_seed` unless `semis.yaml` names another), where every
foreign key is a UUID named `<fk>_id`:

```yaml
# scenarios/minimal_seed.yaml
scenario_id: 0x5001
name: minimal_seed
description: Seven continents and the countries on them
mode: prep-seed          # required: prep-seed | read-back
locale: en_US
seed: 42

tables:
  - name: catalog.tb_continent
    count: 7
    overrides:
      name: [Africa, Antarctica, Asia, Europe, North America, Oceania, South America]
  - name: catalog.tb_country
    count: 50
    trusts_trigger: [created_by]      # a trigger fills it; do not refuse the row
```

A column is renamed `<fk>_id` because the schema says it is a foreign key, never because
of its name. A table without its twin is refused before a row is drawn, and a twin lacking
a column the rows carry is refused before its file is written.

This is the layout confiture's prep-seed validation checks. Its level 1 refuses a seed
into the catalog schema, and warns of a foreign key without its suffix:

```text
INSERT INTO catalog.tb_country (…)                    → ERROR    Seed INSERT targets catalog schema but should target prep_seed
INSERT INTO prep_seed.tb_country (…, fk_continent, …) → WARNING  FK column 'fk_continent' missing _id suffix (should be 'fk_continent_id')
```

## Read-back

For each table in dependency order, parents first, a read-back run draws the table's rows,
writes them, applies the file, and learns the table's keys before the next table is
drawn. The keys are learned by one query per table, joined on the UUIDs semis has written,
never `LIMIT 1` and never an integer semis guessed. Children are spread over the parents
found, round-robin.

This is the only SQL semis writes: taking the scenario's lock, so two applies of it never
overlap, asking whether it is
[already applied](/concepts/determinism/#a-scenario-applies-once), reading the keys of
[existing rows](#parents-already-in-the-database), learning keys, and setting a
[hierarchy's](/guides/scenarios/#hierarchies) paths from them.

```sql
SELECT pg_advisory_xact_lock(%s, %s)

SELECT EXISTS (SELECT FROM <schema>.<table> WHERE <natural_id> BETWEEN %s AND %s)

SELECT <surrogate_pk> FROM <schema>.<table> ORDER BY <surrogate_pk>

SELECT <identifier>, <surrogate_pk> FROM <schema>.<table> WHERE <identifier> = ANY(%s)

SELECT <surrogate_pk>, <natural_id> FROM <schema>.<table> WHERE <natural_id> = ANY(%s)

UPDATE <schema>.<table> AS t SET <path> = v.path::ltree
  FROM unnest(%s::uuid[], %s::text[]) AS v(id, path) WHERE t.<natural_id> = v.id
```

Every identifier in them comes from the schema's facts, and the UUIDs, identifiers and
paths are parameters. `semis apply` runs the whole scenario in one transaction, so a failure
anywhere leaves nothing behind.

### Parents already in the database

Reference rows the schema's own DDL inserts, a table of categories or of countries, are
not generated again. A read-back scenario lists their table under `existing:`, and a
foreign key to it points at the rows it holds, round-robin, as at a parent the run
generated:

```yaml
existing:
  - name: shop.tb_category            # every row, round-robin, in pk_* order
  - name: shop.tb_country
    where: {identifier: [fr, de, es]} # these rows, in this order
```

Their keys are read before the first table is drawn, by the second and third queries
above. An existing table needs no table code, and the scenario's pin covers its keys. The
assignment depends on the rows the database holds: the same rows and the same seed give
the same assignment. A table with no rows, or a `where:` value no row matches, is refused,
naming the table. Prep-seed refuses `existing:`: a child carries its parent's UUID, and
rows semis did not write have none it can know.

## In either mode

A foreign key points at a row of the run, so its parent table must be generated in the
same run, or, in read-back, listed under `existing:`. A parent the run lacks is refused before a row is drawn, by `semis validate` as
by `semis seeds`. A **nullable** key may instead be overridden `null`: it is written
`NULL`, and its parent is not required.

```yaml
  - name: inventory.tb_item
    count: 50
    overrides: {fk_account: null, fk_order: null}   # no account, no order
```

That is the scenario's word, never semis' guess: without the override the parent is still
required. A NOT NULL key, or a table's key to itself, overridden `null` is refused.

The writer follows the mode: prep-seed writes `INSERT`, read-back writes `COPY`.
`--format copy` or `--format insert` overrides either, and confiture's level 1 reads both.

## Next steps

- [Getting started](/getting-started/): one scenario in each mode, applied
- [Validating seeds](/guides/validating-seeds/): confiture's five levels on prep-seed seeds
- [Determinism](/concepts/determinism/): what each mode reproduces
- [Writing scenarios](/guides/scenarios/): overrides, providers and hierarchies
