---
title: Multilingual Scenarios
description: Write each entity's translations in every declared locale, so the code that picks a label by locale and falls back is tested on data
---

A translated schema keeps each entity's labels in a translation table beside it, one row
per entity and locale, and the locales in a table its DDL fills. A scenario that
declares `locales:` writes those rows itself: for each row of a table with
`translations:`, one row per locale, its label drawn in that locale.

```sql title="db/schema/010_catalog.sql"
CREATE SCHEMA catalog;

CREATE TABLE catalog.tb_locale (
    pk_locale BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE catalog.tb_category (
    pk_category BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL
);

CREATE TABLE catalog.tl_category (
    fk_category BIGINT NOT NULL REFERENCES catalog.tb_category (pk_category),
    fk_locale BIGINT NOT NULL REFERENCES catalog.tb_locale (pk_locale),
    label VARCHAR(80) NOT NULL,
    UNIQUE (fk_category, fk_locale)
);
```

```yaml title="scenarios/shop.yaml"
scenario_id: 0x5008
name: shop
mode: read-back
seed: 42
locale: en_US                          # the Faker of every table written, entities included
locales: [fr-FR, en-GB]                # the locale table's codes, in the order rows are written

tables:
  - name: catalog.tb_category
    count: 20
    translations: catalog.tl_category  # 40 rows: each category in French and British English
```

## What is written

For each category, in order, one row per locale, in `locales:` order: 40 rows here, all
written to `catalog.tl_category`, which the run places after its entity as any child.
semis finds the translation table's columns by name:

- the **entity key** is its one foreign key to the entity;
- the **locale key** is `fk_locale`, and its foreign key names the locale table;
- the **label** is `label`, always drawn, even when it is nullable;
- the **locale's code** is the locale table's `code` column.

Every other column follows the rules of any table semis writes: a default is left to
PostgreSQL, a nullable column is `NULL` unless the run names it, a NOT NULL one is drawn,
and another foreign key resolves as any key does.

## A project's rule

When every translation table follows one rule, `semis.yaml` states it once, and a
scenario writes `translations: true`: the table the rule names, `{schema}.tl_{entity}`
unless the project names another, with the columns the rule names. A table a scenario
names under `translations:` overrides the template, and keeps the rule's columns. See
[semis.yaml](/reference/semis-yaml/#translations).

## The locale key

The locale table is read by its `code`, apart from any `existing:` entry for it: a
locale table also listed under `existing:` keeps its `existing:` rows for the keys that
point at it, and its codes for the translations.

- In **read-back**, each declared code's `pk_*` is read before the first table is drawn.
  A code with no row in the locale table, or two, is refused, and nothing is written.
- In **prep-seed**, each code's UUID comes from the scenario's pin: run `semis pin -d`
  against a database holding the locale rows, and every run takes them from the pin,
  reaching no database. A locale added to `locales:` needs a re-pin, and the refusal
  says so.

## Labels, in each locale

`locale:` is the Faker of every table the scenario writes, and `locales:` the locales
translations are written in; the two are independent. A code's Faker locale is the code
itself, the code with `-` read as `_` and its region upper-cased, or its bare language
when Faker has one. Faker has no `fr`, `it`, `pt` or `nl`, so a table keyed by bare
languages names them: `locales: [{code: fr, faker: fr_FR}, en]`.

Each locale draws from its own seeded Faker, so the same scenario and seed write the same
labels, adding `locales:` leaves every entity row as it was, and adding a locale or a
translated table moves no other locale's labels. A label under a unique key never repeats
across the locales: it is drawn again.

## A pseudo-locale

A pseudo-locale makes an untranslated or truncated label visible at a glance: declare
one more locale, by its code in the locale table, and each category gets a last
translation row whose label is the first locale's, every letter marked, 40% longer, and
bracketed.

```yaml
locales: [fr-FR, en-GB]
pseudo_locale: qps-ploc                # its row in tb_locale; not a Faker locale
```

Only the label is transformed; every other value is the first locale's row's, a unique
column drawn afresh. The transform has no randomness, and a snapshot test builds the label
it expects from the first locale's:

```python
from fraiseql_semis.translation import pseudo

assert pseudo("Category") == "[Ḉḁṭḕḡṓṙẏ ···]"
assert pseudo("Category", length=8) == "[Ḉḁṭḕḡṓ]"  # a varchar(8): the padding goes first
```

A label cut to its column keeps both brackets, so the cut shows. The pseudo row's UUID
takes the last slot, 255, so declaring a pseudo-locale moves no other UUID. Two labels
cut to one pseudo label under a unique key are refused, naming both: widen the column,
or drop `pseudo_locale:`.

## Gaps on purpose

An app that picks a label by locale and falls back (the tenant's locale, its language,
the default, the identifier) treats a missing translation as a silent degradation, and
seeds with every label present never exercise it. A scenario's `coverage:` gives a
locale a share of each translated table's rows (`semis.yaml`'s
[`coverage:`](/reference/semis-yaml/#coverage) is another key, for `semis coverage`):

```yaml
locales: [fr-FR, en-GB, de-DE]
pseudo_locale: qps-ploc
coverage: {en-GB: 0.8, de-DE: 0}       # de-DE: enabled, not translated yet
```

Of a table's `n` rows, exactly `⌊n × c⌋` get the locale's label: 16 of the 20 categories
in British English, none in German, every one in French and the pseudo-locale, which a
locale not named keeps. A small table at a low coverage can keep none: 3 rows at 0.3 keep
`⌊0.9⌋ = 0`. Every label is still drawn, then the rows left out are not written, so the
rows written keep the labels and UUIDs they have at full coverage, and each pseudo label
still has its source. `validate`, `seeds` and `apply` say how many are missing:

```text
catalog.tl_category: 20 rows × 4 locales, en-GB 4 missing, de-DE 20 missing
```

Which rows are left out is fixed by the scenario, not the seed: the same ones on every
run and under any `--seed`, each locale its own, and those kept at 0.8 are kept at 0.9.
A change of `count` may re-pick them. A fallback test asserts on them:

```sql
SELECT count(*) FILTER (WHERE gb.label IS NULL) AS fell_back,
       count(*) FILTER (WHERE coalesce(gb.label, fr.label) IS NULL) AS unlabelled
FROM catalog.tb_category AS c
LEFT JOIN catalog.tl_category AS gb ON gb.fk_category = c.pk_category
    AND gb.fk_locale = (SELECT pk_locale FROM catalog.tb_locale WHERE code = 'en-GB')
LEFT JOIN catalog.tl_category AS fr ON fr.fk_category = c.pk_category
    AND fr.fk_locale = (SELECT pk_locale FROM catalog.tb_locale WHERE code = 'fr-FR');
-- fell_back 4, unlabelled 0: four categories render in French on a British page
```

## confiture's coverage check

confiture proposes a live check
([fraiseql/confiture#657](https://github.com/fraiseql/confiture/issues/657)) that counts,
per translation table and required locale, the entity rows with no label. semis writes
the gaps and that check counts them, so the run's line uses the check's words: for a
required locale at coverage `c`, it would report the `n − ⌊n × c⌋` missing semis prints,
and a CI gate failing on missing labels fires on such a scenario, which a fixture for
that gate wants.

- `locale_fk` and `locale_table: schema.table (column)` mean the same in both projects'
  files. The translation table differs on purpose: semis maps an entity to its table
  with a template, `tl_{entity}`, and the check finds every one with a glob, `tl_*`.
- `locales:` is not the check's `required:`. semis' list says which languages the data
  exists in, and the check's which a release promises. A locale can be in both, with
  coverage below 1, and the check then reports it.
- semis writes at most one row per entity and locale, so a unique key over the pair, as
  the check expects, holds wherever the schema declares one.
- semis neither runs the check nor reads its configuration. Its own tests count the gaps
  in SQL, as above.

## Their UUIDs, and the reset

A translation table with a uuid `id` gets one per row, from its own table code: its
category's sequence times 256, plus its locale's place in `locales:` (1 for the first).
Appending a locale changes none of them; inserting or reordering one renumbers those
after it, so append. A translation table without one, keyed by `(fk_category,
fk_locale)`, is written without one, and `semis reset` deletes its rows through their
category.

## Next steps

- [The scenario file](/reference/scenario-file/#translation-table-keys): every key, and
  what is refused
- [The two FK modes](/concepts/fk-modes/): how a key finds its parent in each mode
- [Schema pins](/concepts/schema-pins/#rows-a-prep-seed-scenario-points-at): the locale
  rows a prep-seed pin records
- [Determinism](/concepts/determinism/): what a seed and a scenario fix, gaps included
