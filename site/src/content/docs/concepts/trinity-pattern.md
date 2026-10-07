---
title: The Trinity Pattern
description: The three keys of a trinity-pattern table, which of them PostgreSQL fills, and which semis writes
---

A trinity-pattern table identifies each row three ways: an integer `pk_*` the database
fills, a UUID `id` that APIs and views expose, and a readable `identifier`. Foreign keys
are integers that point at a parent's `pk_*`.

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

## Who fills each column

| Column | Filled by | How |
|---|---|---|
| `pk_*`, an integer identity | PostgreSQL | semis cannot write it, and confiture refuses a seed that tries |
| `id`, a UUID | semis | the [semantic encoding](/concepts/semantic-uuid/): table code, scenario, version, sequence |
| `identifier`, a slug | semis | a word, the scenario id and the sequence: `europe-5001-3` |
| `fk_*`, a foreign key | semis, through one of [the two FK modes](/concepts/fk-modes/) | the parent's UUID in prep-seed, its `pk_*` in read-back |
| every other column | semis | a value drawn from the column's facts, or the scenario's override; a nullable one is `NULL` unless the scenario [names it](/reference/scenario-file/#which-columns-are-drawn) |

semis never assembles a `pk_*` column: confiture's `writable_columns` leaves it out, as it
leaves out a generated column, which PostgreSQL computes on apply. A seed that names one
anyway is refused by confiture's writer, and nothing is written:

```python
>>> write_copy_seed(path, "catalog.tb_continent", ["pk_continent", "id"], rows, model=model)
SeedError: PostgreSQL fills catalog.tb_continent.pk_continent (an identity, generated or
           serial column): leave it out of the seed
```

## How semis finds the keys

semis reads which column is the surrogate key and which the natural id from confiture's
naming hints, `pk_*` and `id`. A table showing neither gets no encoded UUID, and its
scenario supplies identity some other way.

A foreign key is a foreign key because the schema declares it with `REFERENCES`, never
because its name starts with `fk_`. Its parent table and column are the ones the
`REFERENCES` clause names, so a key named for its role, `fk_owner` pointing at
`tb_person`, is read correctly.

## Why the slug carries the scenario

Two scenarios seeded into one database share every unique constraint, and two runs with
the same `seed:` draw the same words. The `identifier` carries the scenario id for the
reason the UUID does: `europe-5001-3` and `europe-5002-3` are two rows of two scenarios,
and neither collides with the other.

## Next steps

- [The semantic UUID](/concepts/semantic-uuid/): what the four fields of `id` encode
- [The two FK modes](/concepts/fk-modes/): how a child learns its parent's `pk_*`
- [Getting started](/getting-started/): a trinity-pattern schema seeded end to end
