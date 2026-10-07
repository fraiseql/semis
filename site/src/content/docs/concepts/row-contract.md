---
title: The Row Contract
description: Why semis refuses a row that is missing a NOT NULL column, and how trusts_trigger names the columns a trigger fills
---

A row is complete before it is written. confiture's seed writers do not check that a row
carries every NOT NULL column, and they do not validate a value against its column. Both
are deliberate: a trigger may fill a column, and the schema model cannot know what a
trigger writes. So semis checks, and a row that fails is refused, naming the table, the
column and the fact it failed. Without the check, the gap surfaces at apply time as a
constraint violation against a file, with no line and no generator in sight.

## The rules

For every column semis writes, a row is complete when:

| The column's fact | The rule |
|---|---|
| NOT NULL, no default | the row carries the column, and its value is not `NULL` |
| NOT NULL, with a default | the column may be left out: PostgreSQL fills it |
| nullable | `NULL` is valid: semis writes it unless the scenario [names the column](/reference/scenario-file/#which-columns-are-drawn) |
| an enum type | the value is one of its labels |
| a declared length, `varchar(n)` or `char(n)` | the value fits |
| UNIQUE | the value has not been used for that column earlier in the run |
| a foreign key | the value came from the parent rows of the run, never from a provider |
| a CHECK | reported to the provider that draws the value, not evaluated |

A CHECK is not evaluated because semis does not evaluate SQL expressions. PostgreSQL
judges it at apply time, and a [schema pin](/concepts/schema-pins/) records it, so a
changed CHECK moves the pin.

## A refused row

This contact table has a NOT NULL `JSONB` column with no default, and its prep-seed twin:

```sql title="db/schema.sql"
CREATE SCHEMA crm;

CREATE TABLE crm.tb_contact (
    pk_contact BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    email VARCHAR(120) NOT NULL,
    preferences JSONB NOT NULL
);

CREATE SCHEMA prep_seed;

CREATE TABLE prep_seed.tb_contact (
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL,
    email VARCHAR(120),
    preferences JSONB
);
```

```yaml title="semis.yaml"
schema:
  ddl: db/schema.sql
table_codes:
  crm.tb_contact: 0x0c010101
```

```yaml title="scenarios/contacts.yaml"
scenario_id: 0x6001
name: contacts
mode: prep-seed
seed: 42

tables:
  - name: crm.tb_contact
    count: 5
```

No provider draws a `JSONB` value, so the column's value is `NULL`, and the first row is
refused before anything is written:

```bash
semis seeds scenarios/contacts.yaml --dry-run
```

```text
scenario contacts: crm.tb_contact.preferences is NOT NULL, but its value is None
Hint: Give the column a value that satisfies it, or a default in the schema; if a trigger fills it, name it under trusts_trigger for this table.
```

The command exits 1. The hint names the three ways out.

## Give the column a value

An override gives every row the same value, or a list gives one per row:

```yaml title="scenarios/contacts.yaml"
scenario_id: 0x6001
name: contacts
mode: prep-seed
seed: 42

tables:
  - name: crm.tb_contact
    count: 5
    overrides:
      preferences: '{"newsletter": false}'
```

```bash
semis seeds scenarios/contacts.yaml --dry-run --verbose
```

```text
scenario contacts is unpinned: its schema is not checked
would write 001_prep_seed.tb_contact.sql  5 rows  insert: id, identifier, email, preferences
```

## Trust a trigger

When a trigger really does fill the column, name it under `trusts_trigger:`. semis leaves
the column out of the seed and does not refuse the row:

```yaml title="scenarios/contacts.yaml"
scenario_id: 0x6001
name: contacts
mode: prep-seed
seed: 42

tables:
  - name: crm.tb_contact
    count: 5
    trusts_trigger: [preferences]     # a trigger fills it
```

```bash
semis seeds scenarios/contacts.yaml --dry-run --verbose
```

```text
scenario contacts is unpinned: its schema is not checked
would write 001_prep_seed.tb_contact.sql  5 rows  insert: id, identifier, email
```

`trusts_trigger:` is per table and per column. There is no scenario-wide switch to trust
every column, because that is the same as not having the contract. A column cannot be
both overridden and trusted.

## What confiture's writers accept

The contract is semis' because confiture's writers accept what it refuses. A NOT NULL
column left out, and values the column cannot hold, are written as given:

```text
# NOT NULL, no default, simply left out of `columns`  → accepted
write_copy_seed(p, "catalog.tb_country", ["id", "fk_continent"], rows, model=m)   # ok

# values the column cannot hold                       → accepted, written verbatim
"not-a-uuid"  into uuid          → not-a-uuid
3.7           into integer       → 3.7
"toolong"     into varchar(3)    → toolong
None          into a NOT NULL    → \N
```

## Next steps

- [Writing scenarios](/guides/scenarios/): overrides, providers and `trusts_trigger:`
- [Shipped provider libraries](/reference/providers/): what draws which columns
- [Exit codes and errors](/reference/exit-codes/): `RowContractError` and the rest
