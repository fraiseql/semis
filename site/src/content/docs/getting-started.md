---
title: Getting Started
description: From an empty directory to seeds applied to PostgreSQL, through semis.yaml, one scenario, semis seeds and semis apply
---

This page builds a project of two tables, continents and the countries on them, writes
their seed data, and applies it to a database. It needs
[uv](https://docs.astral.sh/uv/), Python 3.14, and a server running PostgreSQL 16 or later
that you can create a database on, with `createdb` and `psql` on the path.

## Create the project

```bash
mkdir continents && cd continents
uv init --bare
uv add fraiseql-semis
source .venv/bin/activate
```

`fraiseql-semis` brings `fraiseql-confiture` with it, and installs the `semis` command.

## Write the schema

The schema follows the [trinity pattern](/concepts/trinity-pattern/): PostgreSQL fills
the integer `pk_*`, semis writes the UUID `id` and the readable `identifier`, and a
foreign key points at its parent's `pk_*`.

```sql title="db/schema/010_catalog.sql"
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
    name VARCHAR(80) NOT NULL,
    iso_code CHAR(2) NOT NULL
);
```

A child row needs its parent's `pk_*`, which PostgreSQL assigns on insert, so semis
cannot write it. The first scenario on this page runs in **prep-seed** mode: it writes
each table into a staging twin, the same table in a `prep_seed` schema, where every
foreign key is the parent's UUID, named `<fk>_id`. A project's resolver functions then
promote the staged rows into the catalog. [The two FK modes](/concepts/fk-modes/) explains
both modes and when to choose each.

```sql title="db/schema/020_prep_seed.sql"
CREATE SCHEMA prep_seed;

CREATE TABLE prep_seed.tb_continent (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    name VARCHAR(50)
);

CREATE TABLE prep_seed.tb_country (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    fk_continent_id UUID,
    name VARCHAR(80),
    iso_code CHAR(2)
);
```

## Create the database

```bash
createdb continents_dev
psql -d continents_dev -f db/schema/010_catalog.sql -f db/schema/020_prep_seed.sql
```

## Describe the project

`semis.yaml` names where the schema is read from, the code each table's UUIDs carry, and
where the scenarios are. semis reads the DDL through confiture: the tables, their
columns, and their foreign keys as `REFERENCES` declares them.

```yaml title="semis.yaml"
schema:
  ddl: db/schema/
scenarios: scenarios/
table_codes:
  catalog.tb_continent: 0x02030405
  catalog.tb_country: 0x03040506
providers: [i18n]
```

A table code is written in hex so that it reads back in the UUID: every continent's `id`
starts `02030405-`. `providers: [i18n]` enables the shipped library of ISO codes and
country names. [semis.yaml](/reference/semis-yaml/) lists every key.

## Write a scenario

A scenario is the run, as a file: its id, its mode, its seed, and the rows of each table.

```yaml title="scenarios/continents.yaml"
scenario_id: 0x5001
name: continents
description: Seven continents and the countries on them
mode: prep-seed
locale: en_US
seed: 42

tables:
  - name: catalog.tb_continent
    count: 7
    overrides:
      name: [Africa, Antarctica, Asia, Europe, North America, Oceania, South America]
  - name: catalog.tb_country
    count: 21
    providers: {name: i18n.country_name}
```

`overrides:` gives the continents their names, one per row. `providers:` draws each
country's name from the `i18n` library. Every other column is drawn from its facts:
`iso_code` is a `CHAR(2)` and receives two letters. The countries are spread over the
continents round-robin.

## Check it

`semis validate` reads the scenario against the schema without drawing a row: each table
exists and has a code, each foreign key's parent is in the run, and each staging twin is
there.

```bash
semis validate scenarios/continents.yaml
```

```text
scenario continents is unpinned: its schema is not checked
scenario continents is valid: 2 tables, 28 rows, prep-seed
```

## Write the seeds

```bash
semis seeds scenarios/continents.yaml -o db/seeds
```

```text
scenario continents is unpinned: its schema is not checked
wrote 001_prep_seed.tb_continent.sql  7 rows
wrote 002_prep_seed.tb_country.sql    21 rows
```

`semis seeds` reaches no database. Each file is an `INSERT` into the staging twin:

```bash
head -n 4 db/seeds/002_prep_seed.tb_country.sql
```

```sql
INSERT INTO prep_seed.tb_country (id, identifier, fk_continent_id, name, iso_code) VALUES
    ('03040506-5001-8001-8000-000000000001', 'chair-5001-1', '02030405-5001-8001-8000-000000000001', 'Saint Pierre and Miquelon', 'CA'),
    ('03040506-5001-8001-8000-000000000002', 'require-5001-2', '02030405-5001-8001-8000-000000000002', 'Saint Vincent and the Grenadines', 'ST'),
    ('03040506-5001-8001-8000-000000000003', 'begin-5001-3', '02030405-5001-8001-8000-000000000003', 'Nauru', 'NO'),
```

Each country's `fk_continent_id` is its continent's UUID. Run the command again and the
files are byte for byte the same: the same scenario, seed and schema write the same
bytes on any machine. A seed file can be reviewed in a pull request and diffed in CI.

## Apply them

```bash
semis apply scenarios/continents.yaml -o db/seeds --database-url postgresql:///continents_dev
```

```text
scenario continents is unpinned: its schema is not checked
applied 001_prep_seed.tb_continent.sql  7 rows
applied 002_prep_seed.tb_country.sql    21 rows
committed
```

`semis apply` writes the seeds and applies them in one transaction, committed at the end.
The rows are in the staging twins:

```bash
psql -d continents_dev -c "SELECT id, identifier, fk_continent_id, iso_code FROM prep_seed.tb_country LIMIT 3"
```

```text
                  id                  |   identifier   |           fk_continent_id            | iso_code
--------------------------------------+----------------+--------------------------------------+----------
 03040506-5001-8001-8000-000000000001 | chair-5001-1   | 02030405-5001-8001-8000-000000000001 | CA
 03040506-5001-8001-8000-000000000002 | require-5001-2 | 02030405-5001-8001-8000-000000000002 | ST
 03040506-5001-8001-8000-000000000003 | begin-5001-3   | 02030405-5001-8001-8000-000000000003 | NO
(3 rows)
```

From here, a project's `fn_resolve_*` functions translate each UUID into its parent's
`pk_*` as they promote the rows into `catalog`. They are the project's, and
[Validating seeds](/guides/validating-seeds/) shows how confiture checks them against
these seeds.

## Decode a UUID

Every UUID semis writes says where it came from:

```bash
semis decode-uuid 03040506-5001-8001-8000-000000000003
```

```text
table_code   0x03040506   catalog.tb_country
scenario_id  0x5001       continents
version      1
sequence     3
```

[The semantic UUID](/concepts/semantic-uuid/) describes the layout.

## Pin the scenario to its schema

`semis pin` takes a digest of the facts semis reads, and writes it, with the facts
themselves, to `continents.pin.json` beside the scenario:

```bash
semis pin scenarios/continents.yaml
```

```text
wrote scenarios/continents.pin.json: scenario continents is pinned (ddl sha256:be7c152ffa4c2ca5da8691e3549b1b5fa2d4a403482bb26ad0236016b41f65aa)
```

Commit it beside the scenario. A run now checks the schema against it:

```bash
semis validate scenarios/continents.yaml
```

```text
scenario continents matches its schema pin (ddl sha256:be7c152ffa4c2ca5da8691e3549b1b5fa2d4a403482bb26ad0236016b41f65aa)
scenario continents is valid: 2 tables, 28 rows, prep-seed
```

From now on, a run against a schema whose facts have moved is refused, naming what
changed. [Schema pins](/concepts/schema-pins/) describes what the digest holds.

## Without resolvers: read-back

A project with no resolvers declares **read-back** instead: semis applies each parent
table, reads back the `pk_*` PostgreSQL gave each row, joined on the UUID it wrote, and
then draws the children. The rows land in the catalog tables themselves.

`semis init-scenario` writes a new scenario listing the project's tables, with the next
free scenario id:

```bash
semis init-scenario countries --mode read-back
```

```text
wrote scenarios/countries.yaml: scenario countries, scenario_id 0x5002, read-back
```

Edit it to name the countries:

```yaml title="scenarios/countries.yaml"
scenario_id: 0x5002
name: countries
description: Countries applied to the catalog, their continents' keys read back
mode: read-back
locale: en_US
seed: 42

tables:
  - name: catalog.tb_continent
    count: 3
  - name: catalog.tb_country
    count: 6
    providers: {name: i18n.country_name}
```

A read-back scenario needs a database to generate, so it runs with `semis apply`:

```bash
semis apply scenarios/countries.yaml -o db/seeds/countries --database-url postgresql:///continents_dev
```

```text
scenario countries is unpinned: its schema is not checked
applied 001_catalog.tb_continent.sql  3 rows
applied 002_catalog.tb_country.sql    6 rows
committed
```

Each country's `fk_continent` is now the integer PostgreSQL gave its continent:

```bash
psql -d continents_dev -c "SELECT pk_country, id, fk_continent FROM catalog.tb_country ORDER BY pk_country"
```

```text
 pk_country |                  id                  | fk_continent
------------+--------------------------------------+--------------
          1 | 03040506-5002-8001-8000-000000000001 |            1
          2 | 03040506-5002-8001-8000-000000000002 |            2
          3 | 03040506-5002-8001-8000-000000000003 |            3
          4 | 03040506-5002-8001-8000-000000000004 |            1
          5 | 03040506-5002-8001-8000-000000000005 |            2
          6 | 03040506-5002-8001-8000-000000000006 |            3
(6 rows)
```

The UUIDs carry the scenario id `5002`, and the identifiers carry it too, so the two
scenarios share one database without colliding.

## Next steps

- [The two FK modes](/concepts/fk-modes/): prep-seed or read-back, and why semis never chooses
- [Writing scenarios](/guides/scenarios/): overrides, providers, hierarchies and `trusts_trigger:`
- [Validating seeds](/guides/validating-seeds/): confiture's five levels on these seeds
- [The semis command](/reference/cli/): every command and option
